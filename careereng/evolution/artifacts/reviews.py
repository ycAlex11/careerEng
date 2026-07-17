"""Persistence owner for rendered evolution reviews."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.evolution.artifacts.paths import review_dir
from careereng.utils import ensure_dir, today_str, write_json


class EvolutionReviewStore:
    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.root = review_dir(self.workspace)

    def json_path(self, *, date_label: str | None = None) -> Path:
        return self.root / f"{date_label or today_str()}.json"

    def markdown_path(self, *, date_label: str | None = None) -> Path:
        return self.root / f"{date_label or today_str()}.md"

    def save_json(self, review: dict[str, Any], *, date_label: str | None = None) -> Path:
        path = self.json_path(date_label=date_label)
        ensure_dir(path.parent)
        write_json(path, review)
        return path

    def save_markdown(self, text: str, *, date_label: str | None = None) -> Path:
        path = self.markdown_path(date_label=date_label)
        ensure_dir(path.parent)
        path.write_text(text.rstrip() + "\n", encoding="utf-8")
        return path
