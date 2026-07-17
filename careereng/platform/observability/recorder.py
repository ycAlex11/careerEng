"""Append-only LLM usage metrics."""

from __future__ import annotations

from pathlib import Path
import threading
from typing import Any

from careereng.platform.persistence import JSONLStore
from careereng.utils import now_iso


_WRITE_LOCK = threading.Lock()


def _dump_object(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        return dumped if isinstance(dumped, dict) else {}
    return {}


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def extract_usage(usage: Any) -> dict[str, Any]:
    """Normalize chat/completions and responses usage shapes."""
    data = _dump_object(usage)
    input_tokens = _int_or_none(data.get("input_tokens", data.get("prompt_tokens")))
    output_tokens = _int_or_none(data.get("output_tokens", data.get("completion_tokens")))
    total_tokens = _int_or_none(data.get("total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "usage": data,
    }


class LLMUsageRecorder:
    """Write one JSONL row per LLM request without affecting runtime behavior."""

    def __init__(self, workspace: Path | str | None):
        self.workspace = Path(workspace).resolve() if workspace else None

    @property
    def path(self) -> Path | None:
        if self.workspace is None:
            return None
        return self.workspace / "metrics" / "llm_usage.jsonl"

    def record(self, **payload: Any) -> None:
        path = self.path
        if path is None:
            return
        row = {
            "ts": now_iso(),
            **payload,
        }
        try:
            with _WRITE_LOCK:
                JSONLStore(path).append(row)
        except Exception:
            return
