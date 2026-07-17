"""Per-session message persistence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.platform.persistence import JSONLStore
from careereng.utils import ensure_dir, now_iso, safe_file_stem, write_json, read_json


class SessionManager:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(workspace / "sessions")
        self.state_dir = ensure_dir(workspace / "sessions_state")

    def _session_store(self, session_id: str) -> JSONLStore:
        key = safe_file_stem(session_id.replace(":", "-"))
        return JSONLStore(self.sessions_dir / f"{key}.jsonl")

    def _state_path(self, session_id: str) -> Path:
        key = safe_file_stem(session_id.replace(":", "-"))
        return self.state_dir / f"{key}.json"

    def append_message(self, session_id: str, role: str, content: str, **metadata: Any) -> None:
        self._session_store(session_id).append(
            {
                "ts": now_iso(),
                "session_id": session_id,
                "role": role,
                "content": content,
                "metadata": metadata,
            }
        )

    def get_recent_messages(self, session_id: str, limit: int = 50) -> list[dict[str, str]]:
        rows = self._session_store(session_id).read_last(limit)
        out: list[dict[str, str]] = []
        for row in rows:
            role = str(row.get("role") or "")
            content = str(row.get("content") or "")
            if role in {"user", "assistant", "system"} and content:
                out.append({"role": role, "content": content})
        return out

    def get_state(self, session_id: str) -> dict[str, Any]:
        return read_json(self._state_path(session_id))

    def update_state(self, session_id: str, state: dict[str, Any]) -> None:
        write_json(self._state_path(session_id), state)

    def clear_state(self, session_id: str) -> None:
        path = self._state_path(session_id)
        if path.exists():
            path.unlink()
