"""Generic current-document lifecycle for workspace persistence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .jsonl import JSONLStore
from careereng.utils import ensure_dir, make_id, now_iso, safe_file_stem


class VersionedDocumentStore:
    """Persist one mutable document with history snapshots and events."""

    def __init__(
        self,
        *,
        current_path: Path | str,
        history_dir: Path | str,
        events_path: Path | str | None,
        artifact_type: str,
    ):
        self.current_path = Path(current_path)
        self.history_dir = ensure_dir(Path(history_dir))
        self.events_store = JSONLStore(Path(events_path)) if events_path else None
        self.artifact_type = str(artifact_type or "document").strip() or "document"
        ensure_dir(self.current_path.parent)

    def read_current(self) -> str:
        if not self.current_path.exists():
            return ""
        return self.current_path.read_text(encoding="utf-8")

    def replace_current(
        self,
        text: str,
        *,
        artifact_id: str,
        event_type: str,
        summary: str,
        reason: str = "",
        source: str = "",
        details: dict[str, Any] | None = None,
        snapshot_existing: bool = True,
    ) -> dict[str, Any]:
        snapshot_path = (
            self.snapshot_current(reason=reason or event_type, source=source)
            if snapshot_existing
            else None
        )
        self.current_path.write_text(str(text).rstrip() + "\n", encoding="utf-8")
        event = self.append_event(
            artifact_id=artifact_id,
            event_type=event_type,
            summary=summary,
            reason=reason,
            source=source,
            details=details,
            snapshot_path=snapshot_path,
        )
        return {
            "current_path": str(self.current_path),
            "snapshot_path": str(snapshot_path) if snapshot_path else "",
            "event": event,
        }

    def snapshot_current(self, *, reason: str = "", source: str = "") -> Path | None:
        del reason, source  # Stable API for future metadata-aware backends.
        text = self.read_current()
        if not text.strip():
            return None
        target = self._unique_path(
            directory=self.history_dir,
            stem=f"{safe_file_stem(self.current_path.stem)}_{safe_file_stem(now_iso().replace(':', '-'))}",
            suffix=self.current_path.suffix or ".md",
        )
        target.write_text(text, encoding="utf-8")
        return target

    def archive_current(
        self,
        *,
        archive_dir: Path | str,
        artifact_id: str,
        event_type: str,
        summary: str,
        reason: str = "",
        source: str = "",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.read_current().strip():
            raise ValueError("no current document found")
        target = self._unique_path(
            directory=ensure_dir(Path(archive_dir)),
            stem=f"{now_iso()[:10]}-{safe_file_stem(artifact_id)}",
            suffix=self.current_path.suffix or ".md",
        )
        self.current_path.replace(target)
        event = self.append_event(
            artifact_id=artifact_id,
            event_type=event_type,
            summary=summary,
            reason=reason,
            source=source,
            details={**dict(details or {}), "archive_path": str(target)},
        )
        return {"archive_path": str(target), "event": event}

    def append_event(
        self,
        *,
        artifact_id: str,
        event_type: str,
        summary: str,
        reason: str = "",
        source: str = "",
        details: dict[str, Any] | None = None,
        snapshot_path: Path | None = None,
    ) -> dict[str, Any]:
        row = {
            "event_id": make_id(f"{safe_file_stem(self.artifact_type)}_event"),
            "created_at": now_iso(),
            "artifact_type": self.artifact_type,
            "artifact_id": str(artifact_id or "").strip(),
            "event_type": str(event_type or "document.updated").strip(),
            "summary": str(summary or "").strip(),
            "reason": str(reason or "").strip(),
            "source": str(source or "").strip(),
            "current_path": str(self.current_path),
            "snapshot_path": str(snapshot_path) if snapshot_path else "",
            "details": dict(details or {}),
        }
        if self.events_store is not None:
            self.events_store.append(row)
        return row

    @staticmethod
    def _unique_path(*, directory: Path, stem: str, suffix: str) -> Path:
        target = directory / f"{stem}{suffix}"
        counter = 2
        while target.exists():
            target = directory / f"{stem}-{counter}{suffix}"
            counter += 1
        return target
