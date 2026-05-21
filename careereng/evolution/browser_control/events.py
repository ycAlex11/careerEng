"""Structured event capture for browser-control self-improvement."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.storage.jsonl import JSONLStore
from careereng.utils import now_iso


def phase_events_path(workspace: Path | str) -> Path:
    return Path(workspace) / "evolution" / "browser_control" / "phase_events.jsonl"


def append_phase_event(
    *,
    workspace: Path | str,
    event_type: str,
    batch_id: str = "",
    site_key: str = "",
    phase: str = "",
    turn_id: str = "",
    current_url: str = "",
    guard_name: str = "",
    trigger_values: dict[str, Any] | None = None,
    last_record_jobs_policy: dict[str, Any] | None = None,
    trace_ref: str = "",
    summary: str = "",
) -> Path:
    path = phase_events_path(workspace)
    JSONLStore(path).append(
        {
            "created_at": now_iso(),
            "event_type": str(event_type or "").strip(),
            "batch_id": str(batch_id or "").strip(),
            "site_key": str(site_key or "").strip(),
            "phase": str(phase or "").strip(),
            "turn_id": str(turn_id or "").strip(),
            "current_url": str(current_url or "").strip(),
            "guard_name": str(guard_name or "").strip(),
            "trigger_values": dict(trigger_values or {}),
            "last_record_jobs_policy": dict(last_record_jobs_policy or {}),
            "trace_ref": str(trace_ref or "").strip(),
            "summary": str(summary or "").strip(),
        }
    )
    return path
