"""Local taskboard store for current development work."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from careereng.storage.jsonl import JSONLStore
from careereng.taskboard.renderer import render_initial_taskboard, render_no_taskboard, render_update_section
from careereng.taskboard.schema import (
    EVENT_ARCHIVED,
    EVENT_COMPLETED_ITEM,
    EVENT_CREATED,
    EVENT_UPDATED,
    TASKBOARD_ARCHIVE_DIR,
    TASKBOARD_CURRENT,
    TASKBOARD_EVENTS,
)
from careereng.utils import ensure_dir, make_id, now_iso, safe_file_stem


class TaskboardError(ValueError):
    """Raised when a taskboard operation cannot be completed."""


class TaskboardStore:
    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.root = self.workspace / "taskboard"
        self.current_path = self.root / TASKBOARD_CURRENT
        self.events_path = self.root / TASKBOARD_EVENTS
        self.archive_dir = self.root / TASKBOARD_ARCHIVE_DIR
        self._ensure_layout()

    def show(self) -> str:
        if not self.current_path.exists() or not self.current_path.read_text(encoding="utf-8").strip():
            return render_no_taskboard()
        return self.current_path.read_text(encoding="utf-8").rstrip()

    def update_from_file(self, input_path: Path | str, *, source_name: str = "") -> dict[str, Any]:
        path = Path(input_path)
        if not path.exists():
            raise TaskboardError(f"taskboard update file not found: {path}")
        body = path.read_text(encoding="utf-8").strip()
        if not body:
            raise TaskboardError(f"taskboard update file is empty: {path}")
        normalized_source = str(source_name or path.name).strip()
        now = now_iso()
        created = not self.current_path.exists() or not self.current_path.read_text(encoding="utf-8").strip()
        if created:
            taskboard_id = make_id("taskboard")
            text = render_initial_taskboard(
                taskboard_id=taskboard_id,
                created_at=now,
                source_name=normalized_source,
                body=body,
            )
            event_type = EVENT_CREATED
        else:
            taskboard_id = self._taskboard_id()
            existing = self.current_path.read_text(encoding="utf-8").rstrip()
            existing = self._replace_updated_at(existing, now)
            text = existing + render_update_section(updated_at=now, source_name=normalized_source, body=body)
            event_type = EVENT_UPDATED
        self.current_path.write_text(text.rstrip() + "\n", encoding="utf-8")
        self._append_event(
            event_type=event_type,
            taskboard_id=taskboard_id,
            summary=f"Updated taskboard from {normalized_source}",
            details={"source": str(path)},
        )
        return {
            "taskboard_id": taskboard_id,
            "created": created,
            "current_path": str(self.current_path),
            "events_path": str(self.events_path),
        }

    def mark_done(self, index: int) -> dict[str, Any]:
        if index <= 0:
            raise TaskboardError("task index must be 1-based and greater than 0")
        if not self.current_path.exists():
            raise TaskboardError("no current taskboard found")
        lines = self.current_path.read_text(encoding="utf-8").splitlines()
        seen = 0
        changed = False
        completed_text = ""
        checkbox_pattern = re.compile(r"^(\s*[-*]\s+\[)([ xX])(\]\s+.*)$")
        for pos, line in enumerate(lines):
            match = checkbox_pattern.match(line)
            if not match:
                continue
            seen += 1
            if seen != index:
                continue
            completed_text = line.strip()
            if match.group(2).lower() == "x":
                changed = False
            else:
                lines[pos] = f"{match.group(1)}x{match.group(3)}"
                changed = True
            break
        if seen < index or not completed_text:
            raise TaskboardError(f"task index not found: {index}")
        if changed:
            now = now_iso()
            text = self._replace_updated_at("\n".join(lines), now)
            self.current_path.write_text(text.rstrip() + "\n", encoding="utf-8")
            self._append_event(
                event_type=EVENT_COMPLETED_ITEM,
                taskboard_id=self._taskboard_id(),
                summary=f"Completed taskboard item #{index}",
                details={"index": index, "item": completed_text},
            )
        return {
            "taskboard_id": self._taskboard_id(),
            "index": index,
            "changed": changed,
            "item": completed_text,
            "current_path": str(self.current_path),
        }

    def archive(self) -> dict[str, Any]:
        if not self.current_path.exists() or not self.current_path.read_text(encoding="utf-8").strip():
            raise TaskboardError("no current taskboard found")
        taskboard_id = self._taskboard_id()
        now = now_iso()
        archive_name = f"{now[:10]}-{safe_file_stem(taskboard_id)}.md"
        target = self.archive_dir / archive_name
        counter = 2
        while target.exists():
            target = self.archive_dir / f"{now[:10]}-{safe_file_stem(taskboard_id)}-{counter}.md"
            counter += 1
        self.current_path.replace(target)
        self._append_event(
            event_type=EVENT_ARCHIVED,
            taskboard_id=taskboard_id,
            summary=f"Archived taskboard {taskboard_id}",
            details={"archive_path": str(target)},
        )
        return {
            "taskboard_id": taskboard_id,
            "archive_path": str(target),
            "events_path": str(self.events_path),
        }

    def _ensure_layout(self) -> None:
        ensure_dir(self.root)
        ensure_dir(self.archive_dir)
        JSONLStore(self.events_path)

    def _append_event(self, *, event_type: str, taskboard_id: str, summary: str, details: dict[str, Any] | None = None) -> None:
        JSONLStore(self.events_path).append(
            {
                "event_id": make_id("taskboard_event"),
                "event_type": event_type,
                "taskboard_id": taskboard_id,
                "created_at": now_iso(),
                "summary": summary,
                "details": dict(details or {}),
            }
        )

    def _taskboard_id(self) -> str:
        if not self.current_path.exists():
            return "taskboard_unknown"
        text = self.current_path.read_text(encoding="utf-8")
        match = re.search(r"Taskboard ID:\s+`([^`]+)`", text)
        if match:
            return match.group(1)
        return "taskboard_unknown"

    @staticmethod
    def _replace_updated_at(text: str, updated_at: str) -> str:
        if re.search(r"^- Updated At:\s+`[^`]*`", text, flags=re.MULTILINE):
            return re.sub(r"^- Updated At:\s+`[^`]*`", f"- Updated At: `{updated_at}`", text, count=1, flags=re.MULTILINE)
        return text
