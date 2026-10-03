"""Group failures that are the same defect seen from different tests.

One bug in a library reaches many of its property tests, and a solver finds it
with a different input each time, so a run reports it once per test with a
different example. Triage reads clusters rather than failures, and a cluster
is identified by where the failure happened and what it said -- never by the
example, which is what varies.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .model import CaseOutcome, Classification

#: Paths belonging to the interpreter or to installed packages.
#:
#: A frame here is shared by every project, so clustering on it would merge
#: unrelated defects under one entry.
_FOREIGN_PATH_MARKERS = (
    "site-packages",
    "dist-packages",
    os.sep + "lib" + os.sep + "python",
    ".venv" + os.sep,
    os.sep + "crosshair" + os.sep,
    os.sep + "hypothesis" + os.sep,
)

#: ``path.py:lineno:`` at the start of a line, as pytest renders a frame.
_FRAME_RE = re.compile(r"^(?P<path>[^\s:][^:\n]*\.py):(?P<line>\d+):", re.MULTILINE)

_SCRUB = (
    # Object addresses: `<Foo object at 0x7f3a48462250>`.
    (re.compile(r"0x[0-9a-fA-F]{4,}"), "0xADDR"),
    # Temporary directories, which carry a per-run counter.
    (re.compile(r"/tmp/[^\s'\"]*"), "/tmp/PATH"),
    (re.compile(r"pytest-of-[^\s/'\"]+"), "pytest-of-USER"),
    # ISO timestamps and bare clock times.
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?"), "TIMESTAMP"),
    (re.compile(r"\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b"), "UUID"),
)

#: Numeric literals in a message, which are the falsifying example showing
#: through: the same defect reports a different number on every seed.
_NUMBER_RE = re.compile(r"-?\b\d+(?:\.\d+)?\b")

#: Where Hypothesis starts reporting the input it found.
#:
#: pytest folds this into the same message as the assertion, and it is the one
#: part guaranteed to differ between two sightings of one defect.
_EXAMPLE_RE = re.compile(r"(Falsifying example|Failing test case)\s*:.*", re.DOTALL)

#: pytest's assertion introspection, which spells out the operands it found.
#:
#: These lines hold the values the example produced, so two sightings of one
#: defect differ here even when the assertion itself is identical.
_INTROSPECTION_RE = re.compile(r"^\s*\+\s+(?:where|and)\s+.*$", re.MULTILINE)

_MESSAGE_LIMIT = 160


def scrub(text: str) -> str:
    """Remove the parts of a message that differ between runs of one defect."""
    for pattern, replacement in _SCRUB:
        text = pattern.sub(replacement, text)
    return text


def normalize_message(message: Optional[str]) -> str:
    """A message reduced to what is stable across examples of one defect."""
    if not message:
        return ""
    trimmed = _INTROSPECTION_RE.sub("", _EXAMPLE_RE.sub("", message))
    collapsed = " ".join(scrub(trimmed).split())
    return _NUMBER_RE.sub("N", collapsed)[:_MESSAGE_LIMIT]


def _is_project_frame(path: str, project_dir: str) -> bool:
    absolute = path if os.path.isabs(path) else os.path.join(project_dir, path)
    normalized = os.path.normpath(absolute)
    if any(marker in normalized for marker in _FOREIGN_PATH_MARKERS):
        return False
    return os.path.normpath(project_dir) in normalized or not os.path.isabs(path)


def failing_frame(longrepr: Optional[str], project_dir: str) -> str:
    """The innermost project frame in a traceback, as ``path.py:lineno``.

    Innermost rather than outermost: the outermost frame of a test failure is
    always the test function, which the node id already names, so clustering on
    it would put every failure in one test file together.
    """
    if not longrepr:
        return ""
    found = [
        (m.group("path"), m.group("line"))
        for m in _FRAME_RE.finditer(longrepr)
        if _is_project_frame(m.group("path"), project_dir)
    ]
    if not found:
        return ""
    path, line = found[-1]
    return f"{os.path.normpath(path)}:{line}"


@dataclass(frozen=True)
class Signature:
    """What makes two failures the same defect."""

    exception_type: str
    frame: str
    message: str

    def describe(self) -> str:
        where = self.frame or "unknown frame"
        what = self.message or "no message"
        return f"{self.exception_type or 'no exception'} at {where}: {what}"


@dataclass
class Cluster:
    signature: Signature
    nodeids: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.nodeids)


def signature_of(
    item: Classification, detail: Optional[CaseOutcome], project_dir: str
) -> Signature:
    longrepr = detail.longrepr if detail is not None else None
    message = detail.message if detail is not None else None
    return Signature(
        exception_type=item.exception_type or "",
        frame=failing_frame(longrepr, project_dir),
        message=normalize_message(message),
    )


def cluster(
    items: Sequence[Classification],
    details: Optional[Dict[str, CaseOutcome]] = None,
    project_dir: str = "",
) -> List[Cluster]:
    """Group the failures among ``items``, largest cluster first.

    A verdict with no exception is not a failure and is left out, so a run
    where nothing failed produces no clusters rather than one big one.
    """
    details = details or {}
    found: Dict[Tuple[str, str, str], Cluster] = {}
    for item in items:
        if not item.exception_type:
            continue
        signature = signature_of(item, details.get(item.nodeid), project_dir)
        key = (signature.exception_type, signature.frame, signature.message)
        entry = found.setdefault(key, Cluster(signature))
        entry.nodeids.append(item.nodeid)
        if item.falsifying_example:
            entry.examples.append(item.falsifying_example)
    return sorted(found.values(), key=lambda c: (-c.size, c.signature.describe()))
