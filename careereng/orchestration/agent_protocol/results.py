"""Shared phase-result helpers."""

from __future__ import annotations

from typing import Any


def phase_result_payload(arguments: dict[str, Any]) -> dict[str, Any]:
    status = str(arguments.get("status") or "").strip()
    summary = str(arguments.get("summary") or "").strip()
    if status not in {"done", "waiting_user", "blocked"}:
        return {
            "isError": True,
            "error": "phase_result status must be `done`, `waiting_user`, or `blocked`",
            "structuredContent": {"status": status, "summary": summary},
            "content": [{"type": "text", "text": "Invalid phase_result status."}],
        }
    return {
        "isError": False,
        "structuredContent": {"status": status, "summary": summary},
        "content": [{"type": "text", "text": f"Phase result recorded: {status}. {summary}".strip()}],
    }
