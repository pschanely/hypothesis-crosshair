"""Parsing of Hypothesis observability output (tier-B runs only).

Telemetry steers the loop and feeds the CrossHair-defect stream. It never
decides a verdict: observability realizes symbolic draws and perturbs the
search, so classification reads tier-A runs exclusively.
"""

import json
import os
from typing import Dict, Iterable, Iterator, List, Optional

from .model import CompletionStats

#: Substring the provider logs when a symbolic value is made concrete.
REALIZE_MARKER = "SMT realized symbolic"

#: Above this share of realizing iterations, a "no failure found" result is not
#: evidence that the solver explored the test.
REALIZATION_UNRELIABLE_RATE = 0.5

#: Marker Hypothesis writes into ``how_generated`` for solver-produced cases.
CROSSHAIR_PHASE_MARKER = "backend='crosshair'"


def observed_files(storage_dir: str) -> List[str]:
    observed = os.path.join(storage_dir, "observed")
    if not os.path.isdir(observed):
        return []
    return sorted(
        os.path.join(observed, name)
        for name in os.listdir(observed)
        if name.endswith("_testcases.jsonl")
    )


def iter_test_cases(storage_dir: str) -> Iterator[dict]:
    for path in observed_files(storage_dir):
        with open(path, errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def is_crosshair_case(row: dict) -> bool:
    return CROSSHAIR_PHASE_MARKER in str(row.get("how_generated", ""))


def aggregate(rows: Iterable[dict]) -> Dict[str, CompletionStats]:
    """Summarize observability rows per test property name."""
    stats: Dict[str, CompletionStats] = {}
    for row in rows:
        name = row.get("property")
        if not name:
            continue
        entry = stats.setdefault(name, CompletionStats())
        for path, lines in (row.get("coverage") or {}).items():
            merged = set(entry.covered_lines.get(path, ()))
            merged.update(lines)
            entry.covered_lines[path] = sorted(merged)
        if not is_crosshair_case(row):
            continue
        entry.crosshair_cases += 1
        backend = row.get("metadata", {}).get("backend") or {}
        completion = backend.get("completion")
        if completion:
            entry.counts[completion] = entry.counts.get(completion, 0) + 1
        realized = sum(
            1 for message in backend.get("messages", []) if REALIZE_MARKER in message
        )
        if realized:
            entry.realizing_cases += 1
            entry.realizations += realized
    return stats


def load(storage_dir: str) -> Dict[str, CompletionStats]:
    return aggregate(iter_test_cases(storage_dir))


def coverage_delta(
    baseline: Optional[CompletionStats], crosshair: Optional[CompletionStats]
) -> Dict[str, List[int]]:
    """Lines the CrossHair arm reached that the baseline arm never did."""
    if crosshair is None:
        return {}
    base = baseline.covered_lines if baseline else {}
    delta: Dict[str, List[int]] = {}
    for path, lines in crosshair.covered_lines.items():
        extra = sorted(set(lines) - set(base.get(path, ())))
        if extra:
            delta[path] = extra
    return delta


#: Forwarded Hypothesis exceptions that are ordinary control flow, not drift.
#: ``assume()`` raises UnsatisfiedAssumption to reject an input, so the
#: provider forwards it on every filtered iteration of a perfectly healthy test.
BENIGN_FORWARDED_EXCEPTIONS = frozenset({"UnsatisfiedAssumption"})

_FORWARDED_PREFIX = "forwarded hypothesis "


def api_drift_completions(stats: CompletionStats) -> Dict[str, int]:
    """Forwarded Hypothesis exceptions that suggest the provider is out of step.

    Excludes the benign ones: a test that filters its inputs is not evidence of
    drift, and treating it as such would flag most real projects.
    """
    drift = {}
    for text, count in stats.counts.items():
        if not text.startswith(_FORWARDED_PREFIX):
            continue
        name = text[len(_FORWARDED_PREFIX) :].split(" ")[0]
        if name not in BENIGN_FORWARDED_EXCEPTIONS:
            drift[text] = count
    return drift


def is_benign(completion: str) -> bool:
    """Whether a completion represents healthy behavior."""
    if completion == "completed normally" or completion.startswith("raised "):
        return True
    if completion.startswith(_FORWARDED_PREFIX):
        name = completion[len(_FORWARDED_PREFIX) :].split(" ")[0]
        return name in BENIGN_FORWARDED_EXCEPTIONS
    return False


def search_is_degraded(stats: CompletionStats) -> bool:
    """Whether realization has undercut the solver enough to distrust a pass.

    Realization is invisible in the completion histogram: an iteration whose
    values were all made concrete still reports ``completed normally``.
    """
    return stats.realization_rate >= REALIZATION_UNRELIABLE_RATE


def nondeterminism_rate(stats: CompletionStats) -> float:
    """Share of solver iterations discarded for detected nondeterminism.

    CrossHair's determinism check is deep: a memoization cache or other
    internal state that never changes observable behavior is enough to trip
    it, so a nonzero rate is common in ordinary code and is not by itself a
    CrossHair defect.
    """
    if not stats.crosshair_cases:
        return 0.0
    hits = sum(
        n for text, n in stats.counts.items() if "non determinism" in text.lower()
    )
    return hits / stats.crosshair_cases
