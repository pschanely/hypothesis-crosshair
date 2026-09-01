"""Data types shared across the discovery pipeline."""

import enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional


class Tier(str, enum.Enum):
    """Which of the two execution tiers produced a result.

    ``A`` runs carry no observability and no external tracer; only they may
    decide a verdict. ``B`` runs carry observability and feed steering.
    """

    A_VERDICT = "A"
    B_TELEMETRY = "B"


class Arm(str, enum.Enum):
    """Which leg of the three-way differential a run belongs to."""

    BASELINE = "baseline"
    CROSSHAIR = "crosshair"
    VALIDATION = "validation"


class Outcome(str, enum.Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"
    NOT_RUN = "not_run"


class Verdict(str, enum.Enum):
    TROPHY_CANDIDATE = "trophy_candidate"
    CROSSHAIR_FALSE_POSITIVE = "crosshair_false_positive"
    SHARED_FIND = "shared_find"
    SOUNDNESS_SUSPECT = "soundness_suspect"
    CROSSHAIR_FALSE_NEGATIVE = "crosshair_false_negative"
    NO_SIGNAL = "no_signal"
    CROSSHAIR_CRASH = "crosshair_crash"
    CROSSHAIR_TIMEOUT = "crosshair_timeout"
    QUARANTINED_UNSTABLE = "quarantined_unstable"
    QUARANTINED_NONDETERMINISTIC = "quarantined_nondeterministic"
    OBSERVER_EFFECT = "observer_effect"
    PENDING_VALIDATION = "pending_validation"
    PRE_EXISTING_FAILURE = "pre_existing_failure"
    NO_BASELINE_RESULT = "no_baseline_result"


#: Verdicts that must never be reported without a human reading them first.
NEEDS_HUMAN_REVIEW = frozenset({Verdict.TROPHY_CANDIDATE})

#: Verdicts belonging to the CrossHair-defect stream rather than the trophy stream.
CROSSHAIR_DEFECT_VERDICTS = frozenset(
    {
        Verdict.CROSSHAIR_FALSE_POSITIVE,
        Verdict.SOUNDNESS_SUSPECT,
        Verdict.CROSSHAIR_FALSE_NEGATIVE,
        Verdict.CROSSHAIR_CRASH,
        Verdict.CROSSHAIR_TIMEOUT,
        Verdict.OBSERVER_EFFECT,
    }
)


@dataclass
class CaseOutcome:
    """The result of a single test within one pytest run."""

    nodeid: str
    outcome: Outcome
    duration: float = 0.0
    exception_type: Optional[str] = None
    message: Optional[str] = None
    falsifying_example: Optional[str] = None
    longrepr: Optional[str] = None


@dataclass
class SearchProgress:
    """How far CrossHair's own path search got, per test.

    Read from the provider's ``CoveragePathingOracle``. ``code_locations`` is
    the number of distinct places the solver forked; ``iters_since_discovery``
    is how many iterations passed without finding a new one. Unlike the
    completion counts this needs no observability, so it is available in both
    tiers, and a large gap between them is itself evidence of an observer
    effect.

    It measures reach, not discrimination: the same locations are visited
    whether or not the solver drove an interesting value through them. Use it
    to spot a stalled search, not to certify a thorough one.
    """

    code_locations: int = 0
    iters_since_discovery: int = 0
    solver_iterations: int = 0

    @property
    def discovery_rate(self) -> float:
        if not self.solver_iterations:
            return 0.0
        return self.code_locations / self.solver_iterations

    def stalled(self, threshold: int) -> bool:
        """Whether the search stopped finding new code locations.

        Only meaningful once the search has actually run past the threshold;
        a short run has not had the chance to stall.
        """
        return (
            self.solver_iterations > threshold
            and self.iters_since_discovery > threshold
        )


@dataclass
class CompletionStats:
    """Aggregated ``metadata.backend.completion`` counts from a tier-B run."""

    counts: Dict[str, int] = field(default_factory=dict)
    crosshair_cases: int = 0
    covered_lines: Dict[str, List[int]] = field(default_factory=dict)
    realizing_cases: int = 0
    realizations: int = 0

    @property
    def productive(self) -> int:
        """Iterations that explored user code rather than being discarded."""
        return sum(
            n
            for text, n in self.counts.items()
            if text.startswith("raised ") or text == "completed normally"
        )

    @property
    def productivity(self) -> float:
        if not self.crosshair_cases:
            return 0.0
        return self.productive / self.crosshair_cases

    @property
    def realization_rate(self) -> float:
        """Share of solver iterations in which a symbolic value was realized.

        A realized value is concrete for the rest of the iteration, so the
        solver has stopped steering it. A high rate means the run degenerated
        towards random search regardless of how the iterations completed.
        """
        if not self.crosshair_cases:
            return 0.0
        return self.realizing_cases / self.crosshair_cases

    @property
    def searched_symbolically(self) -> int:
        return self.crosshair_cases - self.realizing_cases

    def dominant_ignore_reason(self) -> Optional[str]:
        ignored = {
            t: n for t, n in self.counts.items() if t.startswith("ignored due to")
        }
        if not ignored:
            return None
        return max(ignored, key=lambda t: ignored[t])


@dataclass
class RunResult:
    """Everything one pytest invocation produced."""

    arm: Arm
    tier: Tier
    returncode: int
    duration: float
    outcomes: Dict[str, CaseOutcome] = field(default_factory=dict)
    telemetry: Dict[str, CompletionStats] = field(default_factory=dict)
    search: Dict[str, SearchProgress] = field(default_factory=dict)
    timed_out: bool = False
    crashed: bool = False
    stderr_tail: str = ""

    def outcome_of(self, nodeid: str) -> Outcome:
        found = self.outcomes.get(nodeid)
        if found is None:
            return Outcome.TIMEOUT if self.timed_out else Outcome.NOT_RUN
        return found.outcome


@dataclass
class Classification:
    """The verdict for one test, with the evidence behind it."""

    nodeid: str
    verdict: Verdict
    baseline: Outcome
    crosshair: Outcome
    validation: Optional[Outcome] = None
    rationale: str = ""
    falsifying_example: Optional[str] = None
    exception_type: Optional[str] = None
    completion: Optional[CompletionStats] = None
    search: Optional[SearchProgress] = None

    @property
    def needs_human_review(self) -> bool:
        return self.verdict in NEEDS_HUMAN_REVIEW

    @property
    def is_crosshair_defect(self) -> bool:
        return self.verdict in CROSSHAIR_DEFECT_VERDICTS
