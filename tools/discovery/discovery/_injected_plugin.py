"""Pytest plugin injected into the target project's environment.

Runs inside the project under test, with no dependency on the rest of the
discovery package. It forces the Hypothesis backend and settings onto every
collected Hypothesis test, and writes a machine-readable report of outcomes.

Configured entirely through ``HCD_*`` environment variables so that no
command-line surface of the target project is disturbed.
"""

import json
import os
import re
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

_REPORT_PATH = os.environ.get("HCD_REPORT")
_BACKEND = os.environ.get("HCD_BACKEND")
_MAX_EXAMPLES = os.environ.get("HCD_MAX_EXAMPLES")
_DEADLINE = os.environ.get("HCD_DEADLINE")
_DB_DIR = os.environ.get("HCD_DB_DIR")
_ONLY_HYPOTHESIS = os.environ.get("HCD_ONLY_HYPOTHESIS") == "1"

_EXAMPLE_RE = re.compile(
    r"(?:Falsifying example|Failing test case):\s*(.*?\n\s*\)\s*$)",
    re.DOTALL | re.MULTILINE,
)
_EXC_RE = re.compile(r"^E\s+([A-Za-z_][\w.]*(?:Error|Exception|Warning|Exit))\b", re.M)

_results: Dict[str, Dict[str, Any]] = {}
_hypothesis_nodeids: List[str] = []
_forced_nodeids: List[str] = []
_search: Dict[str, Dict[str, int]] = {}
_current_nodeid: Optional[str] = None


def _is_hypothesis_test(func: Any) -> bool:
    try:
        from hypothesis.internal.detection import is_hypothesis_test

        return bool(is_hypothesis_test(func))
    except Exception:
        return hasattr(func, "hypothesis")


def _unwrap(item: Any) -> Optional[Any]:
    func = getattr(item, "obj", None)
    if func is None:
        return None
    return getattr(func, "__func__", func)


def pytest_collection_modifyitems(session, config, items):
    """Force our settings onto Hypothesis tests, overriding explicit ``@settings``.

    An explicit ``@settings`` decorator on the test wins over a registered
    profile, so a profile alone would silently leave the default backend in
    place while the run is recorded as a CrossHair result.
    """
    try:
        from hypothesis import settings as hyp_settings
    except Exception:
        return

    database = None
    if _DB_DIR:
        from hypothesis.database import DirectoryBasedExampleDatabase

        database = DirectoryBasedExampleDatabase(_DB_DIR)

    overrides: Dict[str, Any] = {"database": database}
    if _BACKEND:
        overrides["backend"] = _BACKEND
    if _MAX_EXAMPLES:
        overrides["max_examples"] = int(_MAX_EXAMPLES)
    overrides["deadline"] = None if _DEADLINE in (None, "", "none") else int(_DEADLINE)

    keep = []
    for item in items:
        func = _unwrap(item)
        if func is None or not _is_hypothesis_test(func):
            if not _ONLY_HYPOTHESIS:
                keep.append(item)
            continue
        _hypothesis_nodeids.append(item.nodeid)
        parent = getattr(func, "_hypothesis_internal_use_settings", None)
        try:
            merged = (
                hyp_settings(parent, **overrides)
                if parent is not None
                else hyp_settings(**overrides)
            )
            func._hypothesis_internal_use_settings = merged
            _forced_nodeids.append(item.nodeid)
        except Exception as exc:  # a strategy-level settings conflict, not our bug
            _results[item.nodeid] = {
                "nodeid": item.nodeid,
                "outcome": "error",
                "message": f"could not apply settings override: {exc!r}",
            }
        keep.append(item)
    items[:] = keep


def _strip_report_prefix(text: str) -> str:
    return "\n".join(
        line[2:] if line.startswith("E ") else line for line in text.splitlines()
    )


def _extract_example(text: str) -> Optional[str]:
    match = _EXAMPLE_RE.search(_strip_report_prefix(text))
    if not match:
        return None
    lines = [line.rstrip() for line in match.group(1).strip().splitlines()]
    continuations = [line for line in lines[1:] if line.strip()]
    if continuations:
        indent = min(len(line) - len(line.lstrip()) for line in continuations)
        lines = lines[:1] + [
            line[indent:] if line.strip() else line for line in lines[1:]
        ]
    return "\n".join(lines)


def _exception_type(text: str, message: str) -> Optional[str]:
    """Name the raised exception.

    pytest reports a bare ``assert`` statement by its source rather than by
    exception class, so the class name has to be recovered separately.
    """
    found = _EXC_RE.findall(text)
    if found:
        return found[-1]
    if message.startswith("assert"):
        return "AssertionError"
    head = message.split(":", 1)[0].strip()
    return head or None


def pytest_runtest_logreport(report):
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        existing = _results.get(report.nodeid, {})
        if existing.get("outcome") in ("failed", "error"):
            return
        text = report.longreprtext or ""
        entry: Dict[str, Any] = {
            "nodeid": report.nodeid,
            "outcome": (
                "error"
                if report.when == "setup" and report.outcome == "failed"
                else report.outcome
            ),
            "duration": getattr(report, "duration", 0.0),
        }
        if text:
            entry["longrepr"] = text[-8000:]
            entry["falsifying_example"] = _extract_example(text)
            crash = getattr(report.longrepr, "reprcrash", None)
            message = getattr(crash, "message", None)
            if message:
                entry["message"] = message[:2000]
                entry["exception_type"] = _exception_type(text, message)
        _results[report.nodeid] = entry


def _sample_search(provider: Any) -> None:
    """Read CrossHair's own path-search counters for the running test.

    ``CoveragePathingOracle`` tracks the distinct code locations at which the
    solver has forked (``visits``) and how many iterations have passed since
    the last new one (``iters_since_discovery``). Reading them costs nothing
    and needs neither observability nor a tracer, so unlike the completion
    telemetry this is safe to collect in the verdict tier too.
    """
    global _current_nodeid
    try:
        nodeid = _current_nodeid
        if not nodeid:
            return
        oracle = getattr(provider, "constrained_oracle", None)
        inner = getattr(oracle, "inner_oracle", None)
        visits = getattr(inner, "visits", None)
        since = getattr(inner, "iters_since_discovery", None)
        if visits is None or since is None:
            return
        entry = _search.setdefault(
            nodeid,
            {"code_locations": 0, "iters_since_discovery": 0, "solver_iterations": 0},
        )
        entry["code_locations"] = max(entry["code_locations"], len(visits))
        entry["iters_since_discovery"] = int(since)
        entry["solver_iterations"] += 1
    except Exception:
        # Instrumentation must never be able to fail a target project's run.
        pass


def _install_search_probe() -> None:
    if _BACKEND != "crosshair":
        return
    try:
        from hypothesis_crosshair_provider import crosshair_provider as provider_mod

        cls = provider_mod.CrossHairPrimitiveProvider
    except Exception:
        return
    if getattr(cls, "_hcd_probed", False):
        return
    original = cls.per_test_case_context_manager

    @contextmanager
    def probed(self):
        try:
            with original(self):
                yield
        finally:
            _sample_search(self)

    try:
        cls.per_test_case_context_manager = probed
        cls._hcd_probed = True
    except Exception:
        pass


def pytest_configure(config):
    _install_search_probe()


def pytest_runtest_logstart(nodeid, location):
    global _current_nodeid
    _current_nodeid = nodeid


def pytest_runtest_logfinish(nodeid, location):
    global _current_nodeid
    _current_nodeid = None


def pytest_sessionfinish(session, exitstatus):
    if not _REPORT_PATH:
        return
    payload = {
        "exitstatus": int(exitstatus),
        "results": list(_results.values()),
        "hypothesis_nodeids": _hypothesis_nodeids,
        "forced_nodeids": _forced_nodeids,
        "search": _search,
    }
    with open(_REPORT_PATH, "w") as out:
        json.dump(payload, out)
