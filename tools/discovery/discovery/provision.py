"""Building an environment a project's tests can run in.

This is the only stage that uses the network, and the only one that runs code
the project ships before any test is selected: installing an sdist executes
its build. Confining both here is what lets the verdict runs stay offline.

A provisioned environment is described by what it took to reach, because a
project that needed four repairs is a different result from one that needed
none. The repairs come from ``harness``, which only knows failures that have
actually been observed; an unfamiliar one ends provisioning rather than being
guessed at.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import harness
from .sandbox import Limits, Sandbox

#: Builds a virtual environment. Overridable for an image without uv.
VENV_ARGV = ("uv", "venv", "--quiet")

#: Installs into a named interpreter's environment.
INSTALL_ARGV = ("uv", "pip", "install", "--quiet", "--python")

#: Always needed, whatever the project asks for.
BASE_PACKAGES = ("pytest", "hypothesis")

INSTALL_LIMITS = Limits(wall_seconds=1800)
COLLECT_LIMITS = Limits(wall_seconds=600)

_COLLECTED_RE = re.compile(r"(?P<count>\d+)\s+tests? collected")

#: pytest exits 5 when it collected nothing, which is not a broken harness.
_CLEAN_COLLECT_CODES = (0, 5)


@dataclass
class Provisioned:
    """An environment, and the record of what building it required."""

    project: str
    python: str = ""
    #: Arguments every later pytest run in this environment must carry.
    pytest_args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    repairs: List[str] = field(default_factory=list)
    collected: int = 0
    error: str = ""

    @property
    def ready(self) -> bool:
        return not self.error

    def describe(self) -> str:
        name = os.path.basename(os.path.normpath(self.project))
        if not self.ready:
            return f"{name}: not provisioned -- {self.error}"
        repairs = f", {len(self.repairs)} repair(s)" if self.repairs else ""
        return f"{name}: {self.collected} tests collected{repairs}"


def venv_python(venv_dir: str) -> str:
    return os.path.join(venv_dir, "bin", "python")


def _collected_count(text: str) -> int:
    found = _COLLECTED_RE.search(text)
    return int(found.group("count")) if found else 0


def _install(
    sandbox: Sandbox, python: str, targets: Sequence[str], cwd: str, env: Dict[str, str]
) -> Tuple[bool, str]:
    done = sandbox.run(
        list(INSTALL_ARGV) + [python] + list(targets),
        cwd=cwd,
        env=env,
        network=True,
        limits=INSTALL_LIMITS,
    )
    return done.returncode == 0, (done.stderr or done.stdout)


def _collect(
    sandbox: Sandbox,
    python: str,
    project_dir: str,
    pytest_args: Sequence[str],
    env: Dict[str, str],
) -> Tuple[bool, str, int]:
    done = sandbox.run(
        [python, "-m", "pytest", "--collect-only", "-q"] + list(pytest_args),
        cwd=project_dir,
        env=env,
        network=False,
        limits=COLLECT_LIMITS,
    )
    text = (done.stdout or "") + "\n" + (done.stderr or "")
    return done.returncode in _CLEAN_COLLECT_CODES, text, _collected_count(text)


def provision(
    sandbox: Sandbox,
    project_dir: str,
    plugin_dir: str,
    venv_dir: Optional[str] = None,
    python_version: str = "3.12",
    extra_packages: Sequence[str] = (),
) -> Provisioned:
    """Build an environment for one project and prove its tests collect.

    Collection is the proof: an environment that installs but cannot collect
    is not provisioned, and a later run would report every test as having
    produced no result rather than saying the harness is broken.
    """
    result = Provisioned(project=project_dir)
    venv_dir = venv_dir or os.path.join(project_dir, ".venv-ch")
    result.python = venv_python(venv_dir)

    made = sandbox.run(
        list(VENV_ARGV) + [venv_dir, "--python", python_version],
        cwd=project_dir,
        network=True,
        limits=INSTALL_LIMITS,
    )
    if made.returncode != 0:
        result.error = f"could not create a virtual environment: {made.stderr[-300:]}"
        return result

    installed, detail = _install(
        sandbox,
        result.python,
        ["-e", project_dir, *BASE_PACKAGES, "-e", plugin_dir, *extra_packages],
        project_dir,
        {},
    )
    if not installed:
        result.error = f"install failed: {detail.strip()[-300:]}"
        return result

    while True:
        ok, text, count = _collect(
            sandbox, result.python, project_dir, result.pytest_args, result.env
        )
        if ok:
            result.collected = count
            return result
        repair = harness.plan(text, result.repairs)
        if repair is None:
            result.error = f"collection failed: {text.strip()[-300:]}"
            return result
        result.repairs.append(repair.name)
        result.env.update(repair.env)
        result.pytest_args.extend(repair.pytest_args)
        if repair.packages:
            installed, detail = _install(
                sandbox, result.python, repair.packages, project_dir, {}
            )
            if not installed:
                result.error = (
                    f"repair {repair.name} could not install "
                    f"{' '.join(repair.packages)}: {detail.strip()[-200:]}"
                )
                return result
