"""Checks that a run's state survives the process that produced it.

Container restarts have killed multi-hour runs repeatedly, so these assert the
resume path itself: what is skipped, what is run again, and what is given up on.
"""

import pytest
from discovery.cli import _Journal, _run_per_test
from discovery.model import (
    Arm,
    CaseOutcome,
    Classification,
    CompletionStats,
    Outcome,
    RunResult,
    SearchProgress,
    Tier,
    Verdict,
)
from discovery.pipeline import PipelineReport
from discovery.store import Store

VERSIONS = {"crosshair": "0.0.111", "plugin": "0.0.30", "python": "3.12.3"}


def verdict(nodeid, kind=Verdict.NO_SIGNAL):
    """A verdict shaped like the classifier's: evidence attached, not alongside."""
    stats = CompletionStats()
    stats.crosshair_cases = 3
    stats.unsupported = {"POSSESSIVE_REPEAT": 3}
    return Classification(
        nodeid=nodeid,
        verdict=kind,
        baseline=Outcome.PASSED,
        crosshair=Outcome.PASSED,
        completion=stats,
        search=SearchProgress(5, 1, 12),
    )


class FakePipeline:
    """Records which tests actually ran, and can be told to die on one."""

    ran = []
    die_on = None

    def __init__(self, root):
        self.root = root

    def run(self, nodeids):
        nodeid = nodeids[0]
        if nodeid == FakePipeline.die_on:
            raise KeyboardInterrupt("worker lost")
        FakePipeline.ran.append(nodeid)
        report = PipelineReport(project_dir="/proj")
        report.collected = [nodeid]
        report.eligible = [nodeid]
        report.classifications = [verdict(nodeid)]
        crosshair = RunResult(Arm.CROSSHAIR, Tier.A_VERDICT, 0, 1.0)
        crosshair.outcomes[nodeid] = CaseOutcome(nodeid, Outcome.PASSED)
        crosshair.search[nodeid] = SearchProgress(5, 1, 12)
        report.crosshair_run = crosshair
        telemetry = RunResult(Arm.CROSSHAIR, Tier.B_TELEMETRY, 0, 1.0)
        stats = CompletionStats()
        stats.crosshair_cases = 3
        stats.unsupported = {"POSSESSIVE_REPEAT": 3}
        telemetry.telemetry[nodeid] = stats
        report.telemetry_run = telemetry
        return report


@pytest.fixture
def store(tmp_path):
    with Store(str(tmp_path / "s.db")) as opened:
        yield opened


@pytest.fixture(autouse=True)
def fresh():
    FakePipeline.ran = []
    FakePipeline.die_on = None


def journal(store, refresh=False, commit="c0ffee", run_id="run1"):
    return _Journal(store, run_id, "/proj", commit, VERSIONS, refresh)


TESTS = ["a::t", "b::t", "c::t"]


def test_every_test_runs_once_on_a_fresh_run(store, tmp_path):
    report = _run_per_test(FakePipeline, str(tmp_path), TESTS, journal(store))
    assert FakePipeline.ran == TESTS
    assert [c.nodeid for c in report.classifications] == TESTS
    assert store.progress("run1") == {"done": 3}


def test_a_killed_run_resumes_where_it_stopped(store, tmp_path):
    FakePipeline.die_on = "b::t"
    with pytest.raises(KeyboardInterrupt):
        _run_per_test(FakePipeline, str(tmp_path), TESTS, journal(store))
    assert FakePipeline.ran == ["a::t"]

    FakePipeline.ran = []
    FakePipeline.die_on = None
    report = _run_per_test(FakePipeline, str(tmp_path), TESTS, journal(store))
    assert FakePipeline.ran == ["b::t", "c::t"], "a finished test must not run again"
    assert {c.nodeid for c in report.classifications} == set(
        TESTS
    ), "a resumed run must report what the killed one already found"


def test_a_later_run_reuses_a_cached_verdict_without_running_it(store, tmp_path):
    """A fresh run over the same commit and versions pays nothing twice."""
    _run_per_test(FakePipeline, str(tmp_path), TESTS, journal(store))
    FakePipeline.ran = []
    report = _run_per_test(
        FakePipeline, str(tmp_path), TESTS, journal(store, run_id="run2")
    )
    assert FakePipeline.ran == []
    assert len(report.classifications) == 3


def test_refresh_runs_a_cached_test_again(store, tmp_path):
    _run_per_test(FakePipeline, str(tmp_path), TESTS, journal(store))
    FakePipeline.ran = []
    _run_per_test(
        FakePipeline,
        str(tmp_path),
        TESTS,
        journal(store, refresh=True, run_id="run2"),
    )
    assert FakePipeline.ran == TESTS


def test_a_different_commit_is_not_served_from_the_cache(store, tmp_path):
    _run_per_test(FakePipeline, str(tmp_path), TESTS, journal(store))
    FakePipeline.ran = []
    _run_per_test(
        FakePipeline,
        str(tmp_path),
        TESTS,
        journal(store, commit="deadbeef", run_id="run2"),
    )
    assert FakePipeline.ran == TESTS, "a verdict belongs to the commit it was found on"


def test_a_test_that_keeps_killing_its_worker_is_abandoned(store, tmp_path):
    """Without a bound, one such test stops the run from ever finishing."""
    FakePipeline.die_on = "a::t"
    for _ in range(3):
        with pytest.raises(KeyboardInterrupt):
            _run_per_test(FakePipeline, str(tmp_path), ["a::t"], journal(store))
    _run_per_test(FakePipeline, str(tmp_path), ["a::t"], journal(store))
    assert FakePipeline.ran == [], "an abandoned test must not be offered again"
    assert store.progress("run1") == {"abandoned": 1}


def test_telemetry_survives_the_journal(store, tmp_path):
    """A cached verdict keeps the evidence behind it, not just the word."""
    _run_per_test(FakePipeline, str(tmp_path), ["a::t"], journal(store))
    FakePipeline.ran = []
    report = _run_per_test(
        FakePipeline, str(tmp_path), ["a::t"], journal(store, run_id="run2")
    )
    assert FakePipeline.ran == []
    found = report.classifications[0]
    assert found.search is not None and found.search.code_locations == 5


def test_a_run_with_no_store_still_runs_everything(tmp_path):
    report = _run_per_test(FakePipeline, str(tmp_path), TESTS)
    assert FakePipeline.ran == TESTS
    assert len(report.classifications) == 3
