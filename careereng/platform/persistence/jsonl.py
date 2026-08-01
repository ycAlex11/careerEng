"""Simple JSONL storage helpers."""

from __future__ import annotations

import json
from collections.abc import Iterator
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
        return list(self.iter_rows())

    def iter_rows(self) -> Iterator[dict[str, Any]]:
        """Yield valid JSON object rows in file order without materializing the file."""

        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                row = self._parse_line(line)
                if row is not None:
                    yield row

    def iter_rows_reverse(self, *, chunk_size: int = 64 * 1024) -> Iterator[dict[str, Any]]:
        """Yield valid JSON object rows from newest to oldest using bounded buffers."""

        if not self.path.exists():
            return
        with self.path.open("rb") as fh:
            fh.seek(0, 2)
            position = fh.tell()
            remainder = b""
            while position > 0:
                read_size = min(max(1, int(chunk_size)), position)
                position -= read_size
                fh.seek(position)
                chunk = fh.read(read_size)
                parts = (chunk + remainder).split(b"\n")
                remainder = parts[0]
                for raw_line in reversed(parts[1:]):
                    row = self._parse_line(raw_line.decode("utf-8", errors="replace"))
                    if row is not None:
                        yield row
            row = self._parse_line(remainder.decode("utf-8", errors="replace"))
            if row is not None:
                yield row

    def read_last(self, limit: int) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        rows: list[dict[str, Any]] = []
        for row in self.iter_rows_reverse():
            rows.append(row)
            if len(rows) >= limit:
                break
        rows.reverse()
        return rows

    def write_all(self, rows: list[dict[str, Any]]) -> None:
        with self.path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    @staticmethod
    def _parse_line(line: str) -> dict[str, Any] | None:
        normalized = str(line or "").strip()
        if not normalized:
            return None
        try:
            data = json.loads(normalized)
        except (TypeError, ValueError):
            return None
        return data if isinstance(data, dict) else None
