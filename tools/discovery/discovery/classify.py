"""The baseline gate and the three-way differential classifier.

Every input here must come from a tier-A run. Tier-B telemetry may be attached
as supporting evidence, but never decides a verdict.
"""

import enum
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from . import telemetry
from .model import Classification, CompletionStats, Outcome, RunResult, Tier, Verdict

#: Share of solver iterations lost to nondeterminism before a test is dropped.
NONDETERMINISM_QUARANTINE_RATE = 0.5

#: Completion text CrossHair emits when it believes it closed the search space.
EXHAUSTED_COMPLETION = "exhausted all paths"


class Stability(str, enum.Enum):
    STABLE_PASS = "stable_pass"
    STABLE_FAIL = "stable_fail"
    UNSTABLE = "unstable"


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
        if all(o is Outcome.PASSED for o in outcomes):
            stability = Stability.STABLE_PASS
        elif all(o is Outcome.FAILED for o in outcomes):
            stability = Stability.STABLE_FAIL
        else:
            stability = Stability.UNSTABLE
        verdicts[nodeid] = BaselineVerdict(nodeid, stability, outcomes)
    return verdicts


def needs_validation(baseline: Outcome, crosshair: Outcome) -> bool:
    """Whether a clean-room replay is required before any claim can be made."""
    return baseline is Outcome.PASSED and crosshair is Outcome.FAILED


def classify(
    nodeid: str,
    *,
    baseline: BaselineVerdict,
    crosshair_run: RunResult,
    validation: Optional[Outcome] = None,
    stats: Optional[CompletionStats] = None,
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
    )

    if crosshair_run.crashed:
        result.verdict = Verdict.CROSSHAIR_CRASH
        result.rationale = (
            f"CrossHair arm exited abnormally (rc={crosshair_run.returncode})"
        )
        return result

    if baseline.stability is Stability.UNSTABLE:
        result.verdict = Verdict.QUARANTINED_UNSTABLE
        result.rationale = "baseline outcomes differed across seeds: " + ", ".join(
            o.value for o in baseline.outcomes
        )
        return result

    if crosshair is Outcome.TIMEOUT or crosshair_run.timed_out:
        result.verdict = Verdict.CROSSHAIR_TIMEOUT
        result.rationale = "CrossHair arm exceeded its wall-clock budget"
        return result

    if baseline.stability is Stability.STABLE_FAIL:
        if crosshair is Outcome.FAILED:
            result.verdict = Verdict.SHARED_FIND
            result.rationale = "both arms fail; not attributable to CrossHair"
        else:
            exhausted = _claims_exhausted(stats)
            result.verdict = (
                Verdict.SOUNDNESS_SUSPECT
                if exhausted
                else Verdict.CROSSHAIR_FALSE_NEGATIVE
            )
            result.rationale = (
                "baseline fails but CrossHair reported the path space exhausted"
                if exhausted
                else "baseline fails but CrossHair does not"
            )
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

    if (
        stats is not None
        and telemetry.nondeterminism_rate(stats) >= NONDETERMINISM_QUARANTINE_RATE
    ):
        result.verdict = Verdict.QUARANTINED_NONDETERMINISTIC
        result.rationale = (
            "most solver iterations were discarded for detected nondeterminism; "
            "CrossHair's check is deep, so ordinary internal caching is enough "
            "to trip it. Not treated as a CrossHair defect."
        )
        return result

    result.rationale = "neither arm found a failure"
    return result


def _claims_exhausted(stats: Optional[CompletionStats]) -> bool:
    if stats is None:
        return False
    return any(EXHAUSTED_COMPLETION in text for text in stats.counts)


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
