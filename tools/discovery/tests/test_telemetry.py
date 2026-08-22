import json
import os

from discovery import telemetry
from discovery.model import CompletionStats

CH = "during generate phase, using backend='crosshair'"


def row(name, how, completion=None, coverage=None):
    entry = {"property": name, "how_generated": how, "metadata": {}}
    if completion is not None:
        entry["metadata"]["backend"] = {"completion": completion}
    if coverage:
        entry["coverage"] = coverage
    return entry


def test_only_solver_cases_count_towards_completions():
    stats = telemetry.aggregate(
        [
            row("t", CH, "completed normally"),
            row("t", CH, "ignored due to path timeout"),
            row("t", "during shrink phase"),
            row("t", "during generate phase"),
        ]
    )["t"]
    assert stats.crosshair_cases == 2
    assert stats.counts == {
        "completed normally": 1,
        "ignored due to path timeout": 1,
    }


def test_productivity_counts_iterations_that_reached_user_code():
    stats = telemetry.aggregate(
        [
            row("t", CH, "completed normally"),
            row("t", CH, "raised AssertionError exception"),
            row("t", CH, "ignored due to proxy intolerance"),
            row("t", CH, "ignored due to path timeout"),
        ]
    )["t"]
    assert stats.productive == 2
    assert stats.productivity == 0.5
    assert stats.dominant_ignore_reason() in (
        "ignored due to proxy intolerance",
        "ignored due to path timeout",
    )


def test_coverage_merges_across_cases_including_non_solver_ones():
    stats = telemetry.aggregate(
        [
            row("t", CH, "completed normally", {"a.py": [1, 2]}),
            row("t", "during shrink phase", None, {"a.py": [2, 3]}),
        ]
    )["t"]
    assert stats.covered_lines == {"a.py": [1, 2, 3]}


def test_coverage_delta_reports_lines_only_the_solver_reached():
    base = CompletionStats(covered_lines={"a.py": [1, 2]})
    ch = CompletionStats(covered_lines={"a.py": [1, 2, 7], "b.py": [3]})
    assert telemetry.coverage_delta(base, ch) == {"a.py": [7], "b.py": [3]}
    assert telemetry.coverage_delta(base, None) == {}


def test_nondeterminism_rate_is_a_share_of_solver_iterations():
    stats = telemetry.aggregate(
        [row("t", CH, "ignored due to non determinism detected")] * 3
        + [row("t", CH, "completed normally")]
    )["t"]
    assert telemetry.nondeterminism_rate(stats) == 0.75
    assert telemetry.nondeterminism_rate(CompletionStats()) == 0.0


def test_load_reads_observability_files(tmp_path):
    observed = tmp_path / "observed"
    observed.mkdir()
    path = observed / "2026-01-01_testcases.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(row("t", CH, "completed normally")),
                "",
                "{ not json",
                json.dumps(row("t", CH, "completed normally")),
            ]
        )
    )
    stats = telemetry.load(str(tmp_path))
    assert stats["t"].crosshair_cases == 2


def test_load_of_a_missing_directory_is_empty():
    assert telemetry.load("/nonexistent/path") == {}
