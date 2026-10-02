"""Durable state for the loop.

All pipeline state lives here rather than in any agent's context, so a run is
resumable after a crash and auditable after a bad batch.
"""

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from typing import Any, Dict, Iterable, List, Optional

from .model import Classification, CompletionStats, Outcome, SearchProgress, Verdict

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    project     TEXT NOT NULL,
    commit_sha  TEXT,
    started_at  REAL NOT NULL,
    versions    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verdicts (
    run_id      TEXT NOT NULL,
    nodeid      TEXT NOT NULL,
    verdict     TEXT NOT NULL,
    payload     TEXT NOT NULL,
    PRIMARY KEY (run_id, nodeid)
);
CREATE TABLE IF NOT EXISTS cache (
    key         TEXT PRIMARY KEY,
    verdict     TEXT NOT NULL,
    payload     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS work (
    key         TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    project     TEXT NOT NULL,
    nodeid      TEXT NOT NULL,
    state       TEXT NOT NULL,
    claimed_at  REAL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    detail      TEXT,
    PRIMARY KEY (run_id, key)
);
CREATE INDEX IF NOT EXISTS verdicts_by_kind ON verdicts (verdict);
CREATE INDEX IF NOT EXISTS work_by_state ON work (run_id, state);
"""

PENDING = "pending"
CLAIMED = "claimed"
DONE = "done"
ABANDONED = "abandoned"

#: Seconds before a claim is offered to another worker.
#:
#: A worker that dies -- or whose container is reclaimed -- leaves its item
#: claimed forever, so claims expire rather than being released on exit.
CLAIM_LEASE_SECONDS = 1800.0

#: Claims one item may take before it is abandoned rather than offered again.
MAX_ATTEMPTS = 3


def cache_key(
    *,
    commit_sha: str,
    nodeid: str,
    crosshair_version: str,
    plugin_version: str,
    python_version: str,
) -> str:
    """Identity of a verdict.

    A CrossHair or plugin version bump changes every key, so the re-run that
    follows a release doubles as the regression suite.
    """
    return "|".join(
        [commit_sha, nodeid, crosshair_version, plugin_version, python_version]
    )


def classification_from_payload(payload: dict) -> Classification:
    """Rebuild a verdict recorded by ``record_verdict`` or ``put_cache``."""
    data = dict(payload)
    completion = data.pop("completion", None)
    search = data.pop("search", None)
    optional = data.pop("validation", None)
    return Classification(
        nodeid=data["nodeid"],
        verdict=Verdict(data["verdict"]),
        baseline=Outcome(data["baseline"]),
        crosshair=Outcome(data["crosshair"]),
        validation=Outcome(optional) if optional else None,
        rationale=data.get("rationale", ""),
        falsifying_example=data.get("falsifying_example"),
        exception_type=data.get("exception_type"),
        completion=CompletionStats(**completion) if completion else None,
        search=SearchProgress(**search) if search else None,
        attempts=data.get("attempts", 1),
    )


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        with closing(self._conn.cursor()) as cur:
            cur.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def record_run(
        self,
        run_id: str,
        project: str,
        commit_sha: str,
        started_at: float,
        versions: Dict[str, str],
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?)",
            (run_id, project, commit_sha, started_at, json.dumps(versions)),
        )
        self._conn.commit()

    def record_verdict(self, run_id: str, item: Classification) -> None:
        payload = asdict(item)
        payload["verdict"] = item.verdict.value
        self._conn.execute(
            "INSERT OR REPLACE INTO verdicts VALUES (?,?,?,?)",
            (run_id, item.nodeid, item.verdict.value, json.dumps(payload, default=str)),
        )
        self._conn.commit()

    def record_verdicts(self, run_id: str, items: Iterable[Classification]) -> None:
        for item in items:
            self.record_verdict(run_id, item)

    def verdicts(
        self, run_id: Optional[str] = None, kind: Optional[str] = None
    ) -> List[dict]:
        sql = "SELECT payload FROM verdicts WHERE 1=1"
        args: List[Any] = []
        if run_id:
            sql += " AND run_id = ?"
            args.append(run_id)
        if kind:
            sql += " AND verdict = ?"
            args.append(kind)
        with closing(self._conn.cursor()) as cur:
            cur.execute(sql, args)
            return [json.loads(row["payload"]) for row in cur.fetchall()]

    def cached(self, key: str) -> Optional[dict]:
        with closing(self._conn.cursor()) as cur:
            cur.execute("SELECT payload FROM cache WHERE key = ?", (key,))
            row = cur.fetchone()
            return json.loads(row["payload"]) if row else None

    def put_cache(self, key: str, item: Classification) -> None:
        payload = asdict(item)
        payload["verdict"] = item.verdict.value
        self._conn.execute(
            "INSERT OR REPLACE INTO cache VALUES (?,?,?)",
            (key, item.verdict.value, json.dumps(payload, default=str)),
        )
        self._conn.commit()

    def enqueue(self, run_id: str, project: str, items: Iterable[tuple]) -> int:
        """Add ``(key, nodeid)`` pairs as pending work; return how many are new.

        A key already present keeps its state, so re-enqueueing a run resumes it
        rather than restarting it.
        """
        added = 0
        for key, nodeid in items:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO work (key, run_id, project, nodeid, state) "
                "VALUES (?,?,?,?,?)",
                (key, run_id, project, nodeid, PENDING),
            )
            added += cur.rowcount
        self._conn.commit()
        return added

    def claim(
        self,
        run_id: str,
        now: float,
        lease_seconds: float = CLAIM_LEASE_SECONDS,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> Optional[dict]:
        """Take the next item of work, or None when the run is finished.

        Items whose lease has expired are offered again, so work survives a
        worker that never got to report. An item that has been claimed
        ``max_attempts`` times is abandoned instead: a test that reliably kills
        its worker would otherwise be retried forever.
        """
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                "SELECT * FROM work WHERE run_id = ? AND (state = ? OR "
                "(state = ? AND claimed_at < ?)) ORDER BY rowid LIMIT 1",
                (run_id, PENDING, CLAIMED, now - lease_seconds),
            )
            row = cur.fetchone()
        if row is None:
            return None
        attempts = row["attempts"] + 1
        if attempts > max_attempts:
            self.abandon(
                run_id, row["key"], f"claimed {row['attempts']} times without result"
            )
            return self.claim(run_id, now, lease_seconds, max_attempts)
        self._conn.execute(
            "UPDATE work SET state = ?, claimed_at = ?, attempts = ? "
            "WHERE run_id = ? AND key = ?",
            (CLAIMED, now, attempts, run_id, row["key"]),
        )
        self._conn.commit()
        return {"key": row["key"], "nodeid": row["nodeid"], "attempts": attempts}

    def complete(self, key: str, run_id: str, item: Classification) -> None:
        """Record a finished verdict and retire its work item."""
        self.record_verdict(run_id, item)
        self.put_cache(key, item)
        self._conn.execute(
            "UPDATE work SET state = ?, detail = NULL WHERE run_id = ? AND key = ?",
            (DONE, run_id, key),
        )
        self._conn.commit()

    def abandon(self, run_id: str, key: str, detail: str) -> None:
        self._conn.execute(
            "UPDATE work SET state = ?, detail = ? WHERE run_id = ? AND key = ?",
            (ABANDONED, detail, run_id, key),
        )
        self._conn.commit()

    def progress(self, run_id: str) -> Dict[str, int]:
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                "SELECT state, COUNT(*) AS n FROM work WHERE run_id = ? GROUP BY state",
                (run_id,),
            )
            return {row["state"]: row["n"] for row in cur.fetchall()}
