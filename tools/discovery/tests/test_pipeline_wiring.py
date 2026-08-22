"""Checks that the pipeline actually calls its own machinery.

Unit tests over the helpers pass whether or not anything invokes them, so
these assert the wiring itself.
"""

import pytest
from discovery.model import Arm, Outcome, RunResult, Tier
from discovery.pipeline import Pipeline, PipelineConfig
from discovery.runner import EnvSpec
from discovery.validate import CleanRoomCheck

INVENTORY = [
    "tests/t_test.py::test_a",
    "tests/t_test.py::test_b",
    "other/x_test.py::test_c",
]


class FakeRunner:
    def __init__(self):
        self.sandbox = object()
        self.project_dir = "/proj"
        self.run_root = "/run"
        self.collect_calls = []
        self.run_specs = []

    def collect(self, env, limits=None, extra_args=()):
        self.collect_calls.append(tuple(extra_args))
        return list(INVENTORY)

    def run(self, spec):
        self.run_specs.append(spec)
        result = RunResult(spec.arm, spec.tier, 0, 0.1)
        for nodeid in spec.nodeids:
            from discovery.model import CaseOutcome

            result.outcomes[nodeid] = CaseOutcome(nodeid, Outcome.PASSED)
        return result


class FakeValidator:
    def __init__(self, clean=True):
        self.clean = clean
        self.preflight_calls = 0

    def preflight(self, env):
        self.preflight_calls += 1
        return CleanRoomCheck(self.clean, "clean" if self.clean else "plugin reachable")

    def validate(self, examples, env, limits=None):
        return {}


def build(clean=True, **cfg):
    runner = FakeRunner()
    pipeline = Pipeline(
        runner,
        crosshair_env=EnvSpec("ch", ["python"], has_crosshair=True),
        validation_env=EnvSpec("v", ["python"], has_crosshair=False),
        config=PipelineConfig(baseline_seeds=(1,), run_telemetry_tier=False, **cfg),
    )
    pipeline.validator = FakeValidator(clean)
    return pipeline, runner


def test_the_clean_room_is_checked_before_anything_runs():
    pipeline, runner = build()
    pipeline.run()
    assert pipeline.validator.preflight_calls == 1


def test_a_dirty_clean_room_aborts_the_run():
    pipeline, runner = build(clean=False)
    with pytest.raises(RuntimeError, match="not a clean room"):
        pipeline.run()
    assert runner.run_specs == [], "no test should execute against a dirty clean room"


def test_extra_pytest_args_reach_collection():
    pipeline, runner = build(pytest_args=("-m", "property"))
    pipeline.run()
    assert runner.collect_calls == [("-m", "property")]


def test_a_file_selector_is_expanded_against_the_inventory():
    pipeline, _ = build()
    report = pipeline.run(["tests/t_test.py"])
    assert report.collected == INVENTORY[:2]


def test_no_selector_runs_the_whole_inventory():
    pipeline, _ = build()
    assert pipeline.run().collected == INVENTORY
