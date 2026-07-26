"""Normalize Codex App Server frames into CareerEng's Codex protocol."""

from __future__ import annotations

from typing import Any

from careereng.orchestration.agent_protocol.codex import (
    codex_event,
    codex_operation_failed,
    codex_operation_succeeded,
)

from .app_server import CodexAppServerEvent


def normalize_codex_operation(
    operation: str,
    result: dict[str, Any] | None,
    *,
    thread_id: str = "",
) -> dict[str, Any]:
    """Translate one synchronous App Server result to the fixed contract."""

    payload = dict(result or {})
    thread = payload.get("thread") if isinstance(payload.get("thread"), dict) else {}
    turn = payload.get("turn") if isinstance(payload.get("turn"), dict) else {}
    resolved_thread_id = str(thread.get("id") or payload.get("threadId") or thread_id or "")
    resolved_turn_id = str(turn.get("id") or payload.get("turnId") or "")
    if operation in {"thread_start", "thread_resume"} and not resolved_thread_id:
        return codex_operation_failed(operation, "Codex App Server returned no thread id")
    if operation == "turn_start" and not resolved_turn_id:
        return codex_operation_failed(operation, "Codex App Server returned no turn id")
    return codex_operation_succeeded(
        operation,
        thread_id=resolved_thread_id,
        turn_id=resolved_turn_id,
    )


def normalize_codex_event(event: CodexAppServerEvent) -> dict[str, Any]:
    """Translate one asynchronous App Server notification."""

    params = dict(event.params)
    thread = params.get("thread") if isinstance(params.get("thread"), dict) else {}
    turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
    thread_id = str(params.get("threadId") or thread.get("id") or "")
    turn_id = str(params.get("turnId") or turn.get("id") or "")
    if event.method == "thread/started":
        return codex_event("thread_started", thread_id=thread_id, payload=params)
    if event.method == "thread/tokenUsage/updated":
        return codex_event("usage", thread_id=thread_id, payload=params)
    if event.method == "turn/completed":
        return codex_event("turn_completed", thread_id=thread_id, turn_id=turn_id, payload=params)
    return codex_event(
        "notification",
        thread_id=thread_id,
        turn_id=turn_id,
        payload={"method": event.method, "params": params},
    )


def normalize_codex_trace(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep transport telemetry separate from lifecycle notifications."""

    data = dict(payload)
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    thread = params.get("thread") if isinstance(params.get("thread"), dict) else {}
    turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
    return codex_event(
        "transport",
        thread_id=str(data.get("thread_id") or params.get("threadId") or thread.get("id") or ""),
        turn_id=str(data.get("turn_id") or params.get("turnId") or turn.get("id") or ""),
        payload=data,
    )
