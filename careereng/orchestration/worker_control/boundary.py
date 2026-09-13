"""Cooperative delivery of guidance at scoped operation boundaries."""

from pathlib import Path

from careereng.utils import now_iso, read_json, write_json
from .commands import WorkerCommandKind, WorkerCommandStatus
from .inbox import WorkerCommandInbox
from .registry import NativeWorkerRegistry
from .actions import WorkerActionStore, WorkerActionStatus


class WorkerCommandBoundary:
    def __init__(self, workspace: Path | str):
        self.registry = NativeWorkerRegistry(workspace)
        self.inbox = WorkerCommandInbox(workspace)
        self.actions = WorkerActionStore(workspace)
        self.path = Path(workspace) / "sessions" / "worker_commands" / "receipts.json"

    def receipt(self, command_id: str) -> dict:
        with self.inbox._lock:
            return (read_json(self.path) or {}).get(command_id, {})

    def _settle(self, command, receipt: dict) -> None:
        if receipt.get("status") in {"applied", "failed"} and command.status == WorkerCommandStatus.CLAIMED:
            self.inbox.transition(command.command_id, status=receipt["status"],
                                  error=receipt["summary"] if receipt["status"] == "failed" else "")
        for action in self.actions.for_command(command.command_id):
            if action.status == WorkerActionStatus.PENDING:
                self.actions.transition(action.action_id, status=WorkerActionStatus.SUPERSEDED,
                                        error="guidance already received at operation boundary")

    def _worker(self, work_item_id: str, control_epoch: int) -> dict:
        worker = self.registry.get(work_item_id=work_item_id)
        if not worker or int(worker.get("control_epoch") or 0) != control_epoch:
            raise ValueError("obsolete control epoch")
        if worker.get("desired_state") != "running" or worker.get("work_state") not in {"queued", "running"}:
            raise ValueError("worker is not executing")
        return worker

    def pending(self, work_item_id: str, control_epoch: int) -> dict:
        with self.registry._lock, self.inbox._lock:
            worker = self._worker(work_item_id, control_epoch)
            commands = self.inbox.list(site_key=worker["site_key"], work_item_id=work_item_id,
                                       statuses={WorkerCommandStatus.PENDING, WorkerCommandStatus.CLAIMED})
            for command in commands:
                if command.expected_control_epoch != control_epoch:
                    continue
                if command.kind != WorkerCommandKind.GUIDANCE:
                    return {}
                receipt = self.receipt(command.command_id)
                if receipt:
                    self._settle(command, receipt)
                    if receipt.get("status") in {"applied", "failed"}:
                        continue
                return {"command_id": command.command_id, "sequence": command.sequence,
                        "message": command.message, "control_epoch": control_epoch,
                        "work_item_id": work_item_id,
                        "instruction": "Read this guidance, acknowledge received then applied or failed with careereng_ack_worker_guidance. Do not retry the blocked operation unchanged."}
            return {}

    def acknowledge(self, work_item_id: str, control_epoch: int, command_id: str,
                    status: str, summary: str) -> dict:
        if status not in {"received", "applied", "failed"} or not summary.strip():
            raise ValueError("a receipt status and non-empty summary are required")
        with self.registry._lock, self.inbox._lock:
            worker = self._worker(work_item_id, control_epoch)
            command = self.inbox.get(command_id)
            if (command.work_item_id != work_item_id or command.batch_id != worker["batch_id"]
                    or command.expected_control_epoch != control_epoch or command.kind != WorkerCommandKind.GUIDANCE):
                raise ValueError("guidance receipt scope mismatch")
            receipts = read_json(self.path) or {}
            previous = receipts.get(command_id, {})
            if command.status != WorkerCommandStatus.SUPERSEDED and previous and (
                previous.get("status") == status or status == "received"
            ):
                self._settle(command, previous)
                return previous
            if command.status in {WorkerCommandStatus.APPLIED, WorkerCommandStatus.FAILED, WorkerCommandStatus.SUPERSEDED}:
                raise ValueError("guidance is already terminal")
            pending = self.pending(work_item_id, control_epoch)
            if pending.get("command_id") != command_id:
                raise ValueError("guidance receipts must follow command order")
            if status != "received" and previous.get("status") != "received":
                raise ValueError("acknowledge received before applying guidance")
            if command.status == WorkerCommandStatus.PENDING:
                self.inbox.transition(command_id, status=WorkerCommandStatus.CLAIMED)
            receipt = {"command_id": command_id, "work_item_id": work_item_id,
                       "control_epoch": control_epoch, "status": status, "summary": summary.strip(),
                       "received_at": previous.get("received_at") or now_iso(), "updated_at": now_iso()}
            receipts[command_id] = receipt
            write_json(self.path, receipts)
            self._settle(self.inbox.get(command_id), receipt)
            return receipt
