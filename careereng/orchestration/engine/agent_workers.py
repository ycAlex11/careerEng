"""Backend-neutral worker lifecycle for persisted site work items.

The coordinator owns generic scheduling and retained work-item state. An
adapter supplies the concrete thread/turn transport (Codex today, another
external agent tomorrow) and translates its events into ``AgentWorkerEvent``.
"""

from __future__ import annotations

import threading
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
        on_record: Callable[[AgentWorkerRecord], None] | None = None,
        on_usage: Callable[[AgentWorkerRecord, dict[str, Any]], None] | None = None,
    ):
        self.project_root = Path(project_root).resolve()
        self.worker_limit = max(1, int(worker_limit or 1))
        self.transport_factory = transport_factory
        self.load_binding = load_binding
        self.bind_record = bind_record
        self.session_store = session_store or SiteWorkerSessionStore(self.project_root / "workspace")
        self.backend = str(backend or "external_agent")
        self.max_effective_batches_per_session = max(1, int(max_effective_batches_per_session or 1))
        self.on_record = on_record
        self.on_usage = on_usage
        self._lock = threading.RLock()
        self._scheduler = SiteWorkItemScheduler(worker_limit=self.worker_limit)
        self._active: dict[str, AgentWorkerRecord] = {}
        self._paused: dict[str, AgentWorkerRecord] = {}
        self._pause_requested: set[str] = set()
        self._successors: dict[str, AgentWorkerRecord] = {}
        self._by_thread: dict[str, AgentWorkerRecord] = {}
        self._server: AgentThreadTransport | None = None

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
                server.resume_thread(record.thread_id)
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
                    self._ensure_server_locked().interrupt_turn(thread_id=record.thread_id, turn_id=record.turn_id)
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
                        server.resume_thread(current.thread_id)
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
                server.resume_thread(paused.thread_id)
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
                    self._ensure_server_locked().interrupt_turn(thread_id=record.thread_id, turn_id=record.turn_id)
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

    def close(self) -> None:
        with self._lock:
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
        server = self._ensure_server_locked()
        binding = self.load_binding(record.payload_path)
        thread_id = str(record.thread_id or binding.get("thread_id") or "")
        if thread_id:
            server.resume_thread(thread_id)
        else:
            thread = server.start_thread(cwd=record.payload_path.parent, timeout_seconds=self.THREAD_START_TIMEOUT_SECONDS)
            thread_id = str((thread.get("thread") or {}).get("id") or thread.get("threadId") or "")
        if not thread_id:
            raise RuntimeError("external agent transport returned no thread id")
        record.thread_id = thread_id
        self.session_store.bind_thread(
            worker_session_id=record.worker_session_id,
            thread_id=thread_id,
            reason=record.session_rotation_reason,
        )
        prompt = _resume_prompt(record, record.resume_message) if record.resume_message else _work_prompt(record)
        record.resume_message = ""
        # App Server can synchronously ask its client for approval while a
        # turn is starting. Publish ownership before that RPC begins and run
        # it outside the coordinator lock so the request can be persisted and
        # answered through this exact connection.
        record.turn_id = ""
        record.turn_start_inflight = True
        record.status = "starting"
        record.updated_at = now_iso()
        self._active[record.site_key] = record
        self._by_thread[thread_id] = record
        self._persist_locked(record)
        thread = threading.Thread(
            target=self._start_turn_async,
            args=(record, prompt),
            name=f"careereng-agent-turn-{record.site_key}",
            daemon=True,
        )
        thread.start()

    def _start_turn_async(self, record: AgentWorkerRecord, prompt: str) -> None:
        last_error: Exception | None = None
        for attempt in range(self.START_ATTEMPTS):
            try:
                result = self._ensure_server_locked().start_turn(thread_id=record.thread_id, prompt=prompt)
                turn_id = _required_turn_id(result)
                with self._lock:
                    if self._active.get(record.site_key) is not record:
                        return
                    record.turn_id = turn_id
                    record.turn_start_inflight = False
                    should_interrupt = record.site_key in self._pause_requested
                    record.status = "pausing" if should_interrupt else "running"
                    record.last_error = ""
                    record.updated_at = now_iso()
                    self._persist_locked(record)
                if should_interrupt:
                    try:
                        self._ensure_server_locked().interrupt_turn(thread_id=record.thread_id, turn_id=turn_id)
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

    def _ensure_server_locked(self) -> AgentThreadTransport:
        if self._server is None:
            self._server = self.transport_factory(self._on_event)
            self._server.start()
        return self._server

    def _on_event(self, event: AgentWorkerEvent) -> None:
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
            record.turn_id = str(event.turn_id or record.turn_id)
            record.status = str(event.turn_status or "completed")
            record.updated_at = now_iso()
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
            current_payload = worker_record_from_payload(record.payload_path)
            worker_state = _worker_state_from_payload(record.payload_path)
            if (
                current_payload.work_item_id == record.work_item_id
                and current_payload.context_revision > record.context_revision
                and worker_state == "active"
            ):
                record.context_revision = current_payload.context_revision
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
        context_revision=int(payload.get("context_revision") or 0),
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
        return (
            "Continue the same bounded CareerEng site worker with its evolution summary task.\n"
            f"Work item ID: {record.work_item_id}\n"
            f"Evolution run: {str(evolution.get('run_id') or '')}\n"
            f"Solution request: {str(evolution.get('solution_request') or '')}\n"
            f"Proposal output: {str(evolution.get('proposal_output_path') or '')}\n"
            "Read only the listed solution request and its referenced evidence. Write the required proposal JSON at the listed output path, "
            "then run the existing CareerEng evolution apply command from that request. Do not change Python code. "
            "After a successful apply, call careereng_complete_evolution_solution with this work item ID and evolution run ID."
        )
    return (
        "Continue the same bounded CareerEng site-batch work item.\n"
        f"Work item ID: {record.work_item_id}\n"
        "The previous phase or apply target completed. First call careereng_get_work_item_context with this same ID, "
        "then continue only its current phase using the returned scoped tools and resources. Do not inspect project files or create a new browser runtime."
    )


def _worker_state_from_payload(payload_path: Path) -> str:
    payload = read_json(Path(payload_path))
    return str(payload.get("worker_state") or "").strip()


def _turn_id_from(result: dict[str, Any]) -> str:
    turn = result.get("turn") if isinstance(result.get("turn"), dict) else {}
    return str(turn.get("id") or result.get("turnId") or "")


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
        "updated_at": record.updated_at,
    }
