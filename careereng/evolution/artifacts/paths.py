"""Stable workspace paths for evolution artifacts."""

from __future__ import annotations

from pathlib import Path

from careereng.utils import safe_file_stem


def evolution_root(workspace: Path | str) -> Path:
    return Path(workspace) / "evolution"


def evidence_path(workspace: Path | str) -> Path:
    return evolution_root(workspace) / "evidence" / "all.jsonl"


def open_candidates_path(workspace: Path | str) -> Path:
    return evolution_root(workspace) / "candidates" / "open.jsonl"


def memory_units_path(workspace: Path | str) -> Path:
    return evolution_root(workspace) / "memory" / "units.jsonl"


def review_dir(workspace: Path | str) -> Path:
    return evolution_root(workspace) / "reviews"


def context_path(workspace: Path | str) -> Path:
    return evolution_root(workspace) / "context" / "latest.md"


def workflow_summary_paths(workspace: Path | str, batch_id: str) -> tuple[Path, Path]:
    stem = safe_file_stem(str(batch_id or ""))
    root = evolution_root(workspace) / "workflow_summaries"
    return root / f"{stem}.json", root / f"{stem}.md"
