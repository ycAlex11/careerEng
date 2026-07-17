"""State tracking for assistant-curated recent conversation intake."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.utils import ensure_dir, now_iso, read_json, write_json


INTAKE_STATE_PATH = Path("assistant_bridge") / "intake_state.json"


def intake_state_path(workspace: Path | str) -> Path:
    return Path(workspace) / INTAKE_STATE_PATH


def load_recent_intake_state(workspace: Path | str) -> dict[str, Any]:
    return read_json(intake_state_path(workspace))


def save_recent_intake_state(
    *,
    workspace: Path | str,
    import_result: dict[str, Any],
    source_file: Path | str,
    source_limit: int,
    source_thread: str,
    source_client: str,
    context_path: Path | str,
) -> dict[str, Any]:
    """Persist the latest manual recent-N intake result for future assistant context."""
    now = now_iso()
    path = intake_state_path(workspace)
    ensure_dir(path.parent)
    state = {
        "last_imported_at": now,
        "last_source_limit": max(0, int(source_limit or 0)),
        "last_source_thread": str(source_thread or "").strip(),
        "last_source_client": str(source_client or "").strip(),
        "last_candidate_file": str(source_file),
        "last_created_memory_count": int(import_result.get("created") or 0),
        "last_created_lesson_count": int(import_result.get("created_lessons") or 0),
        "last_created_evolution_evidence_count": int(import_result.get("created_evolution_evidence") or 0),
        "last_skipped_existing": int(import_result.get("skipped_existing") or 0),
        "last_read_count": int(import_result.get("read") or 0),
        "last_memory_ids": list(import_result.get("memory_ids") or []),
        "last_lesson_ids": list(import_result.get("lesson_ids") or []),
        "last_evidence_ids": list(import_result.get("evidence_ids") or []),
        "last_context_refreshed_at": now,
        "last_context_path": str(context_path),
    }
    write_json(path, state)
    return state
