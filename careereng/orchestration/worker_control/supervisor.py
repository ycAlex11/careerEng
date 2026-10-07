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
from .reconciler import WorkerLifecycleReconciler
from careereng.platform.persistence.mutex import workspace_mutex
from careereng.utils import now_iso


class NativeWorkerControlSupervisor:
    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.registry = NativeWorkerRegistry(workspace)
        self.inbox = WorkerCommandInbox(workspace)
        self.actions = WorkerActionStore(workspace)
        self.arbiter = WorkerCommandArbiter()

    def enqueue(self, command: WorkerCommand) -> tuple[WorkerCommand, list[WorkerAction]]:
        with self.registry._lock, self.inbox._lock:
            return self._enqueue(command)

    def _enqueue(self, command: WorkerCommand) -> tuple[WorkerCommand, list[WorkerAction]]:
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
        with self.registry._lock, self.inbox._lock:
            return self._reconcile(work_item_id)

    def _reconcile(self, work_item_id: str) -> list[WorkerAction]:
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

    def acknowledge(self, action_id: str, *, applied: bool, error: str = "", observation: dict | None = None) -> tuple[WorkerAction, WorkerCommand | None]:
        with self.registry._lock, self.inbox._lock:
            if observation:
                self._record_observation(action_id, observation)
            return self._acknowledge(action_id, applied=applied, error=error)

    def _record_observation(self, action_id: str, observation: dict) -> None:
        action = self.actions.get(action_id)
        if action.status not in {WorkerActionStatus.CLAIMED, WorkerActionStatus.APPLIED}:
            raise ValueError("prepare the action before recording an observation")
        if action.kind not in {WorkerActionKind.PROBE, WorkerActionKind.CLOSE, WorkerActionKind.INTERRUPT}:
            raise ValueError("only probe and shutdown actions accept runtime observations")
        worker = self.registry.require_binding(action.work_item_id, batch_id=action.batch_id,
                                              agent_id=action.agent_id, control_epoch=action.control_epoch)
        if self.registry.get(agent_id=action.agent_id).get("work_item_id") != action.work_item_id:
            raise ValueError("observation refers to a replaced task binding")
        if action.status == WorkerActionStatus.APPLIED and worker.get("observed_runtime_evidence") == observation:
            return
        if observation.get("agent_id") != action.agent_id or observation.get("control_epoch") != action.control_epoch:
            raise ValueError("observation does not match the prepared task and epoch")
        if observation.get("worker_revision") != worker.get("revision"):
            raise ValueError("observation is stale; inspect the task again")
        if not str(observation.get("summary") or "").strip():
            raise ValueError("runtime observation requires Desktop evidence")
        state = str(observation.get("runtime_state") or "")
        if state not in {"running", "suspended", "terminal", "faulted"}:
            raise ValueError("unsupported observed runtime state")
        self.registry.update(action.work_item_id, expected_control_epoch=action.control_epoch,
                             runtime_state=state, observed_runtime_evidence=dict(observation),
                             observed_runtime_activity_revision=int(worker.get("activity_revision") or 0),
                             observed_runtime_action_id=action_id,
                             control_state=("stopped" if state == "terminal" else
                                            "paused" if state == "suspended" and worker.get("desired_state") == "paused" else
                                            "idle_confirmed" if state == "suspended" else worker.get("control_state", "")),
                             interrupt_ack_started_at="" if state != "running" else worker.get("interrupt_ack_started_at", ""))

    def reconcile_actions(self, *, batch_id: str = "", site_key: str = "",
                          receipt_timeout_seconds: int = 30, max_probe_attempts: int = 2,
                          max_continuation_attempts: int = 2,
                          observed_at: str | None = None) -> dict:
        now = datetime.fromisoformat(observed_at or now_iso())
        unresolved = []
        settled = []
        cleanup_pending = []
        continuations = []
        with self.registry._lock, self.inbox._lock:
            workers = self.registry.list(batch_id=batch_id)
            latest_bindings = {str(worker["agent_id"]): worker["work_item_id"]
                               for worker in self.registry.list() if worker.get("agent_id")}
            outstanding_by_worker = {}
            for action in self.actions.outstanding(batch_id=batch_id, site_key=site_key):
                outstanding_by_worker.setdefault(action.work_item_id, []).append(action)
            for worker in workers:
                if site_key and worker.get("site_key") != site_key:
                    continue
                work_item_id = str(worker["work_item_id"])
                if worker.get("agent_id") and latest_bindings.get(str(worker["agent_id"])) != work_item_id:
                    continue
                cleanup = worker.get("desired_state") in {"paused", "cancelled"} or worker.get("work_state") in {"cancelled", "failed"}
                cleanup = cleanup or (is_terminal_work(str(worker.get("work_state") or "")) and worker.get("browser_policy") == "release")
                quiet = worker.get("runtime_state") in {"suspended", "terminal", "detached"}
                released = worker.get("browser_state") == "absent"
                needs_release = worker.get("browser_policy") == "release" or worker.get("desired_state") == "cancelled"
                complete = cleanup and quiet and (released or not needs_release)
                for action in outstanding_by_worker.get(work_item_id, []):
                    redundant = action.kind == WorkerActionKind.INTERRUPT and cleanup and quiet
                    redundant = redundant or (action.kind == WorkerActionKind.CLOSE and complete)
                    redundant = redundant or (action.kind == WorkerActionKind.PROBE and complete)
                    if redundant:
                        settled.append(self.actions.transition(action.action_id, status=WorkerActionStatus.SUPERSEDED,
                                                               error="observed lifecycle target reached; no delivery receipt inferred").as_dict())
                        self.actions.supersede_dependents(action.action_id, error="observed lifecycle target reached")
                        continue
                    age = (now - datetime.fromisoformat(action.updated_at or action.created_at)).total_seconds()
                    if action.status == WorkerActionStatus.CLAIMED and age >= max(1, receipt_timeout_seconds):
                        unresolved.append({"action_id": action.action_id, "work_item_id": work_item_id,
                                           "kind": action.kind.value, "status": action.status.value,
                                           "reason": "action receipt or lifecycle observation missing"})
                if not cleanup or complete:
                    evidence = worker.get("observed_runtime_evidence") or {}
                    activity_revision = int(worker.get("activity_revision") or 0)
                    resumable = (worker.get("desired_state") == "running" and worker.get("work_state") in {"queued", "running"}
                                 and worker.get("runtime_state") == "suspended" and evidence.get("runtime_state") == "suspended"
                                 and worker.get("observed_runtime_activity_revision") == activity_revision
                                 and worker.get("observed_runtime_action_id")
                                 and worker.get("continuation_observation_id") != worker.get("observed_runtime_action_id")
                                 and worker.get("registered_at") and execution_admitted(worker)
                                 and not worker.get("inflight_operations") and not self.actions.unsettled(work_item_id))
                    if resumable:
                        attempts = int(worker.get("continuation_attempts") or 0) if worker.get("continuation_activity_revision") == activity_revision else 0
                        if attempts >= max(0, max_continuation_attempts):
                            self.registry.update(work_item_id, work_state="waiting_user", control_state="continuation_exhausted",
                                                 last_error="unfinished work repeatedly ended without new execution progress")
                            continuations.append({"kind": "continuation_exhausted", "work_item_id": work_item_id})
                        else:
                            self.registry.update(work_item_id, continuation_observation_id=worker.get("observed_runtime_action_id"),
                                                 continuation_activity_revision=activity_revision,
                                                 continuation_attempts=attempts + 1)
                            command = create_worker_command(
                                command_id=f"worker_continuation:{work_item_id}:{worker.get('control_epoch')}:{activity_revision}:{attempts + 1}",
                                work_item_id=work_item_id, site_key=str(worker.get("site_key") or ""),
                                batch_id=str(worker.get("batch_id") or ""), kind=WorkerCommandKind.RESUME,
                                expected_control_epoch=int(worker.get("control_epoch") or 0),
                                message="Continue this unfinished work item from its current durable phase and remaining Apply Plan. A Desktop turn ending is not business completion; do not restart finished phases or return only a progress summary.",
                            )
                            self.enqueue(command)
                            continuations.append({"kind": "continuation_requested", "work_item_id": work_item_id})
                    continue
                cleanup_pending.append(work_item_id)
                outstanding = self.actions.unsettled(work_item_id)
                history = [action for action in self.actions.history(work_item_id)
                           if action.control_epoch == int(worker.get("control_epoch") or 0)]
                probes = [action for action in history if action.payload.get("cleanup_probe")]
                overdue = any(action.status in {WorkerActionStatus.CLAIMED, WorkerActionStatus.APPLIED}
                              and (now - datetime.fromisoformat(action.updated_at or action.created_at)).total_seconds()
                              >= max(1, receipt_timeout_seconds) for action in history)
                if overdue:
                    unresolved.append({"action_id": f"cleanup:{work_item_id}:{worker.get('control_epoch')}",
                                       "work_item_id": work_item_id, "kind": "cleanup", "status": "unconfirmed",
                                       "reason": "runtime or browser release is unconfirmed",
                                       "runtime_state": worker.get("runtime_state"), "browser_state": worker.get("browser_state"),
                                       "probe_attempts": len(probes)})
                for probe in probes:
                    age = (now - datetime.fromisoformat(probe.updated_at or probe.created_at)).total_seconds()
                    if probe.status in {WorkerActionStatus.PENDING, WorkerActionStatus.CLAIMED} and age >= max(1, receipt_timeout_seconds):
                        self.actions.transition(probe.action_id, status=WorkerActionStatus.SUPERSEDED,
                                                error="read-only cleanup probe timed out")
                outstanding = self.actions.unsettled(work_item_id)
                if not quiet and not any(action.kind == WorkerActionKind.PROBE for action in outstanding):
                    if len(probes) < max(0, max_probe_attempts):
                        self.actions.enqueue(create_worker_action(
                            kind=WorkerActionKind.PROBE, agent_id=str(worker.get("agent_id") or ""),
                            work_item_id=work_item_id, site_key=str(worker.get("site_key") or ""),
                            batch_id=str(worker.get("batch_id") or ""), control_epoch=int(worker.get("control_epoch") or 0),
                            action_id=f"cleanup_probe:{work_item_id}:{worker.get('control_epoch')}:{len(probes) + 1}",
                            payload={"cleanup_probe": True, "cleanup_key": f"cleanup:{work_item_id}:{worker.get('control_epoch')}",
                                     "worker_revision": worker.get("revision"), "reason": "Inspect the original Desktop task; report actual runtime state with evidence, never infer it from cancellation."},
                        ))
                if not outstanding and not any(action.kind in {WorkerActionKind.CLOSE, WorkerActionKind.INTERRUPT} for action in history):
                    for action in WorkerLifecycleReconciler().plan(worker, resource_policy=str(worker.get("browser_policy") or "unchanged")):
                        self.actions.enqueue(action)
            outstanding = self.actions.outstanding(batch_id=batch_id, site_key=site_key)
        return {"settled": settled, "outstanding": [action.as_dict() for action in outstanding],
                "unresolved": unresolved, "cleanup_pending": cleanup_pending,
                "continuations": continuations,
                "cleanup_complete": not outstanding and not cleanup_pending}

    def _acknowledge(self, action_id: str, *, applied: bool, error: str = "") -> tuple[WorkerAction, WorkerCommand | None]:
        self.actions.pending()
        existing = self.actions.get(action_id)
        if existing.status == WorkerActionStatus.SUPERSEDED:
            command_id = str(existing.payload.get("command_id") or "")
            return existing, self.inbox.get(command_id) if command_id else None
        if existing.status == (WorkerActionStatus.APPLIED if applied else WorkerActionStatus.FAILED):
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
            from .boundary import WorkerCommandBoundary

            received = command.kind == WorkerCommandKind.GUIDANCE and bool(
                WorkerCommandBoundary(self.workspace).receipt(command_id)
            )
            if command.status == WorkerCommandStatus.CLAIMED and not received:
                command = self.inbox.transition(command_id, status=WorkerCommandStatus.FAILED, error=error)
            return action, command
        command_actions = self.actions.for_command(command_id)
        if command_actions and all(row.status == WorkerActionStatus.APPLIED for row in command_actions):
            if command.status == WorkerCommandStatus.CLAIMED and command.kind != WorkerCommandKind.GUIDANCE:
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
            if worker.get("desired_state") != "running" or worker.get("work_state") not in {"running", "queued"}:
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
            if str(worker.get("runtime_state") or "") not in {"running", "suspended"} or str(worker.get("work_state") or "") not in {"running", "queued"}:
                continue
            if worker.get("slot_state") and not execution_admitted(worker):
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
        if command.kind == WorkerCommandKind.GUIDANCE and any(
            earlier.sequence < command.sequence
            for earlier in self.inbox.list(work_item_id=command.work_item_id, site_key=command.site_key,
                                           statuses={WorkerCommandStatus.CLAIMED})
        ):
            return []
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
        message = command.message
        if command.kind == WorkerCommandKind.GUIDANCE:
            message = (
                f"CareerEng guidance {command.command_id}; work_item_id={command.work_item_id}; "
                f"expected_control_epoch={command.expected_control_epoch}. "
                "Acknowledge received then applied or failed with careereng_ack_worker_guidance; "
                "do not execute an already acknowledged command twice.\n\n" + message
            )
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
                    payload={**base_payload, "prompt": message},
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
                            "message": message,
                            "depends_on_action_id": resume.action_id,
                        },
                        **common,
                    )
                )
        else:
            planned.append(
                create_worker_action(
                    kind=WorkerActionKind.SEND,
                    payload={**base_payload, "message": message},
                    **common,
                )
            )
        return [self.actions.enqueue(action) for action in planned]
