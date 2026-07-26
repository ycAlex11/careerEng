"""Append-only transport facts for local external-agent execution."""

from __future__ import annotations

import hashlib
from pathlib import Path
import threading
from typing import Any

from careereng.platform.persistence import JSONLStore
from careereng.utils import now_iso


_WRITE_LOCK = threading.Lock()
_MAX_TEXT_BYTES = 4096


class AgentTransportTrace:
    """Persist raw agent transport facts without interpreting workflow state."""

    def __init__(self, workspace: Path | str | None):
        self.workspace = Path(workspace).resolve() if workspace else None

    @property
    def path(self) -> Path | None:
        if self.workspace is None:
            return None
        return self.workspace / "metrics" / "agent_transport_events.jsonl"

    def record(self, *, backend: str, event: str, **payload: Any) -> None:
        path = self.path
        if path is None:
            return
        row = {
            "ts": now_iso(),
            "backend": str(backend or "external_agent"),
            "event": str(event or "unknown"),
            **{key: _compact(value) for key, value in payload.items()},
        }
        try:
            with _WRITE_LOCK:
                JSONLStore(path).append(row)
        except Exception:
            return


def _compact(value: Any) -> Any:
    """Keep diagnostic structure while preventing prompts and snapshots from bloating traces."""

    if isinstance(value, dict):
        return {str(key): _compact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_compact(item) for item in value[:100]]
    if isinstance(value, tuple):
        return [_compact(item) for item in value[:100]]
    if not isinstance(value, str):
        return value
    encoded = value.encode("utf-8")
    if len(encoded) <= _MAX_TEXT_BYTES:
        return value
    return {
        "truncated": True,
        "bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "preview": encoded[:_MAX_TEXT_BYTES].decode("utf-8", errors="replace"),
    }


__all__ = ["AgentTransportTrace"]
