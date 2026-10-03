"""Fetching a candidate's source just long enough to see what it holds.

PyPI metadata cannot say whether a project has Hypothesis tests, and an
agent reading a project's description guesses badly at it. A shallow clone
and an AST survey answer it exactly, in about a second, so the probe is what
decides which projects are worth provisioning.

A checkout here is third-party source that no gate has passed. It is read and
deleted; nothing in it is imported, installed or run.
"""

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Iterator, Optional, Sequence

from .candidates import Assessment, assess, survey
from .pypi import PackageFacts

#: Commits fetched per candidate. Only the current tree is ever read.
CLONE_DEPTH = 1

CLONE_TIMEOUT = 300.0


@dataclass
class ProbeResult:
    """What one candidate turned out to be, once its source was read."""

    facts: PackageFacts
    assessment: Optional[Assessment] = None
    error: str = ""
    seconds: float = 0.0

    @property
    def worth_provisioning(self) -> bool:
        return self.assessment is not None and self.assessment.runnable

    def describe(self) -> str:
        if self.error:
            return f"{self.facts.name}: could not read -- {self.error}"
        found = self.assessment
        if not found.runnable:
            return f"{self.facts.name}: no property tests"
        return (
            f"{self.facts.name}: score {found.score}, {found.units} property "
            f"tests  {self.facts.repo_url}"
        )


def clone(repo_url: str, dest: str, timeout: float = CLONE_TIMEOUT) -> None:
    """Shallow-clone a repository, raising with git's own message on failure."""
    done = subprocess.run(
        ["git", "clone", "--depth", str(CLONE_DEPTH), "-q", repo_url, dest],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip().splitlines()[-1][:200] or "clone failed")


def probe(
    facts: PackageFacts,
    work_dir: str,
    keep: bool = False,
    timeout: float = CLONE_TIMEOUT,
) -> ProbeResult:
    """Clone one candidate, survey it, and remove the checkout again."""
    dest = os.path.join(work_dir, facts.name)
    shutil.rmtree(dest, ignore_errors=True)
    started = time.monotonic()
    try:
        clone(facts.repo_url, dest, timeout)
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        shutil.rmtree(dest, ignore_errors=True)
        return ProbeResult(
            facts,
            error=str(exc) or type(exc).__name__,
            seconds=time.monotonic() - started,
        )
    try:
        found = assess(survey(dest))
    finally:
        if not keep:
            shutil.rmtree(dest, ignore_errors=True)
    return ProbeResult(facts, assessment=found, seconds=time.monotonic() - started)


def probe_all(
    every: Sequence[PackageFacts],
    work_dir: str,
    keep: bool = False,
    timeout: float = CLONE_TIMEOUT,
) -> Iterator[ProbeResult]:
    """Probe candidates in order, yielding each result as it is read."""
    os.makedirs(work_dir, exist_ok=True)
    for facts in every:
        yield probe(facts, work_dir, keep=keep, timeout=timeout)
