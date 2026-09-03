"""Persistent, idempotent inbox for site-worker control commands."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import threading

from careereng.utils import ensure_dir, now_iso, read_json, write_json

from .commands import WorkerCommand, WorkerCommandStatus


class WorkerCommandInbox:
    def __init__(self, workspace: Path | str):
        self.root = ensure_dir(Path(workspace) / "sessions" / "worker_commands")
        self.path = self.root / "commands.json"
        self._lock = threading.RLock()

    def enqueue(self, command: WorkerCommand) -> WorkerCommand:
        with self._lock:
            data = self._load_locked()
            existing = self._find(data, command.command_id)
            if existing is not None:
                persisted = WorkerCommand.from_dict(existing)
                if (
                    persisted.site_key != command.site_key
                    or persisted.batch_id != command.batch_id
                    or persisted.work_item_id != command.work_item_id
                    or persisted.kind != command.kind
                    or persisted.message != command.message
                ):
                    raise ValueError(f"worker command id conflicts with an existing command: {command.command_id}")
                return persisted
            next_sequence = max(
                (
                    int(row.get("sequence") or 0)
                    for row in data["commands"]
                    if str(row.get("site_key") or "") == command.site_key
                    and str(row.get("work_item_id") or "") == command.work_item_id
                ),
                default=0,
            ) + 1
            persisted = replace(command, sequence=next_sequence)
            data["commands"].append(persisted.as_dict())
            self._save_locked(data)
            return persisted

    def pending(self, *, site_key: str, work_item_id: str) -> list[WorkerCommand]:
        with self._lock:
            data = self._load_locked()
            rows = [
                WorkerCommand.from_dict(row)
                for row in data["commands"]
                if str(row.get("site_key") or "") == str(site_key or "")
                and str(row.get("work_item_id") or "") == str(work_item_id or "")
                and str(row.get("status") or "") == WorkerCommandStatus.PENDING.value
            ]
            return sorted(rows, key=lambda row: row.sequence)

    def transition(
        self,
        command_id: str,
        *,
        status: WorkerCommandStatus | str,
        error: str = "",
    ) -> WorkerCommand:
        normalized = status if isinstance(status, WorkerCommandStatus) else WorkerCommandStatus(str(status))
        with self._lock:
            data = self._load_locked()
            row = self._find(data, command_id)
            if row is None:
                raise KeyError(f"worker command not found: {command_id}")
            current = WorkerCommandStatus(str(row.get("status") or WorkerCommandStatus.PENDING.value))
            if current == normalized:
                return WorkerCommand.from_dict(row)
            allowed = {
                WorkerCommandStatus.PENDING: {
                    WorkerCommandStatus.CLAIMED,
                    WorkerCommandStatus.SUPERSEDED,
                    WorkerCommandStatus.FAILED,
                },
                WorkerCommandStatus.CLAIMED: {
                    WorkerCommandStatus.APPLIED,
                    WorkerCommandStatus.FAILED,
                },
            }
            if normalized not in allowed.get(current, set()):
                raise ValueError(f"invalid worker command transition: {current.value} -> {normalized.value}")
            row["status"] = normalized.value
            row["updated_at"] = now_iso()
            row["error"] = str(error or "")
            self._save_locked(data)
            return WorkerCommand.from_dict(row)

    def latest_pending(self, *, site_key: str, work_item_id: str) -> WorkerCommand | None:
        rows = self.pending(site_key=site_key, work_item_id=work_item_id)
        return rows[-1] if rows else None

    def _load_locked(self) -> dict[str, list[dict]]:
        data = read_json(self.path)
        commands = data.get("commands") if isinstance(data.get("commands"), list) else []
        return {"commands": [row for row in commands if isinstance(row, dict)]}

    def _save_locked(self, data: dict[str, list[dict]]) -> None:
        write_json(self.path, data)

    @staticmethod
    def _find(data: dict[str, list[dict]], command_id: str) -> dict | None:
        requested = str(command_id or "")
        return next(
            (row for row in data["commands"] if str(row.get("command_id") or "") == requested),
            None,
        )
