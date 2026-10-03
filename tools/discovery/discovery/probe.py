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
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Sequence

from .candidates import Assessment, assess, survey

#: Commits fetched per candidate. Only the current tree is ever read.
CLONE_DEPTH = 1


@dataclass
class Candidate:
    """A repository worth looking inside, and where the suggestion came from."""

    name: str
    repo_url: str
    #: The source that proposed it, kept so a run's provenance is readable.
    source: str = ""
    #: Node ids a previous survey of this repository recorded, if any.
    known_nodeids: List[str] = field(default_factory=list)
    #: Distribution names the project's tests need, without versions.
    test_dependencies: List[str] = field(default_factory=list)


CLONE_TIMEOUT = 300.0

#: Megabytes a checkout may occupy before it is deleted unread. A monorepo
#: publishing many packages can run to gigabytes of generated source, which
#: exhausts a sandbox's disk allowance for one candidate.
MAX_CHECKOUT_MB = 500


@dataclass
class ProbeResult:
    """What one candidate turned out to be, once its source was read."""

    candidate: Candidate
    assessment: Optional[Assessment] = None
    error: str = ""
    seconds: float = 0.0
    megabytes: int = 0

    @property
    def worth_provisioning(self) -> bool:
        return self.assessment is not None and self.assessment.runnable

    def describe(self) -> str:
        if self.error:
            return f"{self.candidate.name}: could not read -- {self.error}"
        found = self.assessment
        if not found.runnable:
            return f"{self.candidate.name}: no property tests"
        return (
            f"{self.candidate.name}: score {found.score}, {found.units} property "
            f"tests  {self.candidate.repo_url}"
        )


def checkout_megabytes(path: str) -> int:
    total = 0
    for root, _, names in os.walk(path):
        for name in names:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total // (1024 * 1024)


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
    candidate: Candidate,
    work_dir: str,
    keep: bool = False,
    timeout: float = CLONE_TIMEOUT,
    max_megabytes: int = MAX_CHECKOUT_MB,
) -> ProbeResult:
    """Clone one candidate, survey it, and remove the checkout again."""
    dest = os.path.join(work_dir, candidate.name)
    shutil.rmtree(dest, ignore_errors=True)
    started = time.monotonic()
    try:
        clone(candidate.repo_url, dest, timeout)
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        shutil.rmtree(dest, ignore_errors=True)
        return ProbeResult(
            candidate,
            error=str(exc) or type(exc).__name__,
            seconds=time.monotonic() - started,
        )
    size = checkout_megabytes(dest)
    if max_megabytes and size > max_megabytes:
        shutil.rmtree(dest, ignore_errors=True)
        return ProbeResult(
            candidate,
            error=f"checkout is {size}MB, over the {max_megabytes}MB budget",
            seconds=time.monotonic() - started,
            megabytes=size,
        )
    try:
        found = assess(survey(dest))
    finally:
        if not keep:
            shutil.rmtree(dest, ignore_errors=True)
    return ProbeResult(
        candidate,
        assessment=found,
        seconds=time.monotonic() - started,
        megabytes=size,
    )


def probe_all(
    every: Sequence[Candidate],
    work_dir: str,
    keep: bool = False,
    timeout: float = CLONE_TIMEOUT,
    max_megabytes: int = MAX_CHECKOUT_MB,
) -> Iterator[ProbeResult]:
    """Probe candidates in order, yielding each result as it is read."""
    os.makedirs(work_dir, exist_ok=True)
    for candidate in every:
        yield probe(
            candidate,
            work_dir,
            keep=keep,
            timeout=timeout,
            max_megabytes=max_megabytes,
        )
