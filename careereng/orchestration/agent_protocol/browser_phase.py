"""Provider-independent result contract for one browser phase."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BrowserPhaseResult:
    status: str
    reason_tag: str
    summary: str
    current_url: str = ""
    step_count: int = 0
    trace_ref: str = ""
    raw_text: str = ""
    recorded_count: int = 0
    new_count: int = 0
