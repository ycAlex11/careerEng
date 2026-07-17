"""Reusable JSON and Markdown report artifact helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .store import ReportArtifactStore
from careereng.utils import ensure_dir, write_json


@dataclass(frozen=True)
class JsonMarkdownArtifactPaths:
    json_path: Path
    markdown_path: Path


class JsonMarkdownArtifact:
    """Base for artifacts that persist a machine payload and Markdown view."""

    def __init__(
        self,
        *,
        paths: JsonMarkdownArtifactPaths,
        store: ReportArtifactStore | None = None,
        artifact_id: str = "",
        domain: str = "",
        report_type: str = "",
        metadata: dict[str, Any] | None = None,
        snapshot_existing: bool = False,
    ):
        self.paths = paths
        self.store = store
        self.artifact_id = str(artifact_id or "").strip()
        self.domain = str(domain or "").strip()
        self.report_type = str(report_type or "").strip()
        self.metadata = dict(metadata or {})
        self.snapshot_existing = bool(snapshot_existing)

    def build_payload(self) -> dict[str, Any]:
        raise NotImplementedError

    def render_markdown(self, payload: dict[str, Any]) -> str:
        raise NotImplementedError

    def write(self) -> dict[str, Any]:
        payload = self.build_payload()
        markdown = self.render_markdown(payload)
        if self.store is not None:
            self.store.write_json_markdown(
                artifact_id=self.artifact_id or self.paths.json_path.stem,
                domain=self.domain or "unknown",
                report_type=self.report_type or "report",
                json_path=self.paths.json_path,
                markdown_path=self.paths.markdown_path,
                payload=payload,
                markdown=markdown,
                metadata=self.metadata,
                snapshot_existing=self.snapshot_existing,
            )
            return payload
        ensure_dir(self.paths.json_path.parent)
        ensure_dir(self.paths.markdown_path.parent)
        write_json(self.paths.json_path, payload)
        self.paths.markdown_path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
        return payload
