"""Path-search progress read from CrossHair's own pathing oracle.

The counters come from the provider rather than from Hypothesis observability,
so unlike the completion telemetry they need no tracer and are available in the
verdict tier.
"""

import json
import os
import subprocess
import sys
import textwrap

import pytest
from discovery import telemetry
from discovery.model import SearchProgress
from discovery.runner import _read_search

PLUGIN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "discovery"
)


def test_stalled_requires_the_search_to_have_run():
    """A short run has not had the chance to stall."""
    young = SearchProgress(
        code_locations=3, iters_since_discovery=11, solver_iterations=2
    )
    assert not young.stalled(threshold=10)


def test_stalled_when_discovery_stops():
    stuck = SearchProgress(
        code_locations=9, iters_since_discovery=290, solver_iterations=299
    )
    assert stuck.stalled(threshold=10)


def test_still_discovering_is_not_stalled():
    live = SearchProgress(
        code_locations=43, iters_since_discovery=1, solver_iterations=60
    )
    assert not live.stalled(threshold=10)


def test_discovery_rate_of_an_unrun_search_is_zero():
    assert SearchProgress().discovery_rate == 0.0


def test_stalled_searches_selects_only_stalled_entries():
    progress = {
        "t::stuck": SearchProgress(9, 290, 299),
        "t::live": SearchProgress(43, 1, 60),
    }
    assert list(telemetry.stalled_searches(progress)) == ["t::stuck"]


def test_read_search_tolerates_a_malformed_entry(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "search": {
                    "t::good": {
                        "code_locations": 4,
                        "iters_since_discovery": 2,
                        "solver_iterations": 7,
                    },
                    "t::bad": "not a mapping",
                }
            }
        )
    )
    found = _read_search(str(report))
    assert list(found) == ["t::good"]
    assert found["t::good"].code_locations == 4


def test_read_search_of_a_report_without_the_key(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"results": []}))
    assert _read_search(str(report)) == {}


def test_read_search_of_a_missing_report():
    assert _read_search("/nonexistent/report.json") == {}


def test_probe_reports_progress_from_a_real_solver_run(tmp_path):
    """End to end: the injected probe must actually populate the report.

    Asserted against a live CrossHair run rather than a stub, because the
    probe reads private provider attributes that a stub cannot vouch for.
    """
    pytest.importorskip("hypothesis_crosshair_provider")
    (tmp_path / "test_probe_sample.py").write_text(
        textwrap.dedent(
            """
            from hypothesis import given, strategies as st

            def stairs(n):
                t = 0
                if n > 10:
                    t += 1
                    if n > 100:
                        t += 1
                return t

            @given(st.integers())
            def test_stairs(n):
                assert stairs(n) <= 2
            """
        )
    )
    report = tmp_path / "report.json"
    env = dict(
        os.environ,
        HCD_REPORT=str(report),
        HCD_BACKEND="crosshair",
        HCD_MAX_EXAMPLES="30",
        HCD_DEADLINE="none",
        HCD_ONLY_HYPOTHESIS="1",
        PYTHONPATH=PLUGIN_DIR,
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "test_probe_sample.py",
            "-p",
            "_injected_plugin",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert report.exists(), proc.stdout + proc.stderr
    search = json.loads(report.read_text()).get("search") or {}
    entry = search.get("test_probe_sample.py::test_stairs")
    assert entry is not None, search
    assert entry["solver_iterations"] > 0
    assert entry["code_locations"] > 0


def test_probe_is_not_installed_for_the_baseline_backend(tmp_path):
    """The baseline arm must run with the target's own machinery untouched."""
    import importlib

    sys.path.insert(0, PLUGIN_DIR)
    try:
        os.environ["HCD_BACKEND"] = "hypothesis"
        plugin = importlib.reload(importlib.import_module("_injected_plugin"))
        provider = pytest.importorskip(
            "hypothesis_crosshair_provider.crosshair_provider"
        )
        before = provider.CrossHairPrimitiveProvider.per_test_case_context_manager
        plugin._install_search_probe()
        assert (
            provider.CrossHairPrimitiveProvider.per_test_case_context_manager is before
        )
    finally:
        os.environ.pop("HCD_BACKEND", None)
        sys.path.remove(PLUGIN_DIR)
