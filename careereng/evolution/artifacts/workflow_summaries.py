"""Persistence owner for batch workflow evolution summaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.evolution.artifacts.paths import workflow_summary_paths
from careereng.utils import ensure_dir, read_json, write_json


class WorkflowEvolutionSummaryStore:
    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)

    def paths_for_batch(self, batch_id: str) -> tuple[Path, Path]:
        return workflow_summary_paths(self.workspace, batch_id)

    def load(self, batch_id: str) -> dict[str, Any]:
        json_path, _ = self.paths_for_batch(batch_id)
        payload = read_json(json_path)
        return payload if isinstance(payload, dict) else {}

    def save(self, *, batch_id: str, payload: dict[str, Any], markdown: str) -> tuple[Path, Path]:
        json_path, markdown_path = self.paths_for_batch(batch_id)
        ensure_dir(json_path.parent)
        write_json(json_path, payload)
        markdown_path.write_text(markdown, encoding="utf-8")
        return json_path, markdown_path
