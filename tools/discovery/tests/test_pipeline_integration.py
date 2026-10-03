"""End-to-end check of stages 1-3 against a fixture project.

Requires a real CrossHair install for the solver arm and a second interpreter
with the plugin absent for the clean-room replay. Point
``DISCOVERY_VALIDATION_PYTHON`` at the latter to enable the test.
"""

import os
import sys

import pytest
from discovery.model import Verdict
from discovery.pipeline import Pipeline, PipelineConfig
from discovery.runner import EnvSpec, Runner
from discovery.sandbox import Limits, LocalSandbox

FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "demoproj"
)
VALIDATION_PYTHON = os.environ.get("DISCOVERY_VALIDATION_PYTHON")

pytestmark = pytest.mark.skipif(
    not VALIDATION_PYTHON,
    reason="set DISCOVERY_VALIDATION_PYTHON to an interpreter without the plugin",
)


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    run_root = str(tmp_path_factory.mktemp("run"))
    runner = Runner(
        LocalSandbox(i_understand_this_is_unsafe=True),
        project_dir=FIXTURE,
        run_root=run_root,
    )
    pipeline = Pipeline(
        runner,
        crosshair_env=EnvSpec("crosshair", [sys.executable], has_crosshair=True),
        validation_env=EnvSpec("validation", [VALIDATION_PYTHON], has_crosshair=False),
        config=PipelineConfig(
            baseline_max_examples=300,
            crosshair_max_examples=100,
            crosshair_limits=Limits(wall_seconds=600),
        ),
    )
    return pipeline.run()


def verdict_for(report, name):
    for item in report.classifications:
        if item.nodeid.endswith(f"::{name}"):
            return item
    raise AssertionError(f"{name} was never classified")


def test_all_hypothesis_tests_are_collected(report):
    assert len(report.collected) == 5


def test_a_solver_only_bug_becomes_a_validated_trophy(report):
    item = verdict_for(report, "test_checksum_matches_reference")
    assert item.verdict is Verdict.TROPHY_CANDIDATE
    assert item.exception_type == "AssertionError"
    assert "data=" in (item.falsifying_example or "")


def test_a_bug_both_arms_find_is_not_a_trophy(report):
    assert (
        verdict_for(report, "test_first_word_returns_str").verdict
        is Verdict.SHARED_FIND
    )


def test_a_correct_property_yields_no_signal(report):
    assert verdict_for(report, "test_clamp_within_bounds").verdict is Verdict.NO_SIGNAL


def test_an_explicit_settings_decorator_does_not_escape_the_backend(report):
    """``@settings(backend="hypothesis")`` must not survive into the solver arm."""
    assert report.crosshair_run is not None
    forced = [n for n in report.collected if n.endswith("test_clamp_within_bounds")]
    assert forced, "the pinned-backend test should still be run"
    assert forced[0] in report.crosshair_run.outcomes


def test_nothing_is_ever_reported_upstream(report):
    for item in report.trophies:
        assert item.needs_human_review
