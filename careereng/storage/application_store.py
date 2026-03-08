"""Global application storage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.storage.jsonl import JSONLStore
from careereng.utils import ensure_dir, make_id, now_iso


class ApplicationStore:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.app_dir = ensure_dir(workspace / "applications")
        self.all_store = JSONLStore(self.app_dir / "all.jsonl")
        self.events_store = JSONLStore(self.app_dir / "events.jsonl")

    def append_application(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = {
            "application_id": payload.get("application_id") or make_id("app"),
            "ts": now_iso(),
            **payload,
        }
        self.all_store.append(row)
        return row

    def append_event(self, name: str, payload: dict[str, Any]) -> None:
        self.events_store.append(
            {
                "event_id": make_id("app_evt"),
                "ts": now_iso(),
                "name": name,
                "payload": payload,
            }
        )
