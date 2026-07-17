"""Simple JSONL storage helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from careereng.utils import ensure_dir


class JSONLStore:
    def __init__(self, path: Path):
        self.path = path
        ensure_dir(path.parent)
        if not path.exists():
            path.write_text("", encoding="utf-8")

    def append(self, row: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not self.path.exists():
            return rows
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except Exception:
                continue
            if isinstance(data, dict):
                rows.append(data)
        return rows

    def read_last(self, limit: int) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        rows = self.read_all()
        return rows[-limit:]

    def write_all(self, rows: list[dict[str, Any]]) -> None:
        with self.path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
