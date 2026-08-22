"""Collection must never silently hand back a truncated inventory."""

import json
import os

import pytest
from discovery.runner import CollectionFailed, EnvSpec, Runner
from discovery.sandbox import ExecResult


class FakeSandbox:
    def __init__(self, returncode, report=None):
        self.returncode = returncode
        self.report = report

    def run(self, argv, *, cwd, env=None, network=False, limits=None):
        if self.report is not None:
            with open(env["HCD_REPORT"], "w") as handle:
                json.dump(self.report, handle)
        return ExecResult(self.returncode, "", "boom", 0.1)


def runner_for(tmp_path, returncode, report=None):
    return Runner(
        FakeSandbox(returncode, report),
        project_dir=str(tmp_path),
        run_root=str(tmp_path),
    )


ENV = EnvSpec("ch", ["python"])


def test_an_empty_inventory_from_a_failing_run_is_an_error(tmp_path):
    runner = runner_for(tmp_path, 2, {"hypothesis_nodeids": [], "results": []})
    with pytest.raises(CollectionFailed, match="rc=2"):
        runner.collect(ENV)


def test_a_missing_report_is_an_error(tmp_path):
    runner = runner_for(tmp_path, 1, None)
    with pytest.raises(CollectionFailed, match="no report"):
        runner.collect(ENV)


def test_a_project_with_no_hypothesis_tests_is_not_an_error(tmp_path):
    runner = runner_for(tmp_path, 5, {"hypothesis_nodeids": [], "results": []})
    assert runner.collect(ENV) == []


def test_a_nonzero_exit_is_tolerated_when_the_inventory_is_populated(tmp_path):
    """Real suites exit nonzero on collection for unrelated reasons.

    packaging turns warnings into errors, so its collect-only run exits 2 while
    still enumerating every test.
    """
    runner = runner_for(
        tmp_path, 2, {"hypothesis_nodeids": ["t.py::test_a"], "results": []}
    )
    assert runner.collect(ENV) == ["t.py::test_a"]
