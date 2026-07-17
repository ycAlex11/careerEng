"""Thread-scope state for assistant bridge conversations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.utils import ensure_dir, make_id, now_iso, read_json, write_json


class AssistantThreadStateStore:
    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.path = ensure_dir(self.workspace / "assistant_bridge") / "thread_state.json"
        if not self.path.exists():
            write_json(self.path, {"threads": {}})

    @staticmethod
    def thread_key(*, client: str, thread_id: str) -> str:
        client_text = str(client or "unknown").strip() or "unknown"
        thread_text = str(thread_id or "default").strip() or "default"
        return f"{client_text}:{thread_text}"

    def load(self) -> dict[str, Any]:
        data = read_json(self.path)
        if not isinstance(data.get("threads"), dict):
            data["threads"] = {}
        return data

    def save(self, data: dict[str, Any]) -> None:
        if not isinstance(data.get("threads"), dict):
            data["threads"] = {}
        write_json(self.path, data)

    def get(self, *, client: str, thread_id: str) -> dict[str, Any]:
        data = self.load()
        row = data.get("threads", {}).get(self.thread_key(client=client, thread_id=thread_id), {})
        return dict(row) if isinstance(row, dict) else {}

    def open_scope(
        self,
        *,
        client: str,
        thread_id: str,
        category: str,
        topic: str,
        opened_by_event_id: str,
    ) -> dict[str, Any]:
        data = self.load()
        key = self.thread_key(client=client, thread_id=thread_id)
        now = now_iso()
        current = data["threads"].get(key, {})
        row = {
            "thread_id": str(thread_id or "default"),
            "client": str(client or "unknown"),
            "active": True,
            "scope_id": str(current.get("scope_id") or make_id("ascope")),
            "active_category": str(category or ""),
            "topic": str(topic or ""),
            "opened_by_event_id": str(opened_by_event_id or ""),
            "opened_at": str(current.get("opened_at") or now),
            "last_seen_at": now,
            "expires_at": "",
        }
        data["threads"][key] = row
        self.save(data)
        return row

    def touch(self, *, client: str, thread_id: str) -> dict[str, Any]:
        data = self.load()
        key = self.thread_key(client=client, thread_id=thread_id)
        row = data["threads"].get(key, {})
        if isinstance(row, dict) and row:
            row["last_seen_at"] = now_iso()
            data["threads"][key] = row
            self.save(data)
            return dict(row)
        return {}

    def close_scope(self, *, client: str, thread_id: str) -> dict[str, Any]:
        data = self.load()
        key = self.thread_key(client=client, thread_id=thread_id)
        row = data["threads"].get(key, {})
        if not isinstance(row, dict) or not row:
            row = {
                "thread_id": str(thread_id or "default"),
                "client": str(client or "unknown"),
                "scope_id": "",
                "active_category": "",
                "topic": "",
                "opened_by_event_id": "",
                "opened_at": "",
                "expires_at": "",
            }
        row["active"] = False
        row["last_seen_at"] = now_iso()
        data["threads"][key] = row
        self.save(data)
        return dict(row)
