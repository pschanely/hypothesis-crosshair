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


def test_missed_failure_after_claiming_exhaustion_is_a_soundness_suspect():
    stats = CompletionStats(counts={"exhausted all paths - nothing else to do": 3})
    verdict = classify(
        NODE,
        baseline=gate(Outcome.FAILED, Outcome.FAILED),
        crosshair_run=run(Outcome.PASSED),
        stats=stats,
    )
    assert verdict.verdict is Verdict.SOUNDNESS_SUSPECT


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


def test_nondeterminism_quarantines_rather_than_blaming_crosshair():
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
    assert verdict.verdict is Verdict.QUARANTINED_NONDETERMINISTIC
    assert not verdict.is_crosshair_defect


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


def test_a_test_that_never_ran_is_not_called_unstable():
    verdict = classify(
        NODE,
        baseline=gate(Outcome.NOT_RUN, Outcome.NOT_RUN, Outcome.NOT_RUN),
        crosshair_run=run(Outcome.NOT_RUN),
    )
    assert verdict.verdict is Verdict.NO_BASELINE_RESULT
