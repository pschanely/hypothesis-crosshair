"""Parsing of Hypothesis observability output (tier-B runs only).

Telemetry steers the loop and feeds the CrossHair-defect stream. It never
decides a verdict: observability realizes symbolic draws and perturbs the
search, so classification reads tier-A runs exclusively.
"""

import json
import os
from typing import Dict, Iterable, Iterator, List, Optional

from .model import CompletionStats

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
