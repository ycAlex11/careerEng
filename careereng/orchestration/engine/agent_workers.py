"""Backend-neutral worker lifecycle for persisted site work items.

The coordinator owns generic scheduling and retained work-item state. An
adapter supplies the concrete thread/turn transport (Codex today, another
external agent tomorrow) and translates its events into ``AgentWorkerEvent``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from careereng.orchestration.agent_protocol.work_items import work_item_id_from_payload
from careereng.orchestration.engine.site_work_items import SiteWorkItem, SiteWorkItemScheduler
from careereng.platform.sessions import SiteWorkerSessionStore
from careereng.utils import now_iso, read_json


@dataclass(frozen=True)
class AgentWorkerEvent:
    """Normalized external-agent event consumed by the lifecycle coordinator."""

    kind: str
    thread_id: str
    turn_id: str = ""
    turn_status: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    transport: dict[str, Any] = field(default_factory=dict)


class AgentThreadTransport(Protocol):
    """Small transport boundary used by the generic worker coordinator."""

    def start(self) -> dict[str, Any]: ...

    def start_thread(self, *, cwd: Path, timeout_seconds: float | None = None) -> dict[str, Any]: ...

    def resume_thread(self, thread_id: str) -> dict[str, Any]: ...

    def start_turn(self, *, thread_id: str, prompt: str) -> dict[str, Any]: ...

    def interrupt_turn(self, *, thread_id: str, turn_id: str) -> dict[str, Any]: ...

    def close(self) -> None: ...


@dataclass
class AgentWorkerRecord:
    site_key: str
    batch_id: str
    payload_path: Path
    phase_session_path: Path
    work_item_id: str = ""
    thread_id: str = ""
    turn_id: str = ""
    status: str = ""
    resume_message: str = ""
    context_revision: int = 0
    worker_session_id: str = ""
    session_batch_ordinal: int = 0
    session_reused: bool = False
    session_rotation_reason: str = ""
    last_error: str = ""
    recovery_attempts: int = 0
    recovery_pending: bool = False
    # Summary turns have no browser activity to observe and may wait for a
    # proposal/apply authorization, so they are outside the browser watchdog.
    watchdog_enabled: bool = True
    last_activity_monotonic: float = field(default_factory=time.monotonic, repr=False)
    # This is intentionally process-local. A persisted empty turn id after a
    # host restart is resumable; an in-memory launch is not a second turn.
    turn_start_inflight: bool = False
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)


class SiteAgentWorkerCoordinator:
    """Coordinate bounded work items through retained site worker sessions."""

    START_ATTEMPTS = 2
    THREAD_START_TIMEOUT_SECONDS = 45.0

    def __init__(
        self,
        *,
        project_root: Path,
        worker_limit: int,
        transport_factory: Callable[[Callable[[AgentWorkerEvent], None]], AgentThreadTransport],
        load_binding: Callable[[Path], dict[str, Any]],
        bind_record: Callable[[AgentWorkerRecord], None],
        session_store: SiteWorkerSessionStore | None = None,
        backend: str = "external_agent",
        max_effective_batches_per_session: int = 5,
        idle_timeout_seconds: int = 180,
        max_resume_attempts: int = 2,
        on_record: Callable[[AgentWorkerRecord], None] | None = None,
        on_usage: Callable[[AgentWorkerRecord, dict[str, Any]], None] | None = None,
        on_recovery: Callable[[AgentWorkerRecord, str], None] | None = None,
        on_transport_event: Callable[[AgentWorkerRecord | None, dict[str, Any]], None] | None = None,
    ):
        self.project_root = Path(project_root).resolve()
        self.worker_limit = max(1, int(worker_limit or 1))
        self.transport_factory = transport_factory
        self.load_binding = load_binding
        self.bind_record = bind_record
        self.session_store = session_store or SiteWorkerSessionStore(self.project_root / "workspace")
        self.backend = str(backend or "external_agent")
        self.max_effective_batches_per_session = max(1, int(max_effective_batches_per_session or 1))
        self.idle_timeout_seconds = max(1, int(idle_timeout_seconds or 1))
        self.max_resume_attempts = max(0, int(max_resume_attempts or 0))
        self.on_record = on_record
        self.on_usage = on_usage
        self.on_recovery = on_recovery
        self.on_transport_event = on_transport_event
        self._lock = threading.RLock()
        self._scheduler = SiteWorkItemScheduler(worker_limit=self.worker_limit)
        self._active: dict[str, AgentWorkerRecord] = {}
        self._paused: dict[str, AgentWorkerRecord] = {}
        self._pause_requested: set[str] = set()
        self._successors: dict[str, AgentWorkerRecord] = {}
        self._by_thread: dict[str, AgentWorkerRecord] = {}
        self._server: AgentThreadTransport | None = None
        self._server_start_lock = threading.Lock()
        self._closed = threading.Event()
        self._watchdog = threading.Thread(
            target=self._watchdog_loop,
            name="careereng-agent-progress-watchdog",
            daemon=True,
        )
        self._watchdog.start()

    def enqueue(self, record: AgentWorkerRecord) -> AgentWorkerRecord:
        with self._lock:
            self._bind_session_locked(record)
            existing = self._active.get(record.site_key)
            if existing is not None:
                if existing.work_item_id != record.work_item_id:
                    self._successors[record.site_key] = record
                return existing
            accepted = self._scheduler.enqueue(SiteWorkItem(record.site_key, record.batch_id, record))
            if not accepted:
                return record
            self._dispatch_locked()
        return record

    def resume(self, *, site_key: str, message: str) -> AgentWorkerRecord | None:
        with self._lock:
            record = self._active.get(str(site_key))
            if record is None:
                return None
            if record.status in {"pausing", "starting", "waiting_user"} or record.turn_start_inflight:
                # A turn/start call is invalid until App Server confirms that
                # the interrupted turn has reached its terminal event.
                return record
            if record.turn_id:
                # One Codex thread has one in-flight turn.  Preserve a user
                # continuation for its next context read instead of starting
                # a competing turn on the same thread.
                record.resume_message = _append_resume_message(record.resume_message, message)
                record.updated_at = now_iso()
                self._persist_locked(record)
                return record
            server = self._ensure_server_locked()
            if record.thread_id:
                self._associate_thread_locked(record, record.thread_id)
                try:
                    server.resume_thread(record.thread_id)
                except Exception:
                    self._forget_thread_association_locked(record)
                    raise
            result = server.start_turn(thread_id=record.thread_id, prompt=_resume_prompt(record, message))
            record.turn_id = _required_turn_id(result)
            record.status = "running"
            self._persist_locked(record)
            return record

    def pause(self, *, site_key: str) -> AgentWorkerRecord | None:
        """Interrupt one turn while retaining its thread/session binding."""

        with self._lock:
            record = self._active.get(str(site_key))
            if record is None:
                return self._paused.get(str(site_key))
            if record.status == "pausing":
                return record
            self._pause_requested.add(record.site_key)
            if record.thread_id and record.turn_id:
                try:
                    self._interrupt_locked(record, source="user_pause")
                except RuntimeError:
                    # The persisted record remains resumable; a later resume
                    # can use the same thread if transport confirms it idle.
                    pass
            record.status = "pausing"
            record.updated_at = now_iso()
            self._persist_locked(record)
            return record

    def resume_work_order(self, record: AgentWorkerRecord, *, message: str) -> AgentWorkerRecord:
        """Resume a retained work item after a host or transport restart."""

        with self._lock:
            current = self._active.get(record.site_key)
            if current is not None:
                if _same_work_item(current, record):
                    if (
                        current.turn_id
                        or current.turn_start_inflight
                        or current.status in {"running", "pausing", "starting", "waiting_user"}
                    ):
                        # A user message can arrive while this exact work item
                        # is operating the page. Retain it for the next context
                        # read instead of opening a competing Codex turn.
                        current.resume_message = _append_resume_message(current.resume_message, message)
                        current.updated_at = now_iso()
                        self._persist_locked(current)
                        return current

                    # A host restart can retain an active scheduler slot while
                    # its external turn was never started. Resume that same
                    # work item and thread instead of treating queued as live.
                    current.resume_message = _append_resume_message(current.resume_message, message)
                    if current.thread_id:
                        server = self._ensure_server_locked()
                        self._associate_thread_locked(current, current.thread_id)
                        try:
                            server.resume_thread(current.thread_id)
                        except Exception:
                            self._forget_thread_association_locked(current)
                            raise
                        result = server.start_turn(
                            thread_id=current.thread_id,
                            prompt=_resume_prompt(current, current.resume_message),
                        )
                        current.turn_id = _required_turn_id(result)
                        current.resume_message = ""
                        current.status = "running"
                        current.updated_at = now_iso()
                        self._persist_locked(current)
                        return current
                    self._start_locked(current)
                    return current
                self._queue_successor_locked(record, message=message)
                return current

            paused = self._paused.get(record.site_key)
            if paused is not None and _same_work_item(paused, record) and paused.thread_id:
                self._paused.pop(record.site_key, None)
                server = self._ensure_server_locked()
                self._associate_thread_locked(paused, paused.thread_id)
                try:
                    server.resume_thread(paused.thread_id)
                except Exception:
                    self._forget_thread_association_locked(paused)
                    self._paused[record.site_key] = paused
                    raise
                result = server.start_turn(thread_id=paused.thread_id, prompt=_resume_prompt(paused, message))
                paused.turn_id = _required_turn_id(result)
                paused.status = "running"
                paused.updated_at = now_iso()
                self._active[paused.site_key] = paused
                self._by_thread[paused.thread_id] = paused
                self._persist_locked(paused)
                return paused

            # The host no longer has an in-memory owner for this work item.
            # A worker is host-local: recreate it from the durable work item
            # and retained thread binding without persisting a queue state.
            self._scheduler.discard(record.site_key)
            record.resume_message = _append_resume_message(record.resume_message, message)
            self._bind_session_locked(record)
            accepted = self._scheduler.enqueue(SiteWorkItem(record.site_key, record.batch_id, record))
            if not accepted:
                raise RuntimeError(f"unable to claim recovered worker slot for site={record.site_key}")
            self._dispatch_locked()
            return self._active.get(record.site_key, record)

    def _queue_successor_locked(self, record: AgentWorkerRecord, *, message: str) -> None:
        """Keep a distinct work item pending without disturbing the active one."""

        existing = self._successors.get(record.site_key)
        if existing is not None and _same_work_item(existing, record):
            existing.resume_message = _append_resume_message(existing.resume_message, message)
            return
        record.resume_message = _append_resume_message(record.resume_message, message)
        self._successors[record.site_key] = record

    def cancel(self, *, site_key: str) -> AgentWorkerRecord | None:
        with self._lock:
            record = self._active.get(str(site_key))
            if record is None:
                return None
            if record.thread_id and record.turn_id:
                try:
                    self._interrupt_locked(record, source="batch_cancel")
                except RuntimeError:
                    pass
            record.status = "cancelling"
            self._persist_locked(record)
            return record

    def release(self, *, site_key: str) -> AgentWorkerRecord | None:
        with self._lock:
            record = self._active.pop(str(site_key), None)
            self._successors.pop(str(site_key), None)
            paused = self._paused.pop(str(site_key), None)
            self._pause_requested.discard(str(site_key))
            if record is None:
                record = paused
            if record is None:
                return None
            self._by_thread.pop(record.thread_id, None)
            record.status = "released"
            self._persist_locked(record)
            self._dispatch_locked()
            return record

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "worker_limit": self.worker_limit,
                "active": [_record_payload(row) for row in self._active.values()],
                "queued": [_record_payload(item.payload) for item in self._scheduler.snapshot()["queued"]],
                "paused": [_record_payload(row) for row in self._paused.values()],
            }

    def record_activity(self, *, site_key: str) -> None:
        """Record an objective CareerEng tool interaction for one active site."""

        with self._lock:
            record = self._active.get(str(site_key or ""))
            if record is None:
                return
            record.last_activity_monotonic = time.monotonic()

    def record_for_thread(self, thread_id: str) -> AgentWorkerRecord | None:
        """Return the live owner of one external-agent thread, if any."""

        with self._lock:
            return self._by_thread.get(str(thread_id or ""))

    def close(self) -> None:
        with self._lock:
            self._closed.set()
            if self._server is not None:
                self._server.close()
            self._server = None

    def _dispatch_locked(self) -> None:
        for item in self._scheduler.claim_ready():
            try:
                self._start_locked(item.payload)
            except Exception as exc:
                # The durable work item remains recoverable. The temporary
                # worker is released instead of becoming a fake queue owner.
                item.payload.status = "unavailable"
                item.payload.last_error = f"{type(exc).__name__}: {exc}"
                item.payload.updated_at = now_iso()
                self._persist_locked(item.payload)
                self._scheduler.complete(item.site_key)
                raise

    def _start_locked(self, record: AgentWorkerRecord) -> None:
        # Publish ownership before any external RPC. The startup thread does
        # App Server communication without holding the worker-state lock.
        record.turn_id = ""
        record.turn_start_inflight = True
        record.status = "starting"
        record.last_activity_monotonic = time.monotonic()
        record.updated_at = now_iso()
        self._active[record.site_key] = record
        self._persist_locked(record)
        thread = threading.Thread(
            target=self._start_worker_async,
            args=(record,),
            name=f"careereng-agent-start-{record.site_key}",
            daemon=True,
        )
        thread.start()

    def _start_worker_async(self, record: AgentWorkerRecord) -> None:
        """Create or resume an agent thread outside the worker-state lock."""

        try:
            binding = self.load_binding(record.payload_path)
            thread_id = str(record.thread_id or binding.get("thread_id") or "")
            server = self._ensure_server()
            if thread_id:
                with self._lock:
                    if self._active.get(record.site_key) is not record:
                        return
                    self._associate_thread_locked(record, thread_id)
                try:
                    server.resume_thread(thread_id)
                except Exception:
                    with self._lock:
                        self._forget_thread_association_locked(record)
                    raise
            else:
                result = server.start_thread(cwd=record.payload_path.parent, timeout_seconds=self.THREAD_START_TIMEOUT_SECONDS)
                thread_id = _thread_id_from(result)
            if not thread_id:
                raise RuntimeError("external agent transport returned no thread id")
            with self._lock:
                if self._active.get(record.site_key) is not record:
                    return
                self._associate_thread_locked(record, thread_id)
                self.session_store.bind_thread(
                    worker_session_id=record.worker_session_id,
                    thread_id=thread_id,
                    reason=record.session_rotation_reason,
                )
                prompt = _resume_prompt(record, record.resume_message) if record.resume_message else _work_prompt(record)
                record.resume_message = ""
                record.updated_at = now_iso()
                self._persist_locked(record)
            self._start_turn_async(record, prompt)
        except Exception as exc:
            with self._lock:
                if self._active.get(record.site_key) is not record:
                    return
                record.turn_start_inflight = False
                record.status = "unavailable"
                record.last_error = f"{type(exc).__name__}: {exc}"
                record.updated_at = now_iso()
                self._persist_locked(record)
                self._active.pop(record.site_key, None)
                self._scheduler.complete(record.site_key)
                self._by_thread.pop(record.thread_id, None)
                self._dispatch_locked()

    def _start_turn_async(self, record: AgentWorkerRecord, prompt: str) -> None:
        last_error: Exception | None = None
        for attempt in range(self.START_ATTEMPTS):
            try:
                result = self._ensure_server().start_turn(thread_id=record.thread_id, prompt=prompt)
                turn_id = _required_turn_id(result)
                with self._lock:
                    if self._active.get(record.site_key) is not record:
                        return
                    record.turn_id = turn_id
                    record.turn_start_inflight = False
                    should_interrupt = record.site_key in self._pause_requested
                    record.status = "pausing" if should_interrupt else "running"
                    record.last_error = ""
                    record.last_activity_monotonic = time.monotonic()
                    record.updated_at = now_iso()
                    self._persist_locked(record)
                if should_interrupt:
                    try:
                        self._interrupt_locked(record, source="pause_during_turn_start")
                    except RuntimeError:
                        pass
                return
            except RuntimeError as exc:
                last_error = exc
                if attempt + 1 < self.START_ATTEMPTS:
                    continue
        with self._lock:
            if self._active.get(record.site_key) is not record:
                return
            record.turn_id = ""
            record.turn_start_inflight = False
            record.status = "unavailable"
            record.last_error = f"{type(last_error).__name__}: {last_error}"
            record.updated_at = now_iso()
            self._persist_locked(record)
            self._active.pop(record.site_key, None)
            self._scheduler.complete(record.site_key)
            self._by_thread.pop(record.thread_id, None)
            self._dispatch_locked()

    def _watchdog_loop(self) -> None:
        """Request a same-thread recovery only after objective tool inactivity.

        This is deliberately blind to site semantics. It never chooses a browser
        action or advances a job; it only asks the retained agent to inspect its
        current scoped page again.
        """

        while not self._closed.wait(timeout=1.0):
            with self._lock:
                now = time.monotonic()
                for record in list(self._active.values()):
                    if (
                        not record.watchdog_enabled
                        or record.status != "running"
                        or not record.turn_id
                        or record.recovery_pending
                    ):
                        continue
                    if now - record.last_activity_monotonic < self.idle_timeout_seconds:
                        continue
                    if record.recovery_attempts >= self.max_resume_attempts:
                        record.status = "execution_unavailable"
                        record.last_error = "no CareerEng tool progress after configured recovery attempts"
                        record.updated_at = now_iso()
                        self._persist_locked(record)
                        self._emit_recovery_locked(record, "exhausted")
                        try:
                            self._interrupt_locked(record, source="idle_recovery_exhausted")
                        except RuntimeError:
                            pass
                        continue
                    record.recovery_attempts += 1
                    record.recovery_pending = True
                    record.status = "recovering"
                    record.updated_at = now_iso()
                    self._persist_locked(record)
                    self._emit_recovery_locked(record, "detected")
                    try:
                        self._interrupt_locked(record, source="idle_recovery")
                    except RuntimeError as exc:
                        record.recovery_pending = False
                        record.status = "running"
                        record.last_error = f"{type(exc).__name__}: {exc}"
                        record.last_activity_monotonic = now
                        self._persist_locked(record)

    def _emit_recovery_locked(self, record: AgentWorkerRecord, status: str) -> None:
        if self.on_recovery is not None:
            self.on_recovery(record, status)

    def _interrupt_locked(self, record: AgentWorkerRecord, *, source: str) -> None:
        """Trace the mechanical interruption source before requesting it."""

        self._emit_transport_event_locked(
            record,
            {
                "event": "interrupt_requested",
                "source": source,
                "thread_id": record.thread_id,
                "turn_id": record.turn_id,
            },
        )
        self._ensure_server_locked().interrupt_turn(thread_id=record.thread_id, turn_id=record.turn_id)

    def _emit_transport_event_locked(self, record: AgentWorkerRecord | None, payload: dict[str, Any]) -> None:
        if self.on_transport_event is not None:
            self.on_transport_event(record, dict(payload))

    def _associate_thread_locked(self, record: AgentWorkerRecord, thread_id: str) -> None:
        """Associate early transport events with their durable work item."""

        record.thread_id = str(thread_id or "")
        if record.thread_id:
            self._by_thread[record.thread_id] = record

    def _forget_thread_association_locked(self, record: AgentWorkerRecord) -> None:
        thread_id = str(record.thread_id or "")
        if thread_id and self._by_thread.get(thread_id) is record:
            self._by_thread.pop(thread_id, None)

    def _ensure_server(self) -> AgentThreadTransport:
        """Initialize the shared transport without holding worker-state locks."""

        with self._server_start_lock:
            if self._server is None:
                server = self.transport_factory(self._on_event)
                server.start()
                self._server = server
            return self._server

    def _ensure_server_locked(self) -> AgentThreadTransport:
        """Compatibility wrapper for older lifecycle paths still under lock."""

        return self._ensure_server()

    def _on_event(self, event: AgentWorkerEvent) -> None:
        if event.kind == "transport":
            with self._lock:
                self._emit_transport_event_locked(
                    self._by_thread.get(str(event.thread_id)),
                    dict(event.transport),
                )
            return
        if event.kind == "usage":
            with self._lock:
                record = self._by_thread.get(str(event.thread_id))
                if record is not None and self.on_usage is not None:
                    self.on_usage(record, dict(event.usage))
            return
        if event.kind != "turn_completed":
            return
        with self._lock:
            record = self._by_thread.get(str(event.thread_id))
            if record is None:
                return
            prior_status = record.status
            record.turn_id = str(event.turn_id or record.turn_id)
            record.status = str(event.turn_status or "completed")
            record.updated_at = now_iso()
            if prior_status == "execution_unavailable":
                self._persist_locked(record)
                self._active.pop(record.site_key, None)
                self._scheduler.complete(record.site_key)
                self._by_thread.pop(str(event.thread_id), None)
                self._dispatch_locked()
                return
            if record.site_key in self._pause_requested:
                self._pause_requested.discard(record.site_key)
                record.turn_id = ""
                record.status = "paused"
                self._persist_locked(record)
                self._active.pop(record.site_key, None)
                self._scheduler.complete(record.site_key)
                self._by_thread.pop(str(event.thread_id), None)
                self._paused[record.site_key] = record
                self._dispatch_locked()
                return
            if record.recovery_pending:
                record.recovery_pending = False
                record.turn_id = ""
                try:
                    result = self._ensure_server_locked().start_turn(
                        thread_id=record.thread_id,
                        prompt=_recovery_prompt(record),
                    )
                except RuntimeError as exc:
                    record.status = "execution_unavailable"
                    record.last_error = f"{type(exc).__name__}: {exc}"
                    self._persist_locked(record)
                    self._emit_recovery_locked(record, "exhausted")
                    self._active.pop(record.site_key, None)
                    self._scheduler.complete(record.site_key)
                    self._by_thread.pop(str(event.thread_id), None)
                    self._dispatch_locked()
                    return
                record.turn_id = _required_turn_id(result)
                record.status = "running"
                record.last_activity_monotonic = time.monotonic()
                self._persist_locked(record)
                self._emit_recovery_locked(record, "resumed")
                return
            current_payload = worker_record_from_payload(record.payload_path)
            worker_state = _worker_state_from_payload(record.payload_path)
            if (
                current_payload.work_item_id == record.work_item_id
                and current_payload.context_revision > record.context_revision
                and worker_state == "active"
            ):
                record.context_revision = current_payload.context_revision
                record.watchdog_enabled = current_payload.watchdog_enabled
                try:
                    result = self._ensure_server_locked().start_turn(thread_id=record.thread_id, prompt=_continue_prompt(record))
                except RuntimeError:
                    record.status = "interrupted"
                    self._persist_locked(record)
                    self._active.pop(record.site_key, None)
                    self._scheduler.complete(record.site_key)
                    self._by_thread.pop(str(event.thread_id), None)
                    self._dispatch_locked()
                    return
                record.turn_id = _required_turn_id(result)
                record.status = "running"
                self._persist_locked(record)
                return
            self._persist_locked(record, bind_thread=current_payload.work_item_id == record.work_item_id)
            self._active.pop(record.site_key, None)
            self._scheduler.complete(record.site_key)
            self._by_thread.pop(str(event.thread_id), None)
            if worker_state == "waiting_user":
                record.status = "waiting_user"
                self._paused[record.site_key] = record
            successor = self._successors.pop(record.site_key, None)
            if successor is not None:
                self._scheduler.enqueue(SiteWorkItem(successor.site_key, successor.batch_id, successor))
            self._dispatch_locked()

    def _persist_locked(self, record: AgentWorkerRecord, *, bind_thread: bool = True) -> None:
        if bind_thread:
            self.bind_record(record)
        if self.on_record is not None:
            self.on_record(record)

    def _bind_session_locked(self, record: AgentWorkerRecord) -> None:
        if record.worker_session_id:
            return
        binding = self.session_store.bind_batch(
            site_key=record.site_key,
            backend=self.backend,
            batch_id=record.batch_id,
            max_effective_batches=self.max_effective_batches_per_session,
        )
        record.worker_session_id = binding.worker_session_id
        record.session_batch_ordinal = binding.batch_ordinal
        record.session_reused = binding.reused
        record.session_rotation_reason = binding.rotation_reason
        if binding.thread_id:
            record.thread_id = binding.thread_id


def worker_record_from_payload(payload_path: Path) -> AgentWorkerRecord:
    payload = read_json(Path(payload_path))
    phase_session = Path(payload_path).parent / "phase_session.json"
    binding = payload.get("codex_thread") if isinstance(payload.get("codex_thread"), dict) else {}
    phase_context = payload.get("current_phase_context") if isinstance(payload.get("current_phase_context"), dict) else {}
    phase = phase_context.get("phase") if isinstance(phase_context.get("phase"), dict) else {}
    phase_slug = str(payload.get("current_phase") or phase.get("slug") or "")
    return AgentWorkerRecord(
        site_key=str(payload.get("site_key") or ""),
        batch_id=str(payload.get("batch_id") or ""),
        payload_path=Path(payload_path),
        phase_session_path=phase_session,
        work_item_id=work_item_id_from_payload(payload) or Path(payload_path).parent.name,
        thread_id=str(binding.get("thread_id") or ""),
        turn_id=str(binding.get("turn_id") or ""),
        status="waiting_user" if str(payload.get("worker_state") or "") == "waiting_user" else "",
        last_error=str(binding.get("last_error") or ""),
        recovery_attempts=int(binding.get("recovery_attempts") or 0),
        context_revision=int(payload.get("context_revision") or 0),
        watchdog_enabled=phase_slug != "evolution_summary",
    )


def _same_work_item(left: AgentWorkerRecord, right: AgentWorkerRecord) -> bool:
    """Match the durable site-batch work item and its retained binding."""

    if (
        left.site_key != right.site_key
        or left.batch_id != right.batch_id
        or left.work_item_id != right.work_item_id
    ):
        return False
    return not (left.thread_id and right.thread_id and left.thread_id != right.thread_id)


def _append_resume_message(existing: str, message: str) -> str:
    """Preserve user input until the retained work item reads fresh context."""

    incoming = str(message or "").strip()
    if not incoming:
        return str(existing or "")
    prior = str(existing or "").strip()
    if not prior or incoming in prior:
        return incoming or prior
    return f"{prior}\n{incoming}"


def _work_prompt(record: AgentWorkerRecord) -> str:
    payload = read_json(record.payload_path)
    evolution = payload.get("evolution_solution") if isinstance(payload, dict) else {}
    if isinstance(evolution, dict) and str(evolution.get("run_id") or "").strip():
        return _continue_prompt(record)
    return (
        "You are a bounded CareerEng worker.\n"
        f"Work item ID: {record.work_item_id}\n"
        "Your first action must directly call careereng_get_work_item_context with that ID. Do not list, search, or inspect tools first. "
        "Read only the context resources you need through careereng_read_work_item_resource, then use only CareerEng MCP capabilities returned by the context. "
        "Use only the work-item-scoped browser and state tools; never use legacy site-key execution tools. "
        "Do not inspect project files, work-order files, or create a browser runtime. Operate only this retained scope. "
        "Report progress with CareerEng state tools. After a phase_result that advances the item, fetch the same work item context again and continue from its new phase."
    )


def _resume_prompt(record: AgentWorkerRecord, message: str) -> str:
    return (
        "Resume the existing bounded CareerEng work item from its retained scope.\n"
        f"Work item ID: {record.work_item_id}\n"
        f"User continuation: {message}\n"
        "First directly call careereng_get_work_item_context. Do not list or search tools. "
        "Then read the continuation resource; it is the latest user-supplied state and supersedes stale pending observations. "
        "Before reporting any phase result, call the scoped browser snapshot tool once and continue from that live page."
    )


def _continue_prompt(record: AgentWorkerRecord) -> str:
    payload = read_json(record.payload_path)
    evolution = payload.get("evolution_solution") if isinstance(payload, dict) else {}
    if isinstance(evolution, dict) and str(evolution.get("run_id") or "").strip():
        status = str(evolution.get("status") or "waiting_solution")
        next_steps = (
            "Call careereng_complete_evolution_solution with this work item ID and evolution run ID."
            if status == "applied"
            else (
                "Call careereng_apply_evolution_solution, then careereng_complete_evolution_solution with this work item ID and evolution run ID."
                if status == "proposal_written"
                else "Construct the required proposal JSON, call careereng_submit_evolution_proposal, then careereng_apply_evolution_solution. "
                "After a successful apply, call careereng_complete_evolution_solution with this work item ID and evolution run ID."
            )
        )
        return (
            "Continue the same bounded CareerEng site worker with its evolution summary task.\n"
            f"Work item ID: {record.work_item_id}\n"
            f"Evolution run: {str(evolution.get('run_id') or '')}\n"
            "First call careereng_get_work_item_context, then read evolution_summary_brief. The context catalog exposes the evolution protocol, "
            "proposal schema, strategy router, solution request, and evidence pack. Read only the resources or text slices you need; use this thread's "
            "existing execution context before requesting stored evidence. "
            f"{next_steps} "
            "Do not create a browser runtime, inspect project files, or change Python code."
        )
    return (
        "Continue the same bounded CareerEng site-batch work item.\n"
        f"Work item ID: {record.work_item_id}\n"
        "The previous phase or apply target completed. First call careereng_get_work_item_context with this same ID, "
        "then continue only its current phase using the returned scoped tools and resources. Do not inspect project files or create a new browser runtime."
    )


def _recovery_prompt(record: AgentWorkerRecord) -> str:
    return (
        "Continue the same bounded CareerEng work item after an execution idle timeout.\n"
        f"Work item ID: {record.work_item_id}\n"
        "First call careereng_get_work_item_context with this same ID, then take a fresh scoped browser snapshot. "
        "Use the live page and returned context to choose the next action. Do not create a browser, inspect project files, "
        "change site policy, or report a job outcome solely because this recovery occurred."
    )


def _worker_state_from_payload(payload_path: Path) -> str:
    payload = read_json(Path(payload_path))
    return str(payload.get("worker_state") or "").strip()


def _turn_id_from(result: dict[str, Any]) -> str:
    turn = result.get("turn") if isinstance(result.get("turn"), dict) else {}
    return str(turn.get("id") or result.get("turn_id") or result.get("turnId") or "")


def _thread_id_from(result: dict[str, Any]) -> str:
    thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
    return str(thread.get("id") or result.get("thread_id") or result.get("threadId") or "")


def _required_turn_id(result: dict[str, Any]) -> str:
    turn_id = _turn_id_from(result)
    if not turn_id:
        raise RuntimeError("external agent transport returned no turn id")
    return turn_id


def _record_payload(record: AgentWorkerRecord) -> dict[str, Any]:
    return {
        "site_key": record.site_key,
        "batch_id": record.batch_id,
        "work_item_id": record.work_item_id,
        "thread_id": record.thread_id,
        "turn_id": record.turn_id,
        "status": record.status,
        "context_revision": record.context_revision,
        "worker_session_id": record.worker_session_id,
        "session_batch_ordinal": record.session_batch_ordinal,
        "session_reused": record.session_reused,
        "session_rotation_reason": record.session_rotation_reason,
        "last_error": record.last_error,
        "recovery_attempts": record.recovery_attempts,
        "watchdog_enabled": record.watchdog_enabled,
        "updated_at": record.updated_at,
    }
