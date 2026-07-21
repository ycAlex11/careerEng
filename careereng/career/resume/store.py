"""Current CV storage with upload history."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.utils import ensure_dir, now_iso, read_json, safe_file_stem, write_json


class CVStore:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.cv_dir = ensure_dir(workspace / "cv")
        self.current_dir = ensure_dir(self.cv_dir / "current")
        self.history_dir = ensure_dir(self.cv_dir / "history")
        self.metadata_path = self.current_dir / "metadata.json"

    def _current_files(self) -> list[Path]:
        rows: list[Path] = []
        for path in sorted(self.current_dir.iterdir()):
            if not path.is_file():
                continue
            if path.name == self.metadata_path.name:
                continue
            rows.append(path)
        return rows

    def _normalized_current_name(self, source_name: str) -> str:
        source = Path(str(source_name or "").strip() or "resume.txt")
        stem = safe_file_stem(source.stem or "resume")
        return f"{stem}.txt"

    def ensure_initialized(self) -> None:
        ensure_dir(self.current_dir)
        ensure_dir(self.history_dir)

    def save_upload(self, text: str, source_name: str) -> dict[str, Any]:
        self.ensure_initialized()
        archived: list[str] = []
        stamp = now_iso().replace(":", "-")
        for path in self._current_files():
            history_name = f"{stamp}_{path.name}"
            target = self.history_dir / history_name
            target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            path.unlink()
            archived.append(str(target.relative_to(self.workspace)))

        current_name = self._normalized_current_name(source_name)
        current_path = self.current_dir / current_name
        current_path.write_text(text, encoding="utf-8")
        payload = {
            "source_name": str(source_name or ""),
            "active_file": current_name,
            "updated_at": now_iso(),
            "archived": archived,
        }
        write_json(self.metadata_path, payload)
        return {
            "current_path": str(current_path.relative_to(self.workspace)),
            "metadata_path": str(self.metadata_path.relative_to(self.workspace)),
            "archived": archived,
        }

    def load_current_text(self) -> str:
        self.ensure_initialized()
        metadata = read_json(self.metadata_path)
        active_name = str(metadata.get("active_file") or "")
        candidates: list[Path] = []
        if active_name:
            active_path = self.current_dir / active_name
            if active_path.exists() and active_path.is_file():
                candidates.append(active_path)
        if not candidates:
            candidates = sorted(
                self._current_files(),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        for path in candidates:
            try:
                return path.read_text(encoding="utf-8")
            except Exception:
                try:
                    return path.read_bytes().decode("utf-8", errors="ignore")
                except Exception:
                    continue
        return ""

    def has_current_text(self) -> bool:
        """Return whether a current CV artifact exists without reading its body."""

        self.ensure_initialized()
        metadata = read_json(self.metadata_path)
        active_name = str(metadata.get("active_file") or "")
        if active_name:
            active_path = self.current_dir / active_name
            if active_path.is_file():
                return True
        return bool(self._current_files())
