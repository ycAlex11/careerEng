"""Serializable phase-session contracts shared by providers and external agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from careereng.utils import ensure_dir, now_iso, safe_file_stem, write_json


@dataclass(frozen=True)
class PhaseSession:
    site_key: str
    site_name: str
    entry_url: str
    session_id: str
    turn_id: str
    batch_id: str
    current_phase: str
    phase_slugs: tuple[str, ...] = ()
    apply_target_job_ids: tuple[str, ...] = ()
    continuation_context: dict[str, Any] = field(default_factory=dict)
    phase_context: dict[str, Any] = field(default_factory=dict)
    browser_tool_commands: dict[str, str] = field(default_factory=dict)
    state_tool_commands: dict[str, str] = field(default_factory=dict)
    state_tools: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "created_at": now_iso(),
            "site_key": self.site_key,
            "site_name": self.site_name,
            "entry_url": self.entry_url,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "batch_id": self.batch_id,
            "current_phase": self.current_phase,
            "phase_slugs": list(self.phase_slugs),
            "apply_target_job_ids": list(self.apply_target_job_ids),
            "continuation_context": dict(self.continuation_context or {}),
            "phase_context": dict(self.phase_context or {}),
            "browser_tool_commands": dict(self.browser_tool_commands or {}),
            "state_tool_commands": dict(self.state_tool_commands or {}),
            "state_tools": list(self.state_tools or []),
        }


def phase_session_dir(*, workspace: Path, site_key: str, batch_id: str, turn_id: str) -> Path:
    return ensure_dir(
        Path(workspace).resolve()
        / "phase_runtime"
        / "sessions"
        / safe_file_stem(site_key)
        / f"{safe_file_stem(batch_id or 'adhoc_batch')}_{safe_file_stem(turn_id or 'turn')}"
    )


def write_phase_session(session: PhaseSession, *, workspace: Path, path: Path | None = None) -> Path:
    output_path = Path(path) if path is not None else phase_session_dir(
        workspace=workspace,
        site_key=session.site_key,
        batch_id=session.batch_id,
        turn_id=session.turn_id,
    ) / "session.json"
    write_json(output_path, session.as_dict())
    return output_path
