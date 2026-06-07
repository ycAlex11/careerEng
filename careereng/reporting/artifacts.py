"""Reusable JSON + Markdown report/summary artifact helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from careereng.utils import ensure_dir, write_json


@dataclass(frozen=True)
class JsonMarkdownArtifactPaths:
    json_path: Path
    markdown_path: Path


class JsonMarkdownArtifact:
    """Base for artifacts that persist a machine payload and a Markdown view."""

    def __init__(self, *, paths: JsonMarkdownArtifactPaths):
        self.paths = paths

    def build_payload(self) -> dict[str, Any]:
        raise NotImplementedError

    def render_markdown(self, payload: dict[str, Any]) -> str:
        raise NotImplementedError

    def write(self) -> dict[str, Any]:
        payload = self.build_payload()
        ensure_dir(self.paths.json_path.parent)
        ensure_dir(self.paths.markdown_path.parent)
        write_json(self.paths.json_path, payload)
        self.paths.markdown_path.write_text(
            self.render_markdown(payload).rstrip() + "\n",
            encoding="utf-8",
        )
        return payload
