"""The baseline gate and the three-way differential classifier.

Every input here must come from a tier-A run. Tier-B telemetry is attached to
a classification as evidence and is never read to decide one: observability
realizes symbolic draws and perturbs the search, so a CrossHair-backed
observability run is known to diverge from the run being judged. What it
observes becomes a clue -- see ``telemetry.clues_from`` -- which calls for a
further tier-A run rather than settling anything itself.
"""

import enum
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .model import (
    CaseOutcome,
    Classification,
    CompletionStats,
    Outcome,
    RunResult,
    SearchProgress,
    Tier,
    Verdict,
)


class Stability(str, enum.Enum):
    STABLE_PASS = "stable_pass"
    STABLE_FAIL = "stable_fail"
    UNSTABLE = "unstable"
    NO_RESULT = "no_result"


@dataclass
class BaselineVerdict:
    nodeid: str
    stability: Stability
    outcomes: List[Outcome]

    @property
    def eligible(self) -> bool:
        """Only consistently-passing tests can yield a trophy."""
        return self.stability is Stability.STABLE_PASS


def baseline_gate(
    runs: Sequence[RunResult], nodeids: Sequence[str]
) -> Dict[str, BaselineVerdict]:
    """Judge each test's stability across repeated baseline runs at different seeds."""
    verdicts: Dict[str, BaselineVerdict] = {}
    for nodeid in nodeids:
        outcomes = [run.outcome_of(nodeid) for run in runs]
        # Only a pass or a fail says anything about the test. A seed that
        # errored, was skipped or never ran produced no opinion to differ
        # from, and counting it as disagreement reports instability that the
        # seeds do not show -- including from a single seed, which cannot
        # differ from itself.
        decisive = [o for o in outcomes if o in (Outcome.PASSED, Outcome.FAILED)]
        if not decisive:
            stability = Stability.NO_RESULT
        elif all(o is Outcome.PASSED for o in decisive):
            stability = Stability.STABLE_PASS
        elif all(o is Outcome.FAILED for o in decisive):
            stability = Stability.STABLE_FAIL
        else:
            stability = Stability.UNSTABLE
        verdicts[nodeid] = BaselineVerdict(nodeid, stability, outcomes)
    return verdicts


def needs_validation(baseline: Outcome, crosshair: Outcome) -> bool:
    """Whether a clean-room replay is required before any claim can be made."""
    return baseline is Outcome.PASSED and crosshair is Outcome.FAILED


#: Exception types that mean CrossHair itself failed, not the code under test.
#:
#: These surface as ordinary test failures, so without this they are scored as
#: findings and routed to the trophy track, where a CrossHair defect would be
#: presented as a candidate third-party bug awaiting validation.
CROSSHAIR_INTERNAL_EXCEPTIONS = (
    "CrossHairInternal",
    "CrosshairInternal",
    "NotDeterministic",
    "UnexploredPath",
    "IgnoreAttempt",
)


def _is_crosshair_internal_error(detail: Optional[CaseOutcome]) -> bool:
    name = (detail.exception_type or "") if detail is not None else ""
    tail = name.rsplit(".", 1)[-1]
    return tail in CROSSHAIR_INTERNAL_EXCEPTIONS


def classify(
    nodeid: str,
    *,
    baseline: BaselineVerdict,
    crosshair_run: RunResult,
    validation: Optional[Outcome] = None,
    stats: Optional[CompletionStats] = None,
    search: Optional["SearchProgress"] = None,
) -> Classification:
    if crosshair_run.tier is not Tier.A_VERDICT:
        raise ValueError("classification requires a tier-A run")

    crosshair = crosshair_run.outcome_of(nodeid)
    detail = crosshair_run.outcomes.get(nodeid)
    result = Classification(
        nodeid=nodeid,
        verdict=Verdict.NO_SIGNAL,
        baseline=baseline.outcomes[0] if baseline.outcomes else Outcome.NOT_RUN,
        crosshair=crosshair,
        validation=validation,
        falsifying_example=detail.falsifying_example if detail else None,
        exception_type=detail.exception_type if detail else None,
        completion=stats,
        search=search,
    )

    # Checked before the crash arm: the sandbox escalates a timeout straight to
    # SIGKILL, so an exhausted budget also sets a negative return code and would
    # otherwise be reported as a CrossHair crash.
    if crosshair is Outcome.TIMEOUT or crosshair_run.timed_out:
        result.verdict = Verdict.CROSSHAIR_TIMEOUT
        result.rationale = "CrossHair arm exceeded its wall-clock budget"
        return result

    if crosshair_run.crashed or _is_crosshair_internal_error(detail):
        result.verdict = Verdict.CROSSHAIR_CRASH
        result.rationale = (
            f"CrossHair raised {detail.exception_type} inside the test"
            if detail is not None and _is_crosshair_internal_error(detail)
            else f"CrossHair arm exited abnormally (rc={crosshair_run.returncode})"
        )
        return result

    if baseline.stability is Stability.NO_RESULT:
        result.verdict = Verdict.NO_BASELINE_RESULT
        result.rationale = (
            "the baseline arm produced no pass or fail for this test: "
            + ", ".join(o.value for o in baseline.outcomes)
        )
        return result

    if baseline.stability is Stability.UNSTABLE:
        result.verdict = Verdict.QUARANTINED_UNSTABLE
        result.rationale = "baseline passed on some seeds and failed on others: " + (
            ", ".join(o.value for o in baseline.outcomes)
        )
        return result

    if baseline.stability is Stability.STABLE_FAIL:
        if crosshair is Outcome.FAILED:
            result.verdict = Verdict.SHARED_FIND
            result.rationale = "both arms fail; not attributable to CrossHair"
        else:
            result.verdict = Verdict.CROSSHAIR_FALSE_NEGATIVE
            result.rationale = "baseline fails but CrossHair does not"
        return result

    if crosshair is Outcome.FAILED:
        if validation is None or validation is Outcome.NOT_RUN:
            # A replay that could not be carried out is not evidence of absence:
            # never downgrade a finding to a false positive on missing evidence.
            result.verdict = Verdict.PENDING_VALIDATION
            result.rationale = "clean-room replay did not produce a conclusive result"
        elif validation is Outcome.FAILED:
            result.verdict = Verdict.TROPHY_CANDIDATE
            result.rationale = (
                "baseline passes, CrossHair fails, and the example reproduces "
                "with the plugin absent"
            )
        else:
            result.verdict = Verdict.CROSSHAIR_FALSE_POSITIVE
            result.rationale = (
                "CrossHair reported a failure that does not reproduce without it "
                f"(replay outcome: {validation.value})"
            )
        return result

    result.rationale = "neither arm found a failure"
    return result


def detect_observer_effect(
    tier_a: RunResult, tier_b: RunResult, nodeids: Sequence[str]
) -> List[str]:
    """Node ids whose outcome disagrees between the verdict and telemetry tiers.

    A disagreement at a fixed seed means observability changed the result, which
    is a CrossHair or plugin defect in its own right.
    """
    diverged = []
    for nodeid in nodeids:
        a, b = tier_a.outcome_of(nodeid), tier_b.outcome_of(nodeid)
        if a in (Outcome.PASSED, Outcome.FAILED) and b in (
            Outcome.PASSED,
            Outcome.FAILED,
        ):
            if a is not b:
                diverged.append(nodeid)
    return diverged
