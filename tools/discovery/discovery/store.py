"""Durable state for the loop.

All pipeline state lives here rather than in any agent's context, so a run is
resumable after a crash and auditable after a bad batch.
"""

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from typing import Any, Dict, Iterable, List, Optional

from .model import Classification

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
CREATE INDEX IF NOT EXISTS verdicts_by_kind ON verdicts (verdict);
"""


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
