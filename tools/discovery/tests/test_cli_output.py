"""Checks that --json prints JSON and nothing else.

Progress belongs on stderr. A per-test run prints a line as each test
finishes, and sending one of those to stdout makes the whole report
unparseable for whatever consumes it.
"""

import json
import sys

from discovery.cli import main

GIVEN = """\
from hypothesis import given, strategies as st

@given(st.integers())
def test_a(n):
    assert n == n
"""


def tiny_project(tmp_path):
    (tmp_path / "test_a.py").write_text(GIVEN)
    return [
        "--project",
        str(tmp_path),
        "--crosshair-python",
        sys.executable,
        "--sandbox",
        "local",
        "--run-root",
        str(tmp_path / "runs"),
        "--no-telemetry-tier",
        "--baseline-seeds",
        "1",
        "--baseline-max-examples",
        "1",
        "--crosshair-max-examples",
        "1",
        "--crosshair-timeout",
        "30",
    ]


def test_json_output_is_parseable(tmp_path, capsys):
    assert main(tiny_project(tmp_path) + ["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "classifications" in payload


def test_per_test_progress_does_not_reach_stdout(tmp_path, capsys):
    """The per-test loop prints a line per test; none may land in the report."""
    assert main(tiny_project(tmp_path) + ["--per-test", "--json"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert "classifications" in payload
    assert "::test_a ->" in captured.err, "progress must still be reported"
