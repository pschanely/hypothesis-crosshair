"""Stage 4: run the pipeline against faults whose answer is already known.

Every other signal this tool produces is self-reported. A canary is the only
check that the chain from injection through classification actually works, so
it is the thing that catches a metric which has quietly stopped measuring what
it claims to.

A fault is applied to a real checkout and reverted afterwards. Injection
refuses to proceed unless its anchor text appears exactly once, because a
``replace`` that silently matches nothing produces a clean run of an unmodified
project and looks like a passing canary.
"""

import enum
import os
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterator, List, Optional, Sequence

from .model import Classification, Verdict
from .pipeline import Pipeline


class Expectation(str, enum.Enum):
    """What the pipeline should conclude about a fault."""

    DETECTED = "detected"
    NOT_DETECTED = "not_detected"


#: Verdicts that mean the fault was found and survived the clean room.
DETECTION_VERDICTS = frozenset({Verdict.TROPHY_CANDIDATE, Verdict.SHARED_FIND})

#: Verdicts that mean CrossHair failed the test but the finding is unconfirmed.
UNCONFIRMED_VERDICTS = frozenset({Verdict.PENDING_VALIDATION})

#: Verdicts that answer nothing about the fault.
#:
#: A canary scored without this set passes vacuously whenever the pipeline
#: fails to run: nothing is detected, so a NOT_DETECTED expectation is met and
#: the run is reported green while having tested nothing at all.
INCONCLUSIVE_VERDICTS = frozenset(
    {
        Verdict.NO_BASELINE_RESULT,
        Verdict.QUARANTINED_UNSTABLE,
        Verdict.QUARANTINED_NONDETERMINISTIC,
        Verdict.CROSSHAIR_CRASH,
        Verdict.CROSSHAIR_TIMEOUT,
        Verdict.PRE_EXISTING_FAILURE,
        Verdict.OBSERVER_EFFECT,
        # A find the clean room could not confirm does not settle either
        # expectation: it is neither a validated detection nor a clean miss.
        Verdict.PENDING_VALIDATION,
    }
)


class FaultNotApplied(RuntimeError):
    """The fault could not be injected, so any result would be meaningless."""


@dataclass(frozen=True)
class Fault:
    """A known defect, and what the pipeline is expected to make of it."""

    name: str
    relative_path: str
    original: str
    replacement: str
    nodeids: Sequence[str]
    expectation: Expectation = Expectation.DETECTED
    rationale: str = ""
    #: A module that must resolve inside the project being patched.
    #:
    #: Injection edits a source tree, but the environment under test may import
    #: the package from site-packages instead, in which case the fault never
    #: runs and the canary reports a confident result about nothing.
    import_module: Optional[str] = None
    #: The exact verdicts expected, when the boolean is too coarse.
    #:
    #: A trophy and a shared find both count as detection, so a canary meant to
    #: exercise one branch passes on the other without this.
    expect_verdicts: Optional[FrozenSet[Verdict]] = None


#: Where a pending injection is recorded, so a killed run can be undone.
#:
#: ``finally`` does not run when the process is killed, which leaves the target
#: project patched. The next run then fails to find its anchor, and a checkout
#: shared with other work silently carries the fault.
BACKUP_SUFFIX = ".canary-backup"


def restore_pending(project_dir: str) -> List[str]:
    """Undo any injection a previous run was killed before reverting."""
    restored = []
    for root, _dirs, files in os.walk(project_dir):
        for name in files:
            if not name.endswith(BACKUP_SUFFIX):
                continue
            backup = os.path.join(root, name)
            target = backup[: -len(BACKUP_SUFFIX)]
            with open(backup) as handle:
                original = handle.read()
            with open(target, "w") as handle:
                handle.write(original)
            os.remove(backup)
            restored.append(os.path.relpath(target, project_dir))
    return restored


@contextmanager
def injected(project_dir: str, fault: Fault) -> Iterator[str]:
    """Apply a fault for the duration of the block, then restore the file."""
    path = os.path.join(project_dir, fault.relative_path)
    if not os.path.exists(path):
        raise FaultNotApplied(f"{fault.name}: {fault.relative_path} does not exist")
    restore_pending(project_dir)
    with open(path) as handle:
        before = handle.read()
    occurrences = before.count(fault.original)
    if occurrences != 1:
        raise FaultNotApplied(
            f"{fault.name}: anchor text appears {occurrences} times in "
            f"{fault.relative_path}, expected exactly 1"
        )
    patched = before.replace(fault.original, fault.replacement)
    if patched == before:
        raise FaultNotApplied(f"{fault.name}: replacement changed nothing")
    backup = path + BACKUP_SUFFIX
    try:
        with open(backup, "w") as handle:
            handle.write(before)
        with open(path, "w") as handle:
            handle.write(patched)
        yield path
    finally:
        with open(path, "w") as handle:
            handle.write(before)
        if os.path.exists(backup):
            os.remove(backup)
        with open(path) as handle:
            if handle.read() != before:
                raise FaultNotApplied(
                    f"{fault.name}: {fault.relative_path} was left modified"
                )


def verify_target_imports(
    python_argv: Sequence[str], project_dir: str, fault: Fault
) -> None:
    """Check the environment under test imports the code being patched."""
    if not fault.import_module:
        return
    probe = subprocess.run(
        [
            *python_argv,
            "-c",
            f"import {fault.import_module} as m; print(m.__file__)",
        ],
        capture_output=True,
        text=True,
        cwd=tempfile.gettempdir(),
    )
    if probe.returncode != 0:
        raise FaultNotApplied(
            f"{fault.name}: could not import {fault.import_module} in the "
            f"target environment: {probe.stderr.strip()[-200:]}"
        )
    resolved = os.path.realpath(probe.stdout.strip())
    if not resolved.startswith(os.path.realpath(project_dir) + os.sep):
        raise FaultNotApplied(
            f"{fault.name}: the target environment imports "
            f"{fault.import_module} from {resolved}, not from {project_dir}. "
            "The fault would be injected into source that is never loaded."
        )


@dataclass
class CanaryResult:
    fault: str
    expectation: Expectation
    verdicts: Dict[str, Verdict] = field(default_factory=dict)
    detected: bool = False
    unconfirmed: bool = False
    missing: List[str] = field(default_factory=list)
    expect_verdicts: Optional[FrozenSet[Verdict]] = None
    error: Optional[str] = None
    attempts: int = 1
    detections: int = 0

    @property
    def conclusive(self) -> bool:
        """Whether the run answered the question the fault poses."""
        return (
            self.error is None
            and bool(self.verdicts)
            and not self.missing
            and not any(v in INCONCLUSIVE_VERDICTS for v in self.verdicts.values())
        )

    @property
    def passed(self) -> bool:
        if not self.conclusive:
            return False
        if self.expect_verdicts is not None:
            return bool(self.verdicts) and set(self.verdicts.values()) <= set(
                self.expect_verdicts
            )
        return self.detected == (self.expectation is Expectation.DETECTED)

    @property
    def detail(self) -> str:
        if self.error:
            return self.error
        if not self.verdicts:
            return "inconclusive: no classification for any target test"
        seen = ", ".join(sorted({v.value for v in self.verdicts.values()}))
        if self.missing:
            return f"inconclusive: {len(self.missing)} target(s) unclassified ({seen})"
        if not self.conclusive:
            return f"inconclusive: {seen}"
        return seen


def run_fault(pipeline: Pipeline, project_dir: str, fault: Fault) -> CanaryResult:
    """Inject one fault, run the full pipeline, and score the outcome."""
    result = CanaryResult(
        fault=fault.name,
        expectation=fault.expectation,
        expect_verdicts=fault.expect_verdicts,
    )
    try:
        verify_target_imports(pipeline.crosshair_env.python_argv, project_dir, fault)
        with injected(project_dir, fault):
            report = pipeline.run(list(fault.nodeids))
    except FaultNotApplied as exc:
        result.error = str(exc)
        return result
    except Exception as exc:  # a pipeline failure is a canary failure
        result.error = f"{type(exc).__name__}: {exc}"
        return result
    targets = set(fault.nodeids)
    for entry in report.classifications:
        if entry.nodeid in targets:
            result.verdicts[entry.nodeid] = entry.verdict
    result.missing = sorted(targets - set(result.verdicts))
    result.detected = any(v in DETECTION_VERDICTS for v in result.verdicts.values())
    result.unconfirmed = any(
        v in UNCONFIRMED_VERDICTS for v in result.verdicts.values()
    )
    return result


def _expected_label(entry: CanaryResult) -> str:
    if entry.expect_verdicts is not None:
        return ", ".join(sorted(v.value for v in entry.expect_verdicts))
    return entry.expectation.value


def summarise(results: Sequence[CanaryResult]) -> List[str]:
    lines = ["canary:"]
    for entry in results:
        mark = "PASS" if entry.passed else "FAIL"
        lines.append(
            f"  {mark}  {entry.fault:34s} expected {_expected_label(entry):22s} "
            f"{entry.detail}"
            + (
                f"  [found {entry.detections}/{entry.attempts}]"
                if entry.attempts > 1
                else ""
            )
        )
    failed = [entry for entry in results if not entry.passed]
    lines.append(
        f"  {len(results) - len(failed)}/{len(results)} faults behaved as expected"
    )
    if failed:
        lines.append(
            "  a failing canary means the pipeline's verdicts cannot be trusted "
            "until it is explained."
        )
    return lines


def combine(fault: Fault, results: Sequence[CanaryResult]) -> CanaryResult:
    """Fold repeated attempts at one fault into a single result.

    The solver's search is not reproducible: z3 offers no determinism
    guarantee, and per-path deadlines measured in process time widen the spread
    further, so a path that finishes on an idle machine is abandoned on a
    loaded one. A single attempt therefore decides a canary on a coin toss, and
    no upstream fix changes that.

    A fault expected to be found passes if any attempt found it: the question
    is whether the pipeline can reach it at all. A fault expected not to be
    found must go unfound in every attempt, which is the harder claim and the
    one worth making strictly.
    """
    conclusive = [r for r in results if r.conclusive]
    merged = CanaryResult(
        fault=fault.name,
        expectation=fault.expectation,
        expect_verdicts=fault.expect_verdicts,
        attempts=len(results),
        detections=sum(1 for r in conclusive if r.detected),
    )
    if not conclusive:
        merged.error = next((r.error for r in results if r.error), None)
        merged.missing = results[0].missing if results else []
        merged.verdicts = results[0].verdicts if results else {}
        return merged
    for entry in conclusive:
        merged.verdicts.update(entry.verdicts)
    merged.detected = merged.detections > 0
    merged.unconfirmed = any(r.unconfirmed for r in conclusive)
    return merged
