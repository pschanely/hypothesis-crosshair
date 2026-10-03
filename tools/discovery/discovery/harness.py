"""Diagnosing a suite that will not run, and the repairs allowed for it.

A repair may change the environment a suite runs in. It may never change the
suite. Installing a plugin the project's own configuration demands is a
repair; editing a test, relaxing an assertion, deleting a failing case or
rewriting a strategy is not, and a run whose harness was "repaired" that way
would manufacture findings rather than discover them.

``Repair`` can express packages to install, environment variables, and
arguments to pytest. It cannot express a change to a file, so the forbidden
repair has no representation here rather than only a rule against it.

Every diagnosis below comes from a failure observed while provisioning the
corpus. An unrecognized failure yields no repair and is escalated by the
caller, which is the behaviour to keep: guessing at an unseen failure is how
a harness ends up quietly running something other than the suite.
"""

import re
import shlex
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

#: Repairs attempted for one project before it is escalated instead.
MAX_REPAIRS = 3

#: Command-line flags a project's own pytest configuration may carry, mapped
#: to the distribution that supplies them. A suite whose ``addopts`` names a
#: plugin flag will not start until that plugin is installed. No prefix here
#: may extend another, so a flag matches at most one distribution.
PLUGIN_FLAGS = {
    "--benchmark-": "pytest-benchmark",
    "--cov": "pytest-cov",
    "-n": "pytest-xdist",
    "--numprocesses": "pytest-xdist",
    "--dist": "pytest-xdist",
    "--asyncio-mode": "pytest-asyncio",
    "--timeout": "pytest-timeout",
    "--randomly-": "pytest-randomly",
    "--snapshot-update": "syrupy",
    "--mypy": "pytest-mypy-plugins",
    "--hypothesis-": "hypothesis",
}

#: Plugins that must be installed for a suite to start, then kept from acting.
#: xdist moves tests into subprocesses the injected plugin does not reach, so
#: a suite configured for it is run single-process regardless.
NEUTRALIZED_PLUGINS = {"pytest-xdist": ["-n0"]}

_UNRECOGNIZED_RE = re.compile(r"unrecognized arguments:\s*(?P<flags>.+)")
_MISSING_MODULE_RE = re.compile(
    r"(?:ModuleNotFoundError|ImportError): No module named '(?P<module>[\w.]+)'"
)

#: Importable names whose distribution is spelled differently.
MODULE_DISTRIBUTIONS = {
    "pytest_benchmark": "pytest-benchmark",
    "pytest_asyncio": "pytest-asyncio",
    "pytest_mock": "pytest-mock",
    "pytest_xdist": "pytest-xdist",
    "pytest_timeout": "pytest-timeout",
    "hypothesis": "hypothesis",
    "attr": "attrs",
    "yaml": "PyYAML",
}


@dataclass
class Repair:
    """A change to the environment a suite runs in, and nothing more."""

    name: str
    rationale: str
    packages: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    pytest_args: List[str] = field(default_factory=list)

    def describe(self) -> str:
        parts = []
        if self.packages:
            parts.append(f"install {' '.join(self.packages)}")
        if self.env:
            parts.append(" ".join(f"{k}={v}" for k, v in sorted(self.env.items())))
        if self.pytest_args:
            parts.append(f"pytest {' '.join(self.pytest_args)}")
        return f"{self.name}: {'; '.join(parts)}"


def _distribution_for_flag(flag: str) -> Optional[str]:
    bare = flag.split("=", 1)[0]
    for prefix, dist in PLUGIN_FLAGS.items():
        if bare.startswith(prefix):
            return dist
    return None


def _unrecognized_flags(text: str) -> List[str]:
    found: List[str] = []
    for match in _UNRECOGNIZED_RE.finditer(text):
        for flag in shlex.split(match.group("flags")):
            if flag.startswith("-") and flag not in found:
                found.append(flag)
    return found


def diagnose(text: str) -> Optional[Repair]:
    """Name a repair for a failure, or nothing if the failure is unfamiliar."""
    flags = _unrecognized_flags(text)
    distributions: List[str] = []
    for flag in flags:
        dist = _distribution_for_flag(flag)
        if dist and dist not in distributions:
            distributions.append(dist)
    if distributions:
        neutralize = [
            arg for dist in distributions for arg in NEUTRALIZED_PLUGINS.get(dist, [])
        ]
        return Repair(
            name="install-configured-plugins",
            rationale=(
                "the project's pytest configuration passes "
                f"{', '.join(flags)}, which needs "
                f"{', '.join(distributions)}"
            ),
            packages=distributions,
            pytest_args=neutralize,
        )

    missing = _MISSING_MODULE_RE.search(text)
    if missing:
        module = missing.group("module")
        dist = MODULE_DISTRIBUTIONS.get(module.split(".")[0])
        if dist:
            return Repair(
                name="install-missing-import",
                rationale=f"a test module imports {module}, supplied by {dist}",
                packages=[dist],
            )
    return None


def plan(text: str, applied: Sequence[str] = ()) -> Optional[Repair]:
    """The next repair to try, given the ones already tried for this project.

    A repair already attempted is not offered again, so a failure the repair
    did not actually fix escalates instead of looping.
    """
    if len(applied) >= MAX_REPAIRS:
        return None
    found = diagnose(text)
    if found is None or found.name in applied:
        return None
    return found
