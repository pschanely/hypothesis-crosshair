"""Scoring a decider against cases whose answer is already known.

Accuracy is the wrong headline here, because the errors are not worth the
same. Calling someone else's correct code a bug is the only mistake that can
reach a stranger, and one of those costs more than several missed findings.
A scorecard therefore counts the kinds of error separately and reports the
dangerous one first.
"""

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .cluster import Signature
from .triage import Decider, TriageCategory, TriageItem, parse_verdict


@dataclass
class Case:
    """One cluster with a known answer."""

    name: str
    item: TriageItem
    expected: TriageCategory
    #: Why this is the right answer, for whoever revisits a disputed label.
    rationale: str = ""


@dataclass
class Outcome:
    case: Case
    predicted: Optional[TriageCategory]
    confidence: float = 0.0
    reasoning: str = ""
    error: str = ""

    @property
    def correct(self) -> bool:
        return self.predicted is self.case.expected

    @property
    def reaches_a_stranger(self) -> bool:
        """Whether this error could put a draft in front of a third party.

        Only a predicted project bug can, and only when it is not one. The
        routing still needs a clean-room confirmation before such a draft
        exists, so this counts the decider's share of that risk, not a
        certainty.
        """
        return (
            self.predicted is TriageCategory.PROJECT_BUG
            and self.case.expected is not TriageCategory.PROJECT_BUG
        )

    @property
    def loses_a_finding(self) -> bool:
        """A real project bug routed somewhere nothing will pick it up again."""
        return self.case.expected is TriageCategory.PROJECT_BUG and self.predicted in (
            TriageCategory.OVERSTRONG_PROPERTY,
            TriageCategory.CROSSHAIR_ARTIFACT,
        )

    @property
    def deferred(self) -> bool:
        """Answered `unclear` where a real answer existed: safe, but costly."""
        return (
            self.predicted is TriageCategory.UNCLEAR
            and self.case.expected is not TriageCategory.UNCLEAR
        )

    @property
    def wrong_stream(self) -> bool:
        """Confused our own defect with an over-strong property, either way."""
        pair = {TriageCategory.CROSSHAIR_ARTIFACT, TriageCategory.OVERSTRONG_PROPERTY}
        return (
            self.predicted in pair and self.case.expected in pair and not self.correct
        )


@dataclass
class Scorecard:
    outcomes: List[Outcome] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def answered(self) -> int:
        return sum(1 for o in self.outcomes if o.predicted is not None)

    @property
    def correct(self) -> int:
        return sum(1 for o in self.outcomes if o.correct)

    @property
    def unusable(self) -> List[Outcome]:
        return [o for o in self.outcomes if o.predicted is None]

    @property
    def reaching_a_stranger(self) -> List[Outcome]:
        return [o for o in self.outcomes if o.reaches_a_stranger]

    @property
    def lost_findings(self) -> List[Outcome]:
        return [o for o in self.outcomes if o.loses_a_finding]

    @property
    def deferred(self) -> List[Outcome]:
        return [o for o in self.outcomes if o.deferred]

    @property
    def wrong_stream(self) -> List[Outcome]:
        return [o for o in self.outcomes if o.wrong_stream]

    def confusion(self) -> Dict[str, Dict[str, int]]:
        table: Dict[str, Dict[str, int]] = {}
        for outcome in self.outcomes:
            row = table.setdefault(outcome.case.expected.value, {})
            name = outcome.predicted.value if outcome.predicted else "unusable"
            row[name] = row.get(name, 0) + 1
        return table

    def describe(self) -> List[str]:
        lines = [
            f"{self.correct}/{self.total} correct "
            f"({self.answered} answered, {len(self.unusable)} unusable)",
            "",
            f"  reaching a stranger: {len(self.reaching_a_stranger)}   "
            "(called someone else's correct code a bug)",
        ]
        for outcome in self.reaching_a_stranger:
            lines.append(
                f"      {outcome.case.name}: expected "
                f"{outcome.case.expected.value}, said project_bug"
            )
        lines.append(f"  lost findings:       {len(self.lost_findings)}")
        for outcome in self.lost_findings:
            lines.append(
                f"      {outcome.case.name}: said "
                f"{outcome.predicted.value if outcome.predicted else '?'}"
            )
        lines.append(f"  wrong stream:        {len(self.wrong_stream)}")
        lines.append(f"  deferred to a human: {len(self.deferred)}")
        for outcome in self.unusable:
            lines.append(f"      UNUSABLE {outcome.case.name}: {outcome.error[:120]}")
        lines.append("")
        lines.append("  expected \\ predicted")
        for expected, row in sorted(self.confusion().items()):
            got = ", ".join(f"{k}={v}" for k, v in sorted(row.items()))
            lines.append(f"      {expected:<22} {got}")
        return lines


def load_cases(path: str) -> List[Case]:
    """Read labelled cases from a JSONL file."""
    cases = []
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            raw = json.loads(line)
            cases.append(
                Case(
                    name=raw["name"],
                    expected=TriageCategory(raw["expected"]),
                    rationale=raw.get("rationale", ""),
                    item=TriageItem(
                        key=raw["name"],
                        signature=Signature(
                            exception_type=raw.get("exception_type", ""),
                            frame=raw.get("frame", ""),
                            message=raw.get("message", ""),
                        ),
                        nodeids=list(raw.get("nodeids", [])),
                        examples=list(raw.get("examples", [])),
                        project=raw.get("project", ""),
                        sample=raw.get("sample", ""),
                    ),
                )
            )
    return cases


def score(cases: Sequence[Case], decide: Decider) -> Scorecard:
    """Run a decider over every case and tally what it got right and wrong."""
    card = Scorecard()
    for case in cases:
        try:
            verdict = parse_verdict(decide(case.item))
        except Exception as exc:
            card.outcomes.append(
                Outcome(case, None, error=f"{type(exc).__name__}: {exc}")
            )
            continue
        card.outcomes.append(
            Outcome(
                case,
                verdict.category,
                confidence=verdict.confidence,
                reasoning=verdict.reasoning,
            )
        )
    return card
