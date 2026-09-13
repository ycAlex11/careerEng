"""Translate durable worker intent into acknowledged native actions."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime

from .actions import WorkerAction, WorkerActionKind, WorkerActionStatus, WorkerActionStore, create_worker_action
from .arbiter import WorkerCommandAction, WorkerCommandArbiter
from .commands import WorkerCommand, WorkerCommandKind, WorkerCommandStatus, create_worker_command
from .inbox import WorkerCommandInbox
from .registry import NativeWorkerRegistry
from .lifecycle import is_terminal_work
from .liveness import has_recent_activity, obsolete_recovery
from .scheduling import execution_admitted
from careereng.platform.persistence.mutex import workspace_mutex
from careereng.utils import now_iso


class NativeWorkerControlSupervisor:
    def __init__(self, workspace: Path | str):
        self.registry = NativeWorkerRegistry(workspace)
        self.inbox = WorkerCommandInbox(workspace)
        self.actions = WorkerActionStore(workspace)
        self.arbiter = WorkerCommandArbiter()

    def enqueue(self, command: WorkerCommand) -> tuple[WorkerCommand, list[WorkerAction]]:
        persisted = self.inbox.enqueue(command)
        worker = self.registry.get(work_item_id=persisted.work_item_id)
        if worker:
            self._retire_obsolete_commands(worker)
            persisted = self.inbox.get(persisted.command_id)
        if persisted.status != WorkerCommandStatus.PENDING:
            return persisted, self.actions.for_command(persisted.command_id)
        earlier = self.inbox.list(
            site_key=persisted.site_key,
            work_item_id=persisted.work_item_id,
            statuses={WorkerCommandStatus.PENDING, WorkerCommandStatus.CLAIMED},
        )
        if any(
            row.command_id != persisted.command_id
            and row.sequence < persisted.sequence
            and row.status == WorkerCommandStatus.CLAIMED
            for row in earlier
        ):
            return persisted, []
        worker = self.registry.get(work_item_id=persisted.work_item_id)
        if not worker:
            return persisted, []
        self._materialize(persisted, worker)
        return persisted, self._executable_for_command(persisted.command_id)

    def reconcile(self, work_item_id: str) -> list[WorkerAction]:
        worker = self.registry.get(work_item_id=work_item_id)
        if not worker:
            return []
        self._retire_obsolete_commands(worker)
        materialized: list[WorkerAction] = []
        for command in self.inbox.pending(
            site_key=str(worker.get("site_key") or ""),
            work_item_id=str(worker.get("work_item_id") or ""),
        ):
            self._materialize(command, worker)
            actions = self._executable_for_command(command.command_id)
            materialized.extend(actions)
            if self.actions.for_command(command.command_id):
                break
        return materialized

    def _executable_for_command(self, command_id: str) -> list[WorkerAction]:
        allowed = {row.action_id for row in self.actions.pending()}
        return [row for row in self.actions.for_command(command_id) if row.action_id in allowed]

    def _retire_obsolete_commands(self, worker: dict) -> None:
        commands = self.inbox.list(work_item_id=str(worker["work_item_id"]), site_key=str(worker.get("site_key") or ""),
                                   statuses={WorkerCommandStatus.PENDING, WorkerCommandStatus.CLAIMED})
        for command in commands:
            stale = command.expected_control_epoch != int(worker.get("control_epoch") or 0)
            if command.kind == WorkerCommandKind.RECOVERY:
                stale = stale or any(obsolete_recovery(worker, action.payload)
                                     for action in self.actions.for_command(command.command_id))
            terminal_action = is_terminal_work(str(worker.get("work_state") or "")) and command.kind not in {
                WorkerCommandKind.PAUSE, WorkerCommandKind.CANCEL,
            }
            if stale or terminal_action:
                self.inbox.transition(command.command_id, status=WorkerCommandStatus.SUPERSEDED,
                                      error="obsolete control epoch or terminal work item")

    def acknowledge(self, action_id: str, *, applied: bool, error: str = "") -> tuple[WorkerAction, WorkerCommand | None]:
        self.actions.pending()
        existing = self.actions.get(action_id)
        if existing.status == WorkerActionStatus.SUPERSEDED:
            command_id = str(existing.payload.get("command_id") or "")
            return existing, self.inbox.get(command_id) if command_id else None
        action = self.actions.acknowledge(action_id, applied=applied, error=error)
        if applied and action.kind == WorkerActionKind.INTERRUPT:
            worker = self.registry.get(work_item_id=action.work_item_id)
            if worker and action.control_epoch == int(worker.get("control_epoch") or 0) and worker.get("runtime_state") in {"starting", "running", "quiescing"}:
                self.registry.update(
                    action.work_item_id,
                    control_state="transitioning",
                    interrupt_attempts=int(worker.get("interrupt_attempts") or 0) + 1,
                    interrupt_ack_started_at=now_iso(),
                    interrupt_is_recovery=action.payload.get("command_kind") == "recovery",
                    interrupt_activity_revision=action.payload.get("expected_activity_revision"),
                )
        command_id = str(action.payload.get("command_id") or "")
        if not command_id:
            return action, None
        command = self.inbox.get(command_id)
        if not applied:
            self.actions.supersede_dependents(action.action_id, error=error)
            if command.status == WorkerCommandStatus.CLAIMED:
                command = self.inbox.transition(command_id, status=WorkerCommandStatus.FAILED, error=error)
            return action, command
        command_actions = self.actions.for_command(command_id)
        if command_actions and all(row.status == WorkerActionStatus.APPLIED for row in command_actions):
            if command.status == WorkerCommandStatus.CLAIMED:
                command = self.inbox.transition(command_id, status=WorkerCommandStatus.APPLIED)
        return action, command

    def reconcile_liveness(
        self,
        *,
        idle_timeout_seconds: int,
        max_resume_attempts: int,
        interrupt_ack_timeout_seconds: int,
        max_interrupt_attempts: int,
        observed_at: str | None = None,
        probe_interval_seconds: int = 30,
        failure_threshold: int = 3,
        inflight_timeout_seconds: int = 300,
    ) -> list[dict]:
        """Turn stale generic lifecycle facts into bounded recovery evidence."""

        with workspace_mutex(self.registry.path):
            return self._reconcile_liveness(
                idle_timeout_seconds=idle_timeout_seconds, max_resume_attempts=max_resume_attempts,
                interrupt_ack_timeout_seconds=interrupt_ack_timeout_seconds,
                max_interrupt_attempts=max_interrupt_attempts, observed_at=observed_at,
                probe_interval_seconds=probe_interval_seconds, failure_threshold=failure_threshold,
                inflight_timeout_seconds=inflight_timeout_seconds,
            )

    def _reconcile_liveness(self, *, idle_timeout_seconds: int, max_resume_attempts: int,
                            interrupt_ack_timeout_seconds: int, max_interrupt_attempts: int,
                            observed_at: str | None, probe_interval_seconds: int,
                            failure_threshold: int, inflight_timeout_seconds: int) -> list[dict]:

        now = datetime.fromisoformat(observed_at) if observed_at else datetime.fromisoformat(now_iso())
        results: list[dict] = []
        for worker in self.registry.list(active_only=True):
            if worker.get("desired_state") != "running" or worker.get("work_state") != "running":
                if str(worker.get("control_state") or "") != "transitioning":
                    continue
            interrupt_started = str(worker.get("interrupt_ack_started_at") or "")
            if str(worker.get("control_state") or "") == "transitioning" and interrupt_started:
                age = (now - datetime.fromisoformat(interrupt_started)).total_seconds()
                if age < max(1, int(interrupt_ack_timeout_seconds or 1)):
                    continue
                attempts = int(worker.get("interrupt_attempts") or 0)
                if attempts >= max(1, int(max_interrupt_attempts or 1)):
                    updated = self.registry.update(
                        str(worker.get("work_item_id") or ""),
                        control_state="pause_unconfirmed",
                        last_error="interrupt acknowledgement timed out",
                    )
                    results.append({"kind": "interrupt_unconfirmed", "worker": updated, "actions": []})
                    continue
                retry = self.actions.enqueue(
                    create_worker_action(
                        kind=WorkerActionKind.INTERRUPT,
                        agent_id=str(worker.get("agent_id") or ""),
                        work_item_id=str(worker.get("work_item_id") or ""),
                        site_key=str(worker.get("site_key") or ""),
                        batch_id=str(worker.get("batch_id") or ""),
                        control_epoch=int(worker.get("control_epoch") or 0),
                        payload={
                            **({"expected_activity_revision": int(worker.get("interrupt_activity_revision") or 0),
                                "command_kind": "recovery"} if worker.get("interrupt_is_recovery") else {}),
                            "reason": "interrupt_ack_timeout",
                            "attempt": attempts + 1,
                            "worker_revision": int(worker.get("revision") or 0),
                            "resource_policy": str(worker.get("browser_policy") or "unchanged"),
                        },
                    )
                )
                results.append({"kind": "interrupt_retry", "worker": worker, "actions": [retry]})
                continue
            if str(worker.get("runtime_state") or "") != "running" or str(worker.get("work_state") or "") != "running":
                continue
            if has_recent_activity(worker, now, max(1, idle_timeout_seconds), max(1, inflight_timeout_seconds)):
                if worker.get("suspect_checks"):
                    self.registry.update(str(worker["work_item_id"]), suspect_checks=0, last_probe_at="")
                continue
            previous_probe = str(worker.get("last_probe_at") or "")
            if previous_probe and (now - datetime.fromisoformat(previous_probe)).total_seconds() < max(1, probe_interval_seconds):
                continue
            pending = self.actions.unsettled(str(worker["work_item_id"]))
            if any(row.work_item_id == worker["work_item_id"] and row.payload.get("liveness_probe") for row in pending):
                continue
            if any(row.work_item_id == worker["work_item_id"] and row.payload.get("command_kind") == "recovery" for row in pending):
                continue
            checks = int(worker.get("suspect_checks") or 0) + 1
            worker = self.registry.update(str(worker["work_item_id"]), suspect_checks=checks,
                                          last_probe_at=now.isoformat(timespec="seconds"))
            if checks < max(2, failure_threshold):
                probe = self.actions.enqueue(create_worker_action(
                    kind=WorkerActionKind.PROBE, agent_id=str(worker.get("agent_id") or ""),
                    work_item_id=str(worker["work_item_id"]), site_key=str(worker.get("site_key") or ""),
                    batch_id=str(worker.get("batch_id") or ""), control_epoch=int(worker.get("control_epoch") or 0),
                    payload={"liveness_probe": True, "check": checks,
                             "expected_activity_revision": int(worker.get("activity_revision") or 0),
                             "reason": "Read-only inspection: seek fresh execution evidence; an active label alone is not a heartbeat."},
                ))
                results.append({"kind": "suspected_unresponsive", "worker": worker, "actions": [probe]})
                continue
            attempts = int(worker.get("recovery_attempts") or 0)
            if attempts >= max(0, int(max_resume_attempts or 0)):
                updated = self.registry.update(
                    str(worker.get("work_item_id") or ""),
                    work_state="waiting_user",
                    control_state="waiting_user",
                    last_error="worker heartbeat recovery exhausted",
                )
                results.append({"kind": "recovery_exhausted", "worker": updated, "actions": []})
                continue
            updated = self.registry.update(
                str(worker.get("work_item_id") or ""),
                recovery_attempts=attempts + 1,
            )
            command = create_worker_command(
                command_id=f"worker_recovery:{updated['work_item_id']}:{attempts + 1}",
                site_key=str(updated.get("site_key") or ""),
                batch_id=str(updated.get("batch_id") or ""),
                work_item_id=str(updated.get("work_item_id") or ""),
                kind=WorkerCommandKind.RECOVERY,
                message="Continue the current durable work item from its latest CareerEng checkpoint.",
                expected_control_epoch=int(updated.get("control_epoch") or 0),
            )
            _, actions = self.enqueue(command)
            results.append({"kind": "recovery_requested", "worker": updated, "actions": actions})
        return results

    def prepare_action(self, action_id: str) -> WorkerAction:
        with workspace_mutex(self.registry.path):
            pending = {row.action_id: row for row in self.actions.pending()}
            existing = self.actions.get(action_id)
            worker = self.registry.get(work_item_id=existing.work_item_id)
            if existing.kind in {WorkerActionKind.SPAWN, WorkerActionKind.SEND, WorkerActionKind.RESUME} and not execution_admitted(worker):
                raise ValueError("worker is waiting or queued for execution capacity")
            if existing.status == WorkerActionStatus.CLAIMED:
                return existing
            if action_id not in pending:
                raise ValueError("action is obsolete, already claimed, or waiting for its prerequisite")
            return self.actions.transition(action_id, status=WorkerActionStatus.CLAIMED)

    def _materialize(self, command: WorkerCommand, worker: dict) -> list[WorkerAction]:
        obsolete = command.expected_control_epoch != int(worker.get("control_epoch") or 0)
        terminal_action = is_terminal_work(str(worker.get("work_state") or "")) and command.kind not in {
            WorkerCommandKind.PAUSE, WorkerCommandKind.CANCEL,
        }
        if obsolete or terminal_action:
            self.inbox.transition(command.command_id, status=WorkerCommandStatus.SUPERSEDED,
                                  error="obsolete control epoch or terminal work item")
            return []
        existing = self.actions.for_command(command.command_id)
        if existing:
            return existing
        if (
            str(worker.get("desired_state") or "running") == "paused"
            and command.kind not in {WorkerCommandKind.RESUME, WorkerCommandKind.PAUSE, WorkerCommandKind.CANCEL}
        ):
            return []
        runtime_state = str(worker.get("runtime_state") or "detached")
        work_state = str(worker.get("work_state") or "queued")
        decision = self.arbiter.decide(
            command,
            worker_status=runtime_state,
            has_turn=runtime_state in {"starting", "running", "quiescing"} and work_state == "running",
            turn_start_inflight=runtime_state == "starting",
            recovery_pending=any(
                row.command_id != command.command_id
                and row.kind == WorkerCommandKind.RECOVERY
                and row.status == WorkerCommandStatus.CLAIMED
                for row in self.inbox.list(
                    site_key=command.site_key,
                    work_item_id=command.work_item_id,
                    statuses={WorkerCommandStatus.CLAIMED},
                )
            ),
        )
        if decision.action == WorkerCommandAction.QUEUE:
            return []
        self.inbox.transition(command.command_id, status=WorkerCommandStatus.CLAIMED)
        common = {
            "agent_id": str(worker.get("agent_id") or ""),
            "work_item_id": command.work_item_id,
            "site_key": command.site_key,
            "batch_id": command.batch_id,
            "control_epoch": int(worker.get("control_epoch") or 0),
        }
        base_payload = {
            "command_id": command.command_id,
            "command_sequence": command.sequence,
            "command_kind": command.kind.value,
            "reason": decision.reason,
            "worker_revision": int(worker.get("revision") or 0),
            "resource_policy": str(worker.get("browser_policy") or "unchanged"),
        }
        if command.kind == WorkerCommandKind.RECOVERY:
            base_payload["expected_activity_revision"] = int(worker.get("activity_revision") or 0)
        planned: list[WorkerAction] = []
        if decision.action == WorkerCommandAction.TERMINATE:
            planned.append(create_worker_action(kind=WorkerActionKind.CLOSE, payload=base_payload, **common))
        elif decision.action == WorkerCommandAction.INTERRUPT:
            interrupt = create_worker_action(kind=WorkerActionKind.INTERRUPT, payload=base_payload, **common)
            planned.append(interrupt)
            if command.kind in {WorkerCommandKind.REDIRECT, WorkerCommandKind.RECOVERY}:
                planned.append(
                    create_worker_action(
                        kind=WorkerActionKind.SEND,
                        payload={
                            **base_payload,
                            "message": command.message,
                            "depends_on_action_id": interrupt.action_id,
                        },
                        **common,
                    )
                )
        elif not common["agent_id"]:
            planned.append(
                create_worker_action(
                    kind=WorkerActionKind.SPAWN,
                    payload={**base_payload, "prompt": command.message},
                    **common,
                )
            )
        elif command.kind == WorkerCommandKind.RESUME or runtime_state == "suspended":
            resume = create_worker_action(kind=WorkerActionKind.RESUME, payload=base_payload, **common)
            planned.append(resume)
            if command.message:
                planned.append(
                    create_worker_action(
                        kind=WorkerActionKind.SEND,
                        payload={
                            **base_payload,
                            "message": command.message,
                            "depends_on_action_id": resume.action_id,
                        },
                        **common,
                    )
                )
        else:
            planned.append(
                create_worker_action(
                    kind=WorkerActionKind.SEND,
                    payload={**base_payload, "message": command.message},
                    **common,
                )
            )
        return [self.actions.enqueue(action) for action in planned]
