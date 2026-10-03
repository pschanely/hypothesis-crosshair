"""Failure triage: deciding what a cluster of failures actually is.

This is the one judgment in the loop that needs to read code and infer intent,
so it is where a model belongs. Everything around it stays deterministic: the
queue, the item a decider reads, and the schema its answer must satisfy. A
decider returns a plain dict and never touches the store, so an answer that
does not parse is rejected before it can become state.
"""

import enum
import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from . import store as store_mod
from .cluster import Cluster, Signature
from .store import Store


class TriageCategory(str, enum.Enum):
    """What a cluster of failures turned out to be."""

    PROJECT_BUG = "project_bug"
    OVERSTRONG_PROPERTY = "overstrong_property"
    CROSSHAIR_ARTIFACT = "crosshair_artifact"
    UNCLEAR = "unclear"


#: Categories that go no further without a person looking.
#:
#: A project bug is the only one that can become a trophy, and an artifact is
#: the only one that is a CrossHair finding, so everything else stops here.
NEEDS_HUMAN = frozenset({TriageCategory.UNCLEAR})

#: Below this, an answer is treated as no answer whatever it claims.
MIN_CONFIDENCE = 0.0
MAX_CONFIDENCE = 1.0

#: Clusters one invocation will triage before stopping.
#:
#: A budget is what makes an unattended run bounded; without one, a bad batch
#: spends until something else stops it.
DEFAULT_BUDGET = 25


class TriageSchemaError(ValueError):
    """A decider's answer did not satisfy the schema."""


@dataclass
class TriageVerdict:
    category: TriageCategory
    confidence: float
    reasoning: str
    #: Where the decider says it looked, for auditing a bad batch afterwards.
    evidence: List[str] = field(default_factory=list)

    @property
    def needs_human(self) -> bool:
        return self.category in NEEDS_HUMAN


def parse_verdict(payload: object) -> TriageVerdict:
    """Build a verdict from a decider's answer, or refuse it.

    Refusing is the point: a decider is free-form, and an answer that is
    allowed through unvalidated becomes durable state that nothing downstream
    can tell apart from a checked one.
    """
    if not isinstance(payload, dict):
        raise TriageSchemaError(f"expected an object, got {type(payload).__name__}")
    missing = {"category", "confidence", "reasoning"} - set(payload)
    if missing:
        raise TriageSchemaError(f"missing {', '.join(sorted(missing))}")
    try:
        category = TriageCategory(payload["category"])
    except ValueError:
        raise TriageSchemaError(
            f"unknown category {payload['category']!r}; expected one of "
            + ", ".join(c.value for c in TriageCategory)
        ) from None
    confidence = payload["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise TriageSchemaError("confidence must be a number")
    if not MIN_CONFIDENCE <= confidence <= MAX_CONFIDENCE:
        raise TriageSchemaError(
            f"confidence {confidence} outside {MIN_CONFIDENCE}..{MAX_CONFIDENCE}"
        )
    reasoning = payload["reasoning"]
    if not isinstance(reasoning, str) or not reasoning.strip():
        raise TriageSchemaError("reasoning must be a non-empty string")
    evidence = payload.get("evidence", [])
    if not isinstance(evidence, list) or any(not isinstance(e, str) for e in evidence):
        raise TriageSchemaError("evidence must be a list of strings")
    return TriageVerdict(
        category=category,
        confidence=float(confidence),
        reasoning=reasoning.strip(),
        evidence=list(evidence),
    )


def signature_key(signature: Signature) -> str:
    """A short stable name for a cluster, usable as a primary key."""
    raw = "\x00".join(
        [signature.exception_type, signature.frame, signature.message]
    ).encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()[:16]


@dataclass
class TriageItem:
    """One unit of triage work, as a decider receives it."""

    key: str
    signature: Signature
    nodeids: List[str]
    examples: List[str]
    project: str
    #: One unscrubbed traceback, so a decider can read what actually happened.
    sample: str = ""

    def as_json(self) -> str:
        """The item as a decider receives it on stdin."""
        return json.dumps(
            {
                "key": self.key,
                "project": self.project,
                "exception_type": self.signature.exception_type,
                "frame": self.signature.frame,
                "message": self.signature.message,
                "nodeids": self.nodeids,
                "examples": self.examples,
                "sample": self.sample,
            },
            indent=1,
        )

    def describe(self) -> str:
        return (
            f"{self.signature.describe()}\n"
            f"reached by {len(self.nodeids)} test(s): " + ", ".join(self.nodeids[:5])
        )


def _payload_of(group: Cluster, project: str) -> dict:
    return {
        "exception_type": group.signature.exception_type,
        "frame": group.signature.frame,
        "message": group.signature.message,
        "nodeids": group.nodeids,
        "examples": group.examples,
        "project": project,
        "sample": group.sample,
    }


def _item_of(key: str, payload: dict) -> TriageItem:
    return TriageItem(
        key=key,
        signature=Signature(
            exception_type=payload.get("exception_type", ""),
            frame=payload.get("frame", ""),
            message=payload.get("message", ""),
        ),
        nodeids=list(payload.get("nodeids", [])),
        examples=list(payload.get("examples", [])),
        project=payload.get("project", ""),
        sample=payload.get("sample", ""),
    )


def enqueue_clusters(
    store: Store, run_id: str, clusters: Sequence[Cluster], project: str
) -> int:
    """Queue each cluster for triage; return how many are new."""
    return store.enqueue(
        run_id,
        store_mod.TRIAGE_CLUSTER,
        [(signature_key(g.signature), _payload_of(g, project)) for g in clusters],
    )


def next_item(store: Store, run_id: str, now: float) -> Optional[TriageItem]:
    claimed = store.claim(run_id, store_mod.TRIAGE_CLUSTER, now, lease_seconds=0.0)
    if claimed is None:
        return None
    return _item_of(claimed["key"], claimed["payload"])


#: A decider reads one item and answers with a dict matching the schema.
Decider = Callable[[TriageItem], object]

#: Seconds one decider call may take before it counts as no answer.
DECIDER_TIMEOUT = 300.0


class CommandDecider:
    """Triage by running an external command, one invocation per cluster.

    The command receives the item as JSON on stdin and must print a JSON
    object on stdout. Keeping the decider outside the process is what lets a
    model answer here without this tool depending on one, and it means a
    decider that hangs or dies costs a timeout rather than the run.
    """

    def __init__(self, argv: Sequence[str], timeout: float = DECIDER_TIMEOUT) -> None:
        self.argv = list(argv)
        self.timeout = timeout

    def __call__(self, item: TriageItem) -> object:
        done = subprocess.run(
            self.argv,
            input=item.as_json(),
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        if done.returncode != 0:
            raise TriageSchemaError(
                f"decider exited {done.returncode}: {done.stderr.strip()[:300]}"
            )
        try:
            return json.loads(done.stdout)
        except ValueError as exc:
            raise TriageSchemaError(
                f"decider did not print JSON ({exc}): {done.stdout.strip()[:300]}"
            ) from None


@dataclass
class TriageRun:
    decided: Dict[str, TriageVerdict] = field(default_factory=dict)
    rejected: Dict[str, str] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.decided) + len(self.rejected)


def run_triage(
    store: Store,
    run_id: str,
    decide: Decider,
    now: Callable[[], float],
    budget: int = DEFAULT_BUDGET,
) -> TriageRun:
    """Triage queued clusters until the queue or the budget runs out.

    A decider that raises, or answers off-schema, abandons that one cluster
    and the run continues: one unusable answer must not cost the batch.
    """
    outcome = TriageRun()
    for _ in range(max(0, budget)):
        item = next_item(store, run_id, now())
        if item is None:
            break
        try:
            verdict = parse_verdict(decide(item))
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            outcome.rejected[item.key] = reason
            store.abandon(run_id, store_mod.TRIAGE_CLUSTER, item.key, reason)
            continue
        store.record_triage(run_id, item.key, verdict)
        outcome.decided[item.key] = verdict
    return outcome


def verdict_from_payload(payload: dict) -> TriageVerdict:
    return TriageVerdict(
        category=TriageCategory(payload["category"]),
        confidence=float(payload["confidence"]),
        reasoning=payload["reasoning"],
        evidence=list(payload.get("evidence", [])),
    )


def verdict_payload(verdict: TriageVerdict) -> str:
    data = asdict(verdict)
    data["category"] = verdict.category.value
    return json.dumps(data)
