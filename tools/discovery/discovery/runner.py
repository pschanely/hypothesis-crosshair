"""Execution of a single pytest invocation for one arm and tier."""

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from . import telemetry
from .model import Arm, CaseOutcome, Outcome, RunResult, Tier
from .sandbox import Limits, Sandbox

_PLUGIN_SOURCE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "_injected_plugin.py"
)
_PLUGIN_MODULE = "_injected_plugin"

_INTERNAL_ERROR_RE = re.compile(
    r"CrossHairInternal|INTERNALERROR|Fatal Python error|Segmentation fault"
)

#: pytest's exit code for an internal error.
_PYTEST_INTERNAL_ERROR = 3

#: Files that make a directory the root of its own pytest configuration.
_CONFIG_FILES = ("pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg")


@dataclass
class EnvSpec:
    """How to launch pytest for one arm of the differential.

    The validation arm must point at an environment where
    ``hypothesis-crosshair`` is not installed at all: the plugin registers an
    entry point and CrossHair patches builtins on import, so selecting a
    different backend in the same environment is not a clean room.
    """

    label: str
    python_argv: Sequence[str]
    has_crosshair: bool = True

    @property
    def pytest_argv(self) -> List[str]:
        return list(self.python_argv) + ["-m", "pytest"]


@dataclass
class RunSpec:
    arm: Arm
    tier: Tier
    env: EnvSpec
    nodeids: Sequence[str] = ()
    backend: Optional[str] = None
    max_examples: int = 100
    seed: Optional[int] = None
    database_dir: Optional[str] = None
    collect_only: bool = False
    extra_args: Sequence[str] = ()
    limits: Limits = field(default_factory=Limits)


class Runner:
    def __init__(self, sandbox: Sandbox, project_dir: str, run_root: str) -> None:
        self.sandbox = sandbox
        self.project_dir = project_dir
        self.run_root = run_root
        self._plugin_dir = os.path.join(run_root, "_plugin")
        os.makedirs(self._plugin_dir, exist_ok=True)
        shutil.copy(
            _PLUGIN_SOURCE, os.path.join(self._plugin_dir, "_injected_plugin.py")
        )

    def _paths(self, spec: RunSpec) -> Dict[str, str]:
        slug = f"{spec.arm.value}-{spec.tier.value}"
        if spec.seed is not None:
            slug += f"-seed{spec.seed}"
        if spec.collect_only:
            slug += "-collect"
        base = os.path.join(self.run_root, slug)
        os.makedirs(base, exist_ok=True)
        return {
            "base": base,
            "report": os.path.join(base, "report.json"),
            "storage": os.path.join(base, "hypothesis-home"),
        }

    def build_env(self, spec: RunSpec, paths: Dict[str, str]) -> Dict[str, str]:
        env = {
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": self._plugin_dir,
            "HYPOTHESIS_STORAGE_DIRECTORY": paths["storage"],
            "HCD_REPORT": paths["report"],
            "HCD_MAX_EXAMPLES": str(spec.max_examples),
            "HCD_DEADLINE": "none",
            "HCD_ONLY_HYPOTHESIS": "1",
        }
        if spec.backend:
            env["HCD_BACKEND"] = spec.backend
        if spec.database_dir:
            env["HCD_DB_DIR"] = spec.database_dir
        if spec.tier is Tier.B_TELEMETRY:
            env["HYPOTHESIS_EXPERIMENTAL_OBSERVABILITY"] = "1"
        return env

    def _config_args(self) -> List[str]:
        """Keep pytest from adopting a config file above the project directory.

        pytest walks upward for both its ini file and its conftest files, so a
        project would otherwise inherit settings and collection hooks from
        whatever happens to sit above it on disk.
        """
        args = ["--confcutdir", self.project_dir]
        if any(
            os.path.exists(os.path.join(self.project_dir, name))
            for name in _CONFIG_FILES
        ):
            return args
        empty = os.path.join(self.run_root, "empty-pytest.ini")
        if not os.path.exists(empty):
            with open(empty, "w") as handle:
                handle.write("[pytest]\n")
        return args + ["-c", empty, "--rootdir", self.project_dir]

    def build_argv(self, spec: RunSpec) -> List[str]:
        argv = (
            list(spec.env.pytest_argv)
            + self._config_args()
            + [
                "-q",
                "-p",
                "no:cacheprovider",
                "-p",
                "no:randomly",
                "-p",
                _PLUGIN_MODULE,
                "--tb=long",
            ]
        )
        if spec.seed is not None:
            # --hypothesis-seed disables the example database, so it is applied
            # only where reproducibility matters more than saved examples.
            argv += [f"--hypothesis-seed={spec.seed}"]
        if spec.collect_only:
            argv.append("--collect-only")
        argv.extend(spec.extra_args)
        # An explicit target keeps collection anchored to the project regardless
        # of where the active config file lives.
        argv.extend(spec.nodeids or ["."])
        return argv

    def run(self, spec: RunSpec) -> RunResult:
        paths = self._paths(spec)
        exec_result = self.sandbox.run(
            self.build_argv(spec),
            cwd=self.project_dir,
            env=self.build_env(spec, paths),
            network=False,
            limits=spec.limits,
        )
        result = RunResult(
            arm=spec.arm,
            tier=spec.tier,
            returncode=exec_result.returncode,
            duration=exec_result.duration,
            timed_out=exec_result.timed_out,
            stderr_tail=exec_result.stderr[-4000:],
        )
        result.crashed = (
            exec_result.returncode < 0
            or exec_result.returncode == _PYTEST_INTERNAL_ERROR
            or bool(_INTERNAL_ERROR_RE.search(exec_result.stderr))
        )
        result.outcomes = _read_report(paths["report"])
        if spec.tier is Tier.B_TELEMETRY:
            result.telemetry = telemetry.load(paths["storage"])
        return result

    def collect(
        self,
        env: EnvSpec,
        limits: Optional[Limits] = None,
        extra_args: Sequence[str] = (),
    ) -> List[str]:
        """Return the node ids of Hypothesis-driven tests in the project."""
        spec = RunSpec(
            arm=Arm.BASELINE,
            tier=Tier.A_VERDICT,
            env=env,
            collect_only=True,
            extra_args=extra_args,
            limits=limits or Limits(),
        )
        paths = self._paths(spec)
        self.sandbox.run(
            self.build_argv(spec),
            cwd=self.project_dir,
            env=self.build_env(spec, paths),
            network=False,
            limits=spec.limits,
        )
        if not os.path.exists(paths["report"]):
            return []
        with open(paths["report"]) as handle:
            return list(json.load(handle).get("hypothesis_nodeids", []))


def _read_report(path: str) -> Dict[str, CaseOutcome]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as handle:
            payload = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return {}
    outcomes: Dict[str, CaseOutcome] = {}
    for entry in payload.get("results", []):
        nodeid = entry.get("nodeid")
        if not nodeid:
            continue
        try:
            outcome = Outcome(entry.get("outcome", "not_run"))
        except ValueError:
            outcome = Outcome.NOT_RUN
        outcomes[nodeid] = CaseOutcome(
            nodeid=nodeid,
            outcome=outcome,
            duration=float(entry.get("duration") or 0.0),
            exception_type=entry.get("exception_type"),
            message=entry.get("message"),
            falsifying_example=entry.get("falsifying_example"),
            longrepr=entry.get("longrepr"),
        )
    return outcomes
