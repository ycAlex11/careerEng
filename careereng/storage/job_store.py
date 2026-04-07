"""Batch storage for retrieve/apply workflows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.storage.jsonl import JSONLStore
from careereng.utils import ensure_dir, make_id, now_iso, read_json, write_json


TERMINAL_BATCH_STATUSES = {"completed", "partial_completed", "failed", "cancelled"}


class JobStore:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.jobs_dir = ensure_dir(workspace / "jobs")
        self.batches_dir = ensure_dir(self.jobs_dir / "batches")
        self.events = JSONLStore(self.jobs_dir / "events.jsonl")

    def _batch_path(self, batch_id: str) -> Path:
        return self.batches_dir / f"{batch_id}.json"

    def create_batch(
        self,
        *,
        session_id: str,
        turn_id: str,
        user_message: str,
        apply_requested: bool,
        sites: list[dict[str, Any]],
    ) -> dict[str, Any]:
        batch_id = make_id("job_batch")
        now = now_iso()
        payload = {
            "batch_id": batch_id,
            "session_id": session_id,
            "turn_id": turn_id,
            "user_message": user_message,
            "apply_requested": bool(apply_requested),
            "status": "running",
            "created_at": now,
            "updated_at": now,
            "sites": {str(row.get("site_key") or ""): row for row in sites if str(row.get("site_key") or "")},
        }
        write_json(self._batch_path(batch_id), payload)
        self.append_event(
            "batch.created",
            {
                "batch_id": batch_id,
                "session_id": session_id,
                "turn_id": turn_id,
                "apply_requested": bool(apply_requested),
                "site_count": len(payload["sites"]),
            },
        )
        return payload

    def load_batch(self, batch_id: str) -> dict[str, Any]:
        return read_json(self._batch_path(batch_id))

    def save_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        batch_id = str(payload.get("batch_id") or "")
        if not batch_id:
            return {}
        payload = dict(payload)
        payload["updated_at"] = now_iso()
        write_json(self._batch_path(batch_id), payload)
        return payload

    def append_event(self, name: str, payload: dict[str, Any]) -> None:
        self.events.append(
            {
                "event_id": make_id("job_evt"),
                "ts": now_iso(),
                "name": name,
                "payload": payload,
            }
        )

    def latest_open_batch(self, session_id: str) -> dict[str, Any] | None:
        rows = self.list_batches(session_id=session_id)
        for row in rows:
            if str(row.get("status") or "") not in TERMINAL_BATCH_STATUSES:
                return row
        return rows[0] if rows else None

    def list_batches(self, *, session_id: str | None = None, include_terminal: bool = True) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in sorted(self.batches_dir.glob("*.json")):
            row = read_json(path)
            if not row:
                continue
            if session_id and str(row.get("session_id") or "") != session_id:
                continue
            if not include_terminal and str(row.get("status") or "") in TERMINAL_BATCH_STATUSES:
                continue
            rows.append(row)
        rows.sort(key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""), reverse=True)
        return rows

    def clear_open_batches(self, *, session_id: str | None = None, status: str = "cancelled") -> list[dict[str, Any]]:
        cleared: list[dict[str, Any]] = []
        for row in self.list_batches(session_id=session_id, include_terminal=False):
            payload = dict(row)
            payload["status"] = status
            payload["closed_at"] = now_iso()
            saved = self.save_batch(payload)
            self.append_event(
                "batch.cleared",
                {
                    "batch_id": str(saved.get("batch_id") or ""),
                    "session_id": str(saved.get("session_id") or ""),
                    "new_status": status,
                },
            )
            cleared.append(saved)
        return cleared

    def update_site(self, batch: dict[str, Any], site_key: str, patch: dict[str, Any]) -> dict[str, Any]:
        batch = dict(batch or {})
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        current = dict(sites.get(site_key) or {"site_key": site_key})
        current.update(patch or {})
        sites[site_key] = current
        batch["sites"] = sites
        return self.save_batch(batch)
