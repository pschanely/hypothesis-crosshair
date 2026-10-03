"""Checks provisioning, including the properties the safety model rests on.

Provisioning is the only stage allowed to reach the network, and the only one
that runs a project's own build code. Both are asserted here rather than left
to review.
"""

from typing import Dict, List, Optional, Sequence

from discovery.harness import MAX_REPAIRS
from discovery.provision import Provisioned, provision, venv_python
from discovery.sandbox import ExecResult, Limits, Sandbox


class FakeSandbox(Sandbox):
    """Answers each command from a script, and records how it was called."""

    def __init__(self, answers: Sequence[ExecResult]):
        self.answers = list(answers)
        self.calls: List[dict] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        env: Optional[Dict[str, str]] = None,
        network: bool = False,
        limits: Optional[Limits] = None,
    ) -> ExecResult:
        self.calls.append(
            {"argv": list(argv), "cwd": cwd, "env": dict(env or {}), "network": network}
        )
        if not self.answers:
            raise AssertionError(f"unscripted command: {list(argv)}")
        return self.answers.pop(0)


def ok(stdout: str = "") -> ExecResult:
    return ExecResult(returncode=0, stdout=stdout, stderr="", duration=0.0)


def fail(stdout: str = "", stderr: str = "", code: int = 1) -> ExecResult:
    return ExecResult(returncode=code, stdout=stdout, stderr=stderr, duration=0.0)


COLLECTED = "41 tests collected in 0.4s"

BENCHMARK_FAILURE = (
    "ERROR: usage: pytest [options]\n"
    "pytest: error: unrecognized arguments: --benchmark-sort=fullname\n"
)

DESELECTED = "7 deselected in 0.2s\n"


def run_provision(answers, **kw):
    sandbox = FakeSandbox(answers)
    result = provision(sandbox, "/proj", "/plugin", venv_dir="/proj/.venv", **kw)
    return result, sandbox


def test_a_clean_project_provisions_without_repairs():
    result, sandbox = run_provision([ok(), ok(), ok(COLLECTED)])
    assert result.ready
    assert result.collected == 41
    assert result.repairs == []
    assert result.python == venv_python("/proj/.venv")
    assert "41 tests collected" in result.describe()


def test_the_project_and_the_plugin_are_both_installed():
    _, sandbox = run_provision([ok(), ok(), ok(COLLECTED)])
    install = sandbox.calls[1]["argv"]
    assert "-e" in install and "/proj" in install and "/plugin" in install
    assert "pytest" in install and "hypothesis" in install


def test_installing_may_use_the_network_and_collecting_may_not():
    """The two-phase rule the safety model depends on."""
    _, sandbox = run_provision([ok(), ok(), ok(COLLECTED)])
    kinds = [(c["argv"][0], c["network"]) for c in sandbox.calls]
    assert kinds[0][1] is True, "creating the environment fetches an interpreter"
    assert kinds[1][1] is True, "installing fetches packages"
    assert kinds[2][1] is False, "collection imports the project and must not"


def test_a_failed_environment_stops_before_installing():
    result, sandbox = run_provision([fail(stderr="no such python")])
    assert not result.ready
    assert "virtual environment" in result.error
    assert len(sandbox.calls) == 1


def test_a_failed_install_stops_before_collecting():
    result, sandbox = run_provision([ok(), fail(stderr="resolution impossible")])
    assert not result.ready
    assert "install failed" in result.error
    assert "resolution impossible" in result.error
    assert len(sandbox.calls) == 2


def test_a_project_that_installs_but_cannot_collect_is_not_provisioned():
    """Collection is the proof; without it every later test reports no result."""
    result, _ = run_provision([ok(), ok(), fail(stdout="E ImportError: boom")])
    assert not result.ready
    assert "collection failed" in result.error


def test_a_diagnosed_failure_is_repaired_and_collection_retried():
    result, sandbox = run_provision(
        [ok(), ok(), fail(stdout=BENCHMARK_FAILURE), ok(), ok(COLLECTED)]
    )
    assert result.ready
    assert result.repairs == ["install-configured-plugins"]
    assert "pytest-benchmark" in sandbox.calls[3]["argv"]
    assert result.collected == 41


def test_a_repair_that_only_changes_arguments_installs_nothing():
    result, sandbox = run_provision(
        [ok(), ok(), fail(stdout=DESELECTED), ok(COLLECTED)]
    )
    assert result.ready
    assert result.pytest_args == ["-m", ""]
    assert len(sandbox.calls) == 4, "no install round for an argument-only repair"


def test_a_repairs_arguments_are_carried_into_the_retry_and_the_result():
    result, sandbox = run_provision(
        [ok(), ok(), fail(stdout=DESELECTED), ok(COLLECTED)]
    )
    retry = sandbox.calls[3]["argv"]
    assert retry[-2:] == ["-m", ""]
    assert result.pytest_args == ["-m", ""]


def test_an_unfamiliar_collection_failure_is_not_repaired():
    result, sandbox = run_provision([ok(), ok(), fail(stdout="E   AssertionError")])
    assert not result.ready
    assert result.repairs == []
    assert len(sandbox.calls) == 3


def test_a_repair_whose_install_fails_names_it():
    result, _ = run_provision(
        [ok(), ok(), fail(stdout=BENCHMARK_FAILURE), fail(stderr="no such package")]
    )
    assert not result.ready
    assert "install-configured-plugins" in result.error
    assert "pytest-benchmark" in result.error


def test_collecting_nothing_is_not_a_broken_harness():
    """pytest exits 5 for an empty selection, which is a real answer."""
    result, _ = run_provision([ok(), ok(), fail(stdout="no tests ran", code=5)])
    assert result.ready
    assert result.collected == 0


def test_repairs_stop_rather_than_looping():
    answers = [ok(), ok()]
    for _ in range(MAX_REPAIRS + 2):
        answers += [fail(stdout=BENCHMARK_FAILURE), ok()]
    result, _ = run_provision(answers)
    assert not result.ready
    assert len(result.repairs) <= MAX_REPAIRS


def test_extra_packages_are_installed_with_the_rest():
    _, sandbox = run_provision(
        [ok(), ok(), ok(COLLECTED)], extra_packages=["pytest-xdist"]
    )
    assert "pytest-xdist" in sandbox.calls[1]["argv"]


def test_describe_reports_a_failure_rather_than_a_count():
    result = Provisioned(project="/a/b", error="install failed: x")
    assert result.describe() == "b: not provisioned -- install failed: x"
