"""CareerEng's stable contract for Codex App Server operations.

The Codex adapter owns conversion from App Server JSON-RPC frames.  Callers
only consume these compact payloads and never depend on raw notification
ordering or App Server response shapes.
"""

from __future__ import annotations

from typing import Any


def codex_operation_succeeded(
    operation: str,
    *,
    thread_id: str = "",
    turn_id: str = "",
) -> dict[str, Any]:
    """Build the fixed successful result used by the Codex adapter."""

    return {
        "ok": True,
        "operation": str(operation),
        "thread_id": str(thread_id or ""),
        "turn_id": str(turn_id or ""),
        "error": None,
    }


def codex_operation_failed(operation: str, error: str) -> dict[str, Any]:
    """Build the fixed failed result used by the Codex adapter."""

    return {
        "ok": False,
        "operation": str(operation),
        "thread_id": "",
        "turn_id": "",
        "error": str(error or "Codex App Server operation failed"),
    }


def codex_event(
    kind: str,
    *,
    thread_id: str = "",
    turn_id: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a fixed asynchronous Codex event payload."""

    return {
        "kind": str(kind),
        "thread_id": str(thread_id or ""),
        "turn_id": str(turn_id or ""),
        "payload": dict(payload or {}),
    }
