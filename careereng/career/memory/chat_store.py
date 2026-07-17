"""Global chat logs (all + daily)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.platform.persistence import JSONLStore
from careereng.utils import ensure_dir, now_iso, today_str


class ChatStore:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.chat_dir = ensure_dir(workspace / "chat")
        self.daily_dir = ensure_dir(self.chat_dir / "daily")
        self.all_store = JSONLStore(self.chat_dir / "all.jsonl")

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        is_profile_related: bool = False,
        is_intent_related: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = {
            "ts": now_iso(),
            "date": today_str(),
            "session_id": session_id,
            "role": role,
            "content": content,
            "is_profile_related": is_profile_related,
            "is_intent_related": is_intent_related,
            "metadata": metadata or {},
        }
        self.all_store.append(row)
        JSONLStore(self.daily_dir / f"{today_str()}.jsonl").append(row)
        return row

    def recent_related(self, session_id: str, domain: str, limit: int = 6) -> list[dict[str, Any]]:
        rows = self.all_store.read_all()
        key = "is_profile_related" if domain == "profile" else "is_intent_related"
        matched = [
            r
            for r in rows
            if r.get("session_id") == session_id and r.get(key) and str(r.get("role")) in {"user", "assistant"}
        ]
        return matched[-limit:]
