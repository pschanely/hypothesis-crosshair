import pytest
from discovery.classify import (
    Stability,
    baseline_gate,
    classify,
    detect_observer_effect,
    needs_validation,
)
from discovery.model import (
    Arm,
    CaseOutcome,
    CompletionStats,
    Outcome,
    RunResult,
    Tier,
    Verdict,
)

NODE = "t.py::test_x"


def run(outcome, *, tier=Tier.A_VERDICT, crashed=False, timed_out=False, example=None):
    result = RunResult(Arm.CROSSHAIR, tier, 1, 1.0)
    result.crashed = crashed
    result.timed_out = timed_out
    if outcome is not None:
        result.outcomes[NODE] = CaseOutcome(
            NODE, outcome, falsifying_example=example, exception_type="AssertionError"
        )
    return result


def gate(*outcomes):
    return baseline_gate(
        [run(o) for o in outcomes],
        [NODE],
    )[NODE]


def test_gate_labels_stability():
    assert gate(Outcome.PASSED, Outcome.PASSED).stability is Stability.STABLE_PASS
    assert gate(Outcome.FAILED, Outcome.FAILED).stability is Stability.STABLE_FAIL
    assert gate(Outcome.PASSED, Outcome.FAILED).stability is Stability.UNSTABLE


def test_trophy_requires_a_reproducing_replay():
    verdict = classify(
        NODE,
        baseline=gate(Outcome.PASSED, Outcome.PASSED),
        crosshair_run=run(Outcome.FAILED, example="test_x(a=1)"),
        validation=Outcome.FAILED,
    )
    assert verdict.verdict is Verdict.TROPHY_CANDIDATE
    assert verdict.needs_human_review


def test_finding_that_does_not_reproduce_is_a_crosshair_defect():
    verdict = classify(
        NODE,
        baseline=gate(Outcome.PASSED, Outcome.PASSED),
        crosshair_run=run(Outcome.FAILED),
        validation=Outcome.PASSED,
    )
    assert verdict.verdict is Verdict.CROSSHAIR_FALSE_POSITIVE
    assert verdict.is_crosshair_defect


@pytest.mark.parametrize("validation", [None, Outcome.NOT_RUN])
def test_inconclusive_replay_never_refutes_a_finding(validation):
    """Absence of replay evidence must not be read as evidence of absence."""
    verdict = classify(
        NODE,
        baseline=gate(Outcome.PASSED, Outcome.PASSED),
        crosshair_run=run(Outcome.FAILED),
        validation=validation,
    )
    assert verdict.verdict is Verdict.PENDING_VALIDATION


def test_shared_find_is_not_a_trophy():
    verdict = classify(
        NODE,
        baseline=gate(Outcome.FAILED, Outcome.FAILED),
        crosshair_run=run(Outcome.FAILED),
    )
    assert verdict.verdict is Verdict.SHARED_FIND
    assert not verdict.needs_human_review


def test_missed_failure_is_a_false_negative():
    verdict = classify(
        NODE,
        baseline=gate(Outcome.FAILED, Outcome.FAILED),
        crosshair_run=run(Outcome.PASSED),
    )
    assert verdict.verdict is Verdict.CROSSHAIR_FALSE_NEGATIVE


def test_an_exhaustion_claim_from_telemetry_does_not_change_the_verdict():
    """Observability perturbs the search, so the run that claimed exhaustion
    is not the run being judged."""
    stats = CompletionStats(counts={"exhausted all paths - nothing else to do": 3})
    verdict = classify(
        NODE,
        baseline=gate(Outcome.FAILED, Outcome.FAILED),
        crosshair_run=run(Outcome.PASSED),
        stats=stats,
    )
    assert verdict.verdict is Verdict.CROSSHAIR_FALSE_NEGATIVE
    assert verdict.completion is stats, "telemetry is attached as evidence"


def test_unstable_baseline_is_quarantined_before_anything_else():
    verdict = classify(
        NODE,
        baseline=gate(Outcome.PASSED, Outcome.FAILED),
        crosshair_run=run(Outcome.FAILED),
        validation=Outcome.FAILED,
    )
    assert verdict.verdict is Verdict.QUARANTINED_UNSTABLE


def test_crash_outranks_every_other_signal():
    verdict = classify(
        NODE,
        baseline=gate(Outcome.PASSED, Outcome.PASSED),
        crosshair_run=run(Outcome.PASSED, crashed=True),
    )
    assert verdict.verdict is Verdict.CROSSHAIR_CRASH


def test_nondeterminism_in_telemetry_does_not_change_the_verdict():
    stats = CompletionStats(
        counts={"ignored due to non determinism detected": 9, "completed normally": 1},
        crosshair_cases=10,
    )
    verdict = classify(
        NODE,
        baseline=gate(Outcome.PASSED, Outcome.PASSED),
        crosshair_run=run(Outcome.PASSED),
        stats=stats,
    )
    assert verdict.verdict is Verdict.NO_SIGNAL


def test_no_telemetry_can_change_any_verdict():
    """The rule, rather than one instance of it.

    Telemetry comes from a CrossHair-backed observability run, which realizes
    symbolic draws and so diverges from the run being judged. Whatever it
    says, the verdict must be the one the tier-A outcomes give.
    """
    loud = CompletionStats(
        counts={
            "ignored due to non determinism detected": 50,
            "exhausted all paths - nothing else to do": 50,
        },
        crosshair_cases=100,
        realizing_cases=100,
        realizations=500,
    )
    for baseline_outcomes, crosshair_outcome in (
        ((Outcome.PASSED, Outcome.PASSED), Outcome.PASSED),
        ((Outcome.FAILED, Outcome.FAILED), Outcome.PASSED),
        ((Outcome.FAILED, Outcome.FAILED), Outcome.FAILED),
    ):
        without = classify(
            NODE,
            baseline=gate(*baseline_outcomes),
            crosshair_run=run(crosshair_outcome),
        )
        with_telemetry = classify(
            NODE,
            baseline=gate(*baseline_outcomes),
            crosshair_run=run(crosshair_outcome),
            stats=loud,
        )
        assert without.verdict is with_telemetry.verdict, baseline_outcomes


def test_telemetry_tier_may_not_decide_a_verdict():
    with pytest.raises(ValueError):
        classify(
            NODE,
            baseline=gate(Outcome.PASSED, Outcome.PASSED),
            crosshair_run=run(Outcome.FAILED, tier=Tier.B_TELEMETRY),
        )


def test_observer_effect_is_outcome_disagreement_between_tiers():
    tier_a = run(Outcome.PASSED)
    tier_b = run(Outcome.FAILED, tier=Tier.B_TELEMETRY)
    assert detect_observer_effect(tier_a, tier_b, [NODE]) == [NODE]
    assert detect_observer_effect(tier_a, run(Outcome.PASSED), [NODE]) == []


def test_needs_validation_only_when_crosshair_is_alone_in_failing():
    assert needs_validation(Outcome.PASSED, Outcome.FAILED)
    assert not needs_validation(Outcome.FAILED, Outcome.FAILED)
    assert not needs_validation(Outcome.PASSED, Outcome.PASSED)


def test_a_single_errored_baseline_is_not_called_unstable():
    """One seed cannot differ from itself.

    natsort's locale-dependent tests error in baseline setup, and a run with
    a single seed reported "baseline outcomes differed across seeds: error",
    which is not a thing that happened.
    """
    verdict = classify(
        NODE, baseline=gate(Outcome.ERROR), crosshair_run=run(Outcome.PASSED)
    )
    assert verdict.verdict is Verdict.NO_BASELINE_RESULT
    assert "differed" not in verdict.rationale
    assert "error" in verdict.rationale


def test_a_baseline_that_only_errors_produced_no_result():
    assert gate(Outcome.ERROR, Outcome.ERROR).stability is Stability.NO_RESULT


def test_a_seed_that_errored_does_not_make_the_rest_unstable():
    """An error is the harness failing, not the test disagreeing with itself."""
    assert gate(Outcome.PASSED, Outcome.ERROR).stability is Stability.STABLE_PASS
    assert gate(Outcome.FAILED, Outcome.ERROR).stability is Stability.STABLE_FAIL
    assert gate(Outcome.PASSED, Outcome.SKIPPED).stability is Stability.STABLE_PASS


def test_genuine_disagreement_is_still_unstable():
    assert gate(Outcome.PASSED, Outcome.FAILED).stability is Stability.UNSTABLE
    assert (
        gate(Outcome.PASSED, Outcome.FAILED, Outcome.ERROR).stability
        is Stability.UNSTABLE
    )


def test_a_test_that_never_ran_is_not_called_unstable():
    verdict = classify(
        NODE,
        baseline=gate(Outcome.NOT_RUN, Outcome.NOT_RUN, Outcome.NOT_RUN),
        crosshair_run=run(Outcome.NOT_RUN),
    )
    assert verdict.verdict is Verdict.NO_BASELINE_RESULT


def _run_with(nodeid, outcome, **kw):
    """A tier-A crosshair run carrying one case outcome."""
    from discovery.model import Arm, CaseOutcome, RunResult, Tier

    run = RunResult(Arm.CROSSHAIR, Tier.A_VERDICT, kw.pop("returncode", 0), 1.0, **kw)
    run.outcomes[nodeid] = outcome
    return run


def test_a_timeout_is_not_reported_as_a_crash():
    """The sandbox SIGKILLs on timeout, so a timeout also looks like a crash."""
    from discovery.classify import BaselineVerdict, Stability, classify
    from discovery.model import CaseOutcome, Outcome, Verdict

    nodeid = "t.py::test_slow"
    run = _run_with(
        nodeid,
        CaseOutcome(nodeid, Outcome.NOT_RUN),
        returncode=-9,
        timed_out=True,
        crashed=True,
    )
    entry = classify(
        nodeid,
        baseline=BaselineVerdict(nodeid, Stability.STABLE_PASS, [Outcome.PASSED]),
        crosshair_run=run,
    )
    assert entry.verdict is Verdict.CROSSHAIR_TIMEOUT


def test_a_crosshair_internal_error_is_not_a_trophy_candidate():
    """CrossHair failing itself must not be routed to the trophy track."""
    from discovery.classify import BaselineVerdict, Stability, classify
    from discovery.model import CaseOutcome, Outcome, Verdict

    nodeid = "t.py::test_thing"
    run = _run_with(
        nodeid,
        CaseOutcome(
            nodeid,
            Outcome.FAILED,
            exception_type="crosshair.util.CrossHairInternal",
            message="Numeric operation on symbolic while not tracing",
        ),
    )
    entry = classify(
        nodeid,
        baseline=BaselineVerdict(nodeid, Stability.STABLE_PASS, [Outcome.PASSED]),
        crosshair_run=run,
    )
    assert entry.verdict is Verdict.CROSSHAIR_CRASH


def test_an_ordinary_failure_still_needs_validation():
    """The internal-error check must not swallow real findings."""
    from discovery.classify import BaselineVerdict, Stability, classify
    from discovery.model import CaseOutcome, Outcome, Verdict

    nodeid = "t.py::test_real"
    run = _run_with(
        nodeid,
        CaseOutcome(nodeid, Outcome.FAILED, exception_type="AssertionError"),
    )
    entry = classify(
        nodeid,
        baseline=BaselineVerdict(nodeid, Stability.STABLE_PASS, [Outcome.PASSED]),
        crosshair_run=run,
    )
    assert entry.verdict is Verdict.PENDING_VALIDATION
