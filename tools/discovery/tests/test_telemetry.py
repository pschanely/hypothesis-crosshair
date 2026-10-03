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


def test_assume_filtering_is_not_treated_as_api_drift():
    """assume() raises UnsatisfiedAssumption on every rejected input.

    Counting that as drift would flag most real projects: pypa/packaging's
    version suite forwards it on 1% of solver iterations while being healthy.
    """
    stats = telemetry.aggregate(
        [row("t", CH, "forwarded hypothesis UnsatisfiedAssumption exception")] * 46
        + [row("t", CH, "completed normally")] * 4573
    )["t"]
    assert telemetry.api_drift_completions(stats) == {}
    assert telemetry.is_benign("forwarded hypothesis UnsatisfiedAssumption exception")


def test_other_forwarded_hypothesis_exceptions_are_reported_as_drift():
    stats = telemetry.aggregate(
        [row("t", CH, "forwarded hypothesis InvalidArgument exception")] * 3
        + [row("t", CH, "forwarded hypothesis UnsatisfiedAssumption exception")]
    )["t"]
    assert telemetry.api_drift_completions(stats) == {
        "forwarded hypothesis InvalidArgument exception": 3
    }
    assert not telemetry.is_benign("forwarded hypothesis InvalidArgument exception")


def test_ignored_iterations_are_not_benign():
    assert not telemetry.is_benign("ignored due to proxy intolerance")
    assert telemetry.is_benign("raised AssertionError exception")


def realizing_row(name, n=1):
    entry = row(name, CH, "completed normally")
    entry["metadata"]["backend"]["messages"] = [
        f"SMT realized symbolic str_{i:02d} to 'x'" for i in range(n)
    ]
    return entry


def test_realization_is_counted_even_when_iterations_complete_normally():
    """Realization is invisible in the completion histogram.

    An iteration whose values were all made concrete still reports
    'completed normally', so productivity alone cannot show that the solver
    actually steered the search.
    """
    stats = telemetry.aggregate(
        [realizing_row("t", 3)] * 9 + [row("t", CH, "completed normally")]
    )["t"]
    assert stats.counts == {"completed normally": 10}
    assert stats.productivity == 1.0
    assert stats.realizing_cases == 9
    assert stats.realizations == 27
    assert stats.realization_rate == 0.9
    assert stats.searched_symbolically == 1


def test_a_mostly_realized_run_is_flagged_as_degraded():
    degraded = telemetry.aggregate(
        [realizing_row("t")] * 6 + [row("t", CH, "completed normally")] * 4
    )["t"]
    healthy = telemetry.aggregate(
        [realizing_row("t")] * 2 + [row("t", CH, "completed normally")] * 8
    )["t"]
    assert telemetry.search_is_degraded(degraded)
    assert not telemetry.search_is_degraded(healthy)


def test_a_run_with_no_solver_cases_is_not_reported_as_degraded():
    assert not telemetry.search_is_degraded(CompletionStats())


def test_solver_cases_contribute_no_coverage():
    """Hypothesis records coverage: null under the CrossHair backend.

    Its line tracer cannot run alongside CrossHair's, so coverage from a solver
    run describes the concrete phases only and must not be read as solver reach.
    """
    solver = row("t", CH, "completed normally")
    solver["coverage"] = None
    concrete = row("t", "during shrink phase", None, {"a.py": [1, 2]})
    stats = telemetry.aggregate([solver, concrete])["t"]
    assert stats.crosshair_cases == 1
    assert stats.covered_lines == {"a.py": [1, 2]}


def _crosshair_row(name, messages):
    return {
        "property": name,
        "how_generated": CH,
        "metadata": {
            "backend": {"completion": "completed normally", "messages": messages}
        },
    }


def test_an_unsupported_construct_is_counted_per_reason():
    stats = telemetry.aggregate(
        [
            _crosshair_row(
                "t1", ["Unsupported symbolic regex: \\s* POSSESSIVE_REPEAT"]
            ),
            _crosshair_row(
                "t1", ["Unsupported symbolic regex: \\s* POSSESSIVE_REPEAT"]
            ),
            _crosshair_row(
                "t2", ["Unsupported symbolic regex: unsupported subpattern args"]
            ),
        ]
    )
    assert telemetry.unsupported_constructs(stats) == {
        "\\s* POSSESSIVE_REPEAT": 2,
        "unsupported subpattern args": 1,
    }


def test_a_fallback_is_invisible_in_the_completion_histogram():
    """The whole point: these iterations report completing normally."""
    stats = telemetry.aggregate(
        [_crosshair_row("t1", ["Unsupported symbolic regex: \\s* POSSESSIVE_REPEAT"])]
    )
    entry = stats["t1"]
    assert entry.counts == {"completed normally": 1}
    assert entry.fell_back_to_concrete == 1


def test_realization_sites_are_aggregated():
    stats = telemetry.aggregate(
        [
            _crosshair_row("t1", ["Realized at (t a.py:9) (__init__ version.py:418)"]),
            _crosshair_row("t2", ["Realized at (t a.py:9) (__init__ version.py:418)"]),
        ]
    )
    assert telemetry.realization_sites(stats) == {
        "(t a.py:9) (__init__ version.py:418)": 2
    }


def test_a_run_with_no_fallbacks_reports_none():
    stats = telemetry.aggregate([_crosshair_row("t1", ["SMT chose: x > 0"])])
    assert telemetry.unsupported_constructs(stats) == {}
    assert stats["t1"].fell_back_to_concrete == 0


def _stats(**kw):
    stats = CompletionStats()
    for key, value in kw.items():
        setattr(stats, key, value)
    return stats


def test_nondeterminism_becomes_a_clue_not_a_verdict():
    stats = {
        "t.py::test_a": _stats(
            counts={"ignored due to non determinism detected": 9},
            crosshair_cases=10,
        )
    }
    found = telemetry.clues_from(stats)
    assert len(found) == 1
    assert "nondeterminism" in found[0].observation
    assert found[0].follow_up, "a clue must say what would settle it"


def test_an_exhaustion_claim_becomes_a_clue():
    stats = {
        "t.py::test_a": _stats(
            counts={"exhausted all paths - nothing else to do": 2}, crosshair_cases=2
        )
    }
    found = telemetry.clues_from(stats)
    assert any("exhausted" in c.observation for c in found)


def test_a_degraded_search_only_clues_where_it_undercuts_the_verdict():
    """A pass that CrossHair reached honestly needs no caveat."""
    stats = {
        "t.py::test_a": _stats(
            counts={"completed normally": 10},
            crosshair_cases=10,
            realizing_cases=10,
            realizations=40,
        )
    }
    assert telemetry.clues_from(stats, {"t.py::test_a": "trophy_candidate"}) == []
    found = telemetry.clues_from(stats, {"t.py::test_a": "crosshair_false_negative"})
    assert len(found) == 1
    assert "realized" in found[0].observation
    assert "larger budget" in found[0].follow_up


def test_a_healthy_run_produces_no_clues():
    stats = {
        "t.py::test_a": _stats(counts={"completed normally": 10}, crosshair_cases=10)
    }
    assert telemetry.clues_from(stats, {"t.py::test_a": "no_signal"}) == []


def test_a_tier_with_no_solver_cases_produces_no_clues():
    """Counts can survive from a run where the solver never produced a case,
    and a clue read off those describes nothing that happened."""
    assert telemetry.clues_from({"t.py::test_a": CompletionStats()}) == []
    stranded = _stats(
        counts={"exhausted all paths - nothing else to do": 4},
        crosshair_cases=0,
    )
    assert telemetry.clues_from({"t.py::test_a": stranded}) == []


def test_the_report_shows_clues_despite_an_observer_effect_entry():
    """A node id can carry two classifications, and the annotation is not the
    verdict a clue should be read against."""
    from discovery.cli import _clue_section
    from discovery.model import Arm, Classification, Outcome, RunResult, Tier, Verdict
    from discovery.pipeline import PipelineReport

    nodeid = "t.py::test_a"
    report = PipelineReport(project_dir="/proj")
    report.classifications = [
        Classification(
            nodeid=nodeid,
            verdict=Verdict.CROSSHAIR_FALSE_NEGATIVE,
            baseline=Outcome.FAILED,
            crosshair=Outcome.PASSED,
        ),
        Classification(
            nodeid=nodeid,
            verdict=Verdict.OBSERVER_EFFECT,
            baseline=Outcome.FAILED,
            crosshair=Outcome.PASSED,
        ),
    ]
    tier_b = RunResult(Arm.CROSSHAIR, Tier.B_TELEMETRY, 0, 1.0)
    tier_b.telemetry["test_a"] = _stats(
        counts={"completed normally": 10},
        crosshair_cases=10,
        realizing_cases=10,
        realizations=30,
    )
    report.telemetry_run = tier_b

    text = "\n".join(_clue_section(report))
    assert "never verdicts" in text
    assert "crosshair_false_negative" in text
