"""Router decision and feedback storage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.storage.jsonl import JSONLStore
from careereng.utils import ensure_dir, make_id, now_iso


class RouterStore:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.router_dir = ensure_dir(workspace / "router")
        self.events = JSONLStore(self.router_dir / "events.jsonl")
        self.feedback = JSONLStore(self.router_dir / "feedback.jsonl")

    def append_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = {
            "route_event_id": payload.get("route_event_id") or make_id("route_evt"),
            "ts": now_iso(),
            **payload,
        }
        self.events.append(row)
        return row

    def append_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = {
            "feedback_id": payload.get("feedback_id") or make_id("route_fb"),
            "ts": now_iso(),
            **payload,
        }
        self.feedback.append(row)
        return row

    def find_event(self, route_event_id: str) -> dict[str, Any] | None:
        rid = str(route_event_id or "").strip()
        if not rid:
            return None
        for row in reversed(self.events.read_all()):
            if str(row.get("route_event_id") or "") == rid:
                return row
        return None
