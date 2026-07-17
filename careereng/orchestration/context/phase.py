"""Serializable, current-phase context for any CareerEng agent driver."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _phase_memory_text(value: Any | None) -> str:
    if value is None:
        return ""
    render = getattr(value, "phase_memory_text", None)
    if callable(render):
        try:
            return str(render() or "").strip()
        except Exception:
            return ""
    return str(value or "").strip() if isinstance(value, str) else ""


@dataclass(frozen=True)
class PhaseContext:
    """Only the context required to execute the current phase."""

    phase_slug: str
    phase_title: str
    project_skill: str = ""
    site_skill: str = ""
    phase_memory: str = ""
    continuation: dict[str, Any] = field(default_factory=dict)
    local_state: dict[str, Any] = field(default_factory=dict)

    @property
    def combined_guidance(self) -> str:
        parts: list[str] = []
        if self.site_skill:
            parts.append("Site skill guidance:\n" + self.site_skill)
        if self.project_skill:
            parts.append("Project skill guidance:\n" + self.project_skill)
        return "\n\n".join(parts).strip()

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": {"slug": self.phase_slug, "title": self.phase_title},
            "project_skill": self.project_skill,
            "site_skill": self.site_skill,
            "phase_memory": self.phase_memory,
            "continuation": dict(self.continuation or {}),
            "local_state": dict(self.local_state or {}),
        }


def build_phase_context(
    phase: Any,
    *,
    phase_memory: Any | None = None,
    continuation: dict[str, Any] | None = None,
    local_state: dict[str, Any] | None = None,
) -> PhaseContext:
    """Build a driver-neutral context without deciding phase business policy."""

    return PhaseContext(
        phase_slug=str(getattr(phase, "slug", "") or "").strip(),
        phase_title=str(getattr(phase, "title", "") or "").strip(),
        project_skill=str(getattr(phase, "project_text", "") or "").strip(),
        site_skill=str(getattr(phase, "site_text", "") or "").strip(),
        phase_memory=_phase_memory_text(phase_memory),
        continuation=dict(continuation or {}),
        local_state=dict(local_state or {}),
    )
