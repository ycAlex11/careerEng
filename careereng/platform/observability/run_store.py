"""Run summary storage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.platform.persistence import JSONLStore
from careereng.utils import ensure_dir, now_iso, today_str


class RunStore:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.daily_dir = ensure_dir(workspace / "runs" / "daily")

    def append(self, summary: dict[str, Any]) -> None:
        row = {
            "ts": now_iso(),
            **summary,
        }
        JSONLStore(self.daily_dir / f"{today_str()}.jsonl").append(row)
