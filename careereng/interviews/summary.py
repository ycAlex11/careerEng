"""Lightweight local interview summaries."""

from __future__ import annotations

from typing import Any

from careereng.interviews.store import InterviewStore


def build_interview_summary(store: InterviewStore, session_id: str, *, recent_limit: int = 5) -> dict[str, Any]:
    session = store.get_session(session_id)
    return {
        "session": session,
        "counts": store.session_counts(session_id),
        "recent": store.recent_rows(session_id, limit=recent_limit),
    }


def render_interview_summary(summary: dict[str, Any]) -> str:
    session = summary.get("session") if isinstance(summary.get("session"), dict) else {}
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    recent = summary.get("recent") if isinstance(summary.get("recent"), dict) else {}
    lines = [
        "# Interview Session",
        "",
        f"- Session: `{session.get('session_id') or ''}`",
        f"- Status: `{session.get('status') or ''}`",
        f"- Company: {session.get('company') or '-'}",
        f"- Title: {session.get('title') or '-'}",
        f"- Site: `{session.get('site_key') or ''}`",
        f"- URL: {session.get('url') or '-'}",
        f"- Application Status: {session.get('application_status') or '-'}",
        f"- Application Stage: {session.get('application_stage') or '-'}",
        "",
        "## Counts",
        "",
    ]
    for key in ("prep_events", "predicted_questions", "turns", "suggestions", "evidence", "audio_chunks"):
        lines.append(f"- {key}: {int(counts.get(key) or 0)}")

    lines.extend(["", "## Recent Records", ""])
    for key in ("prep_events", "predicted_questions", "turns", "suggestions", "evidence", "audio_chunks"):
        rows = recent.get(key) if isinstance(recent.get(key), list) else []
        lines.append(f"### {key}")
        if not rows:
            lines.append("- none")
            continue
        for row in rows:
            lines.append(f"- {_record_line(row)}")
    return "\n".join(lines).rstrip() + "\n"


def _record_line(row: dict[str, Any]) -> str:
    for key in ("summary", "question", "raw_text", "suggested_answer", "audio_path"):
        text = str(row.get(key) or "").strip()
        if text:
            return text[:240]
    return str(row.get("evidence_id") or row.get("turn_id") or row.get("created_at") or "record")
