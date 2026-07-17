"""Local taskboard store for current development work."""

from __future__ import annotations

import re
from pathlib import Path

from careereng.platform.persistence import JSONLStore, VersionedDocumentStore
from careereng.utils import ensure_dir, make_id, now_iso

from .taskboard_renderer import render_initial_taskboard, render_no_taskboard, render_taskboard
from .taskboard_schema import (
    EVENT_ARCHIVED,
    EVENT_COMPLETED_ITEM,
    EVENT_CREATED,
    EVENT_UPDATED,
    TASKBOARD_ARCHIVE_DIR,
    TASKBOARD_CURRENT,
    TASKBOARD_EVENTS,
    TASKBOARD_HISTORY_DIR,
)


class TaskboardError(ValueError):
    """Raised when a taskboard operation cannot be completed."""


class TaskboardStore:
    """Keep a compact active taskboard and immutable prior snapshots."""

    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.root = self.workspace / "taskboard"
        self.current_path = self.root / TASKBOARD_CURRENT
        self.events_path = self.root / TASKBOARD_EVENTS
        self.archive_dir = self.root / TASKBOARD_ARCHIVE_DIR
        self.history_dir = self.root / TASKBOARD_HISTORY_DIR
        self._ensure_layout()
        self.documents = VersionedDocumentStore(
            current_path=self.current_path,
            history_dir=self.history_dir,
            events_path=self.events_path,
            artifact_type="taskboard",
        )

    def show(self) -> str:
        text = self.documents.read_current()
        return text.rstrip() if text.strip() else render_no_taskboard()

    def update_from_file(self, input_path: Path | str, *, source_name: str = "") -> dict[str, object]:
        """Create or replace the active board, preserving its prior snapshot."""
        path = Path(input_path)
        if not path.exists():
            raise TaskboardError(f"taskboard update file not found: {path}")
        raw_body = path.read_text(encoding="utf-8").strip()
        body = self._extract_task_body(raw_body)
        if not body:
            raise TaskboardError(f"taskboard update file is empty: {path}")

        now = now_iso()
        normalized_source = str(source_name or path.name).strip()
        created = not self.documents.read_current().strip()
        source_is_current = path.resolve() == self.current_path.resolve()
        if created:
            taskboard_id = make_id("taskboard")
            text = render_initial_taskboard(
                taskboard_id=taskboard_id,
                created_at=now,
                source_name=normalized_source,
                body=body,
            )
            event_type = EVENT_CREATED
            snapshot_existing = False
        else:
            taskboard_id = self._taskboard_id()
            if source_is_current:
                # The caller already edited current.md in place. Record the edit
                # without creating a duplicate self-snapshot.
                text = self._replace_updated_at(raw_body, now)
                snapshot_existing = False
            else:
                text = render_taskboard(
                    taskboard_id=taskboard_id,
                    created_at=self._created_at() or now,
                    updated_at=now,
                    source_name=normalized_source,
                    body=body,
                )
                snapshot_existing = True
            event_type = EVENT_UPDATED

        result = self.documents.replace_current(
            text,
            artifact_id=taskboard_id,
            event_type=event_type,
            summary=f"Updated taskboard from {normalized_source}",
            source=normalized_source,
            details={"source_path": str(path)},
            snapshot_existing=snapshot_existing,
        )
        return {
            "taskboard_id": taskboard_id,
            "created": created,
            "current_path": str(self.current_path),
            "history_path": result.get("snapshot_path") or "",
            "events_path": str(self.events_path),
        }

    def mark_done(self, index: int) -> dict[str, object]:
        if index <= 0:
            raise TaskboardError("task index must be 1-based and greater than 0")
        text = self.documents.read_current()
        if not text.strip():
            raise TaskboardError("no current taskboard found")

        lines = text.splitlines()
        seen = 0
        changed = False
        completed_text = ""
        checkbox_pattern = re.compile(r"^(\s*[-*]\s+\[)([ xX])(\]\s+.*)$")
        for position, line in enumerate(lines):
            match = checkbox_pattern.match(line)
            if not match:
                continue
            seen += 1
            if seen != index:
                continue
            completed_text = line.strip()
            if match.group(2).lower() != "x":
                lines[position] = f"{match.group(1)}x{match.group(3)}"
                changed = True
            break
        if seen < index or not completed_text:
            raise TaskboardError(f"task index not found: {index}")

        taskboard_id = self._taskboard_id()
        if changed:
            self.documents.replace_current(
                self._replace_updated_at("\n".join(lines), now_iso()),
                artifact_id=taskboard_id,
                event_type=EVENT_COMPLETED_ITEM,
                summary=f"Completed taskboard item #{index}",
                details={"index": index, "item": completed_text},
                snapshot_existing=True,
            )
        return {
            "taskboard_id": taskboard_id,
            "index": index,
            "changed": changed,
            "item": completed_text,
            "current_path": str(self.current_path),
        }

    def archive(self) -> dict[str, str]:
        if not self.documents.read_current().strip():
            raise TaskboardError("no current taskboard found")
        taskboard_id = self._taskboard_id()
        result = self.documents.archive_current(
            archive_dir=self.archive_dir,
            artifact_id=taskboard_id,
            event_type=EVENT_ARCHIVED,
            summary=f"Archived taskboard {taskboard_id}",
        )
        return {
            "taskboard_id": taskboard_id,
            "archive_path": str(result.get("archive_path") or ""),
            "events_path": str(self.events_path),
        }

    def _ensure_layout(self) -> None:
        ensure_dir(self.root)
        ensure_dir(self.archive_dir)
        ensure_dir(self.history_dir)
        JSONLStore(self.events_path)

    def _taskboard_id(self) -> str:
        text = self.documents.read_current() if hasattr(self, "documents") else (
            self.current_path.read_text(encoding="utf-8") if self.current_path.exists() else ""
        )
        match = re.search(r"Taskboard ID:\s+`([^`]+)`", text)
        return str(match.group(1) or "") if match else "taskboard_unknown"

    def _created_at(self) -> str:
        text = self.documents.read_current() if hasattr(self, "documents") else (
            self.current_path.read_text(encoding="utf-8") if self.current_path.exists() else ""
        )
        match = re.search(r"^- Created At:\s+`([^`]+)`", text, flags=re.MULTILINE)
        return str(match.group(1) or "") if match else ""

    @staticmethod
    def _replace_updated_at(text: str, updated_at: str) -> str:
        if re.search(r"^- Updated At:\s+`[^`]*`", text, flags=re.MULTILINE):
            return re.sub(r"^- Updated At:\s+`[^`]*`", f"- Updated At: `{updated_at}`", text, count=1, flags=re.MULTILINE)
        return text

    @staticmethod
    def _extract_task_body(text: str) -> str:
        """Accept either a task fragment or a fully rendered taskboard file."""
        match = re.search(r"^## Tasks\s*$", text, flags=re.MULTILINE)
        if match is None:
            return text.strip()
        return text[match.end() :].strip()
