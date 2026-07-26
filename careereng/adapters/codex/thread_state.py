"""Persist the small CareerEng-to-Codex thread linkage beside work orders."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.utils import now_iso, read_json, write_json


def bind_work_order_thread(
    *,
    payload_path: Path,
    phase_session_path: Path,
    thread_id: str,
    turn_id: str = "",
    status: str,
    worker_session_id: str = "",
    session_batch_ordinal: int = 0,
    session_reused: bool = False,
    session_rotation_reason: str = "",
    last_error: str = "",
    recovery_attempts: int = 0,
) -> dict[str, Any]:
    """Record an adapter-owned thread id without introducing a second store."""

    payload = read_json(Path(payload_path))
    phase_session = read_json(Path(phase_session_path))
    if not isinstance(payload, dict) or not isinstance(phase_session, dict):
        raise ValueError("Codex thread binding requires an active work order")
    binding = {
        "thread_id": str(thread_id),
        "turn_id": str(turn_id),
        "status": str(status),
        "updated_at": now_iso(),
        "worker_session_id": str(worker_session_id or ""),
        "session_batch_ordinal": int(session_batch_ordinal or 0),
        "session_reused": bool(session_reused),
        "session_rotation_reason": str(session_rotation_reason or ""),
        "last_error": str(last_error or ""),
        "recovery_attempts": int(recovery_attempts or 0),
    }
    for target in (payload, phase_session):
        target["codex_thread"] = binding
        target["updated_at"] = binding["updated_at"]
    write_json(Path(payload_path), payload)
    write_json(Path(phase_session_path), phase_session)
    return binding


def load_work_order_binding(payload_path: Path) -> dict[str, Any]:
    payload = read_json(Path(payload_path))
    binding = payload.get("codex_thread") if isinstance(payload, dict) else None
    return dict(binding) if isinstance(binding, dict) else {}
