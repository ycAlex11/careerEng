"""Persistence owner for the compact evolution context pack."""

from __future__ import annotations

from pathlib import Path

from careereng.evolution.artifacts.paths import context_path
from careereng.utils import ensure_dir


class EvolutionContextStore:
    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.path = context_path(self.workspace)

    def save_markdown(self, text: str) -> Path:
        ensure_dir(self.path.parent)
        self.path.write_text(text.rstrip() + "\n", encoding="utf-8")
        return self.path
