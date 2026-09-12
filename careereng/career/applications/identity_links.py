"""Evidence-backed associations; source job records remain immutable to this store."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from careereng.utils import ensure_dir, now_iso, safe_file_stem


class JobIdentityLinks:
    def __init__(self, workspace: Path | str, site_key: str):
        self.path = ensure_dir(Path(workspace) / "jobs" / "identity_links") / f"{safe_file_stem(site_key)}.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("CREATE TABLE IF NOT EXISTS events (revision INTEGER PRIMARY KEY, operation_id TEXT UNIQUE, payload TEXT NOT NULL)")
        return connection

    @staticmethod
    def _snapshot(connection: sqlite3.Connection) -> dict[str, Any]:
        events = [json.loads(row[0]) for row in connection.execute("SELECT payload FROM events ORDER BY revision")]
        active: dict[str, dict[str, Any]] = {}
        for event in events:
            if event["action"] == "confirm":
                active[event["operation_id"]] = event
            else:
                active.pop(event["association_id"], None)
        return {"revision": len(events), "associations": list(active.values()), "events": events}

    def snapshot(self) -> dict[str, Any]:
        with closing(self._connect()) as connection, connection:
            return self._snapshot(connection)

    def record(self, *, operation_id: str, expected_revision: int, action: str,
               evidence: str, canonical_job_id: str = "", keys: list[str] | None = None,
               association_id: str = "", observations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if not operation_id.strip() or not evidence.strip():
            raise ValueError("operation_id and evidence are required")
        if action not in {"confirm", "revoke"}:
            raise ValueError("identity action must be confirm or revoke")
        normalized_keys = sorted(set(keys or []))
        if action == "confirm" and (not canonical_job_id or not normalized_keys):
            raise ValueError("confirmation requires canonical identity and strong source keys")
        payload = dict(operation_id=operation_id, action=action, evidence=evidence,
                       canonical_job_id=canonical_job_id, keys=normalized_keys,
                       association_id=association_id, observations=observations or [])
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            snapshot = self._snapshot(connection)
            previous = next((event for event in snapshot["events"] if event["operation_id"] == operation_id), None)
            if previous:
                if any(previous.get(key) != value for key, value in payload.items()):
                    raise ValueError("operation_id reused with different identity evidence")
                return previous
            if snapshot["revision"] != expected_revision:
                raise ValueError("identity revision changed; inspect before retrying")
            if action == "confirm":
                for entry in snapshot["associations"]:
                    if set(entry["keys"]) & set(normalized_keys) and entry["canonical_job_id"] != canonical_job_id:
                        raise ValueError("conflicting identity association; revoke before correcting")
            elif not any(entry["operation_id"] == association_id for entry in snapshot["associations"]):
                raise ValueError("active association not found")
            payload.update(revision=snapshot["revision"] + 1, recorded_at=now_iso())
            connection.execute("INSERT INTO events VALUES (?, ?, ?)",
                               (payload["revision"], operation_id, json.dumps(payload, ensure_ascii=False)))
            return payload

    @staticmethod
    def resolve(keys: list[str], snapshot: dict[str, Any]) -> str:
        candidates = {entry["canonical_job_id"] for entry in snapshot["associations"]
                      if set(entry["keys"]) & set(keys)}
        return next(iter(candidates)) if len(candidates) == 1 else ""
