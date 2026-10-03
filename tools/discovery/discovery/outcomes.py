"""Where a triaged cluster goes next.

Triage says what a cluster is; this says what follows from that, and refuses
the promotions that do not follow. A trophy needs two independent things to
agree -- the classifier saw the example reproduce in a clean room, and triage
read the code and called it a project bug -- because either one alone has a
failure mode the other covers.

Nothing here reports anything anywhere. A trophy record is a draft for a
person to read.
"""

import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .cluster import Cluster
from .model import Classification, Verdict
from .triage import TriageCategory, TriageVerdict, signature_key

#: Classifier verdicts from which a trophy may be drafted.
#:
#: Only this one: it is the verdict that means the falsifying example was
#: replayed with the plugin absent and still failed. pending_validation means
#: the replay was inconclusive, which is not evidence either way, so triage
#: calling it a project bug cannot stand in for the replay that did not run.
TROPHY_ELIGIBLE = frozenset({Verdict.TROPHY_CANDIDATE})


@dataclass
class Evidence:
    """What the run can say about one cluster, independent of any judgment."""

    project: str
    commit: str
    nodeids: List[str]
    exception_type: str
    frame: str
    falsifying_example: str
    sample: str
    crosshair_version: str
    classifier_verdicts: List[str] = field(default_factory=list)


@dataclass
class TrophyRecord:
    """A draft third-party finding, for a person to review and nothing else."""

    evidence: Evidence
    reasoning: str
    confidence: float
    baseline_examples: int
    baseline_seeds: int
    found_on: str

    @property
    def why_random_search_misses_it(self) -> str:
        """The column that makes this a CrossHair trophy rather than a bug.

        Quantified from the baseline arm's actual effort, so it says what was
        tried rather than asserting that random search would fail.
        """
        total = self.baseline_examples * self.baseline_seeds
        return (
            f"the baseline found nothing in {self.baseline_examples} examples "
            f"across {self.baseline_seeds} seeds ({total} draws)"
        )


@dataclass
class CrossHairDefect:
    """A finding about CrossHair or this plugin."""

    evidence: Evidence
    reasoning: str
    confidence: float


@dataclass
class Dismissed:
    evidence: Evidence
    reasoning: str
    category: TriageCategory


@dataclass
class Routing:
    trophies: List[TrophyRecord] = field(default_factory=list)
    crosshair_defects: List[CrossHairDefect] = field(default_factory=list)
    dismissed: List[Dismissed] = field(default_factory=list)
    needs_human: List[Dismissed] = field(default_factory=list)
    #: Clusters triage called a project bug that the clean room never confirmed.
    withheld: List[Dismissed] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            len(self.trophies)
            + len(self.crosshair_defects)
            + len(self.dismissed)
            + len(self.needs_human)
            + len(self.withheld)
        )


def _evidence(
    group: Cluster,
    by_nodeid: Dict[str, Classification],
    project: str,
    commit: str,
    crosshair_version: str,
) -> Evidence:
    seen = [by_nodeid[n] for n in group.nodeids if n in by_nodeid]
    return Evidence(
        project=project,
        commit=commit,
        nodeids=list(group.nodeids),
        exception_type=group.signature.exception_type,
        frame=group.signature.frame,
        falsifying_example=group.examples[0] if group.examples else "",
        sample=group.sample,
        crosshair_version=crosshair_version,
        classifier_verdicts=sorted({c.verdict.value for c in seen}),
    )


def _is_trophy_eligible(group: Cluster, by_nodeid: Dict[str, Classification]) -> bool:
    return any(
        by_nodeid[n].verdict in TROPHY_ELIGIBLE for n in group.nodeids if n in by_nodeid
    )


def route(
    clusters: Sequence[Cluster],
    decided: Dict[str, TriageVerdict],
    classifications: Sequence[Classification],
    *,
    project: str,
    commit: str = "",
    crosshair_version: str = "",
    baseline_examples: int = 0,
    baseline_seeds: int = 0,
    today: Optional[str] = None,
) -> Routing:
    """Send each triaged cluster where its category and its evidence agree.

    A cluster with no triage answer is left alone: an undecided cluster is not
    a dismissed one.
    """
    by_nodeid = {c.nodeid: c for c in classifications}
    found_on = today or datetime.date.today().isoformat()
    routing = Routing()
    for group in clusters:
        verdict = decided.get(signature_key(group.signature))
        if verdict is None:
            continue
        evidence = _evidence(group, by_nodeid, project, commit, crosshair_version)
        if verdict.category is TriageCategory.CROSSHAIR_ARTIFACT:
            routing.crosshair_defects.append(
                CrossHairDefect(evidence, verdict.reasoning, verdict.confidence)
            )
        elif verdict.category is TriageCategory.PROJECT_BUG:
            if _is_trophy_eligible(group, by_nodeid):
                routing.trophies.append(
                    TrophyRecord(
                        evidence=evidence,
                        reasoning=verdict.reasoning,
                        confidence=verdict.confidence,
                        baseline_examples=baseline_examples,
                        baseline_seeds=baseline_seeds,
                        found_on=found_on,
                    )
                )
            else:
                routing.withheld.append(
                    Dismissed(evidence, verdict.reasoning, verdict.category)
                )
        elif verdict.category is TriageCategory.OVERSTRONG_PROPERTY:
            routing.dismissed.append(
                Dismissed(evidence, verdict.reasoning, verdict.category)
            )
        else:
            routing.needs_human.append(
                Dismissed(evidence, verdict.reasoning, verdict.category)
            )
    return routing
