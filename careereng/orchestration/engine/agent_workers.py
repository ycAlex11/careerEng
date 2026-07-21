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
    status: str = "queued"
    resume_message: str = ""
    context_revision: int = 0
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)


class SiteAgentWorkerCoordinator:
    """Coordinate one retained external-agent thread per site + batch scope."""

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
        on_record: Callable[[AgentWorkerRecord], None] | None = None,
        on_usage: Callable[[AgentWorkerRecord, dict[str, Any]], None] | None = None,
    ):
        self.project_root = Path(project_root).resolve()
        self.worker_limit = max(1, int(worker_limit or 1))
        self.transport_factory = transport_factory
        self.load_binding = load_binding
        self.bind_record = bind_record
        self.on_record = on_record
        self.on_usage = on_usage
        self._lock = threading.RLock()
        self._scheduler = SiteWorkItemScheduler(worker_limit=self.worker_limit)
        self._active: dict[str, AgentWorkerRecord] = {}
        self._paused: dict[str, AgentWorkerRecord] = {}
        self._successors: dict[str, AgentWorkerRecord] = {}
        self._by_thread: dict[str, AgentWorkerRecord] = {}
        self._server: AgentThreadTransport | None = None

    def enqueue(self, record: AgentWorkerRecord) -> AgentWorkerRecord:
        with self._lock:
            existing = self._active.get(record.site_key)
            if existing is not None:
                if existing.work_item_id != record.work_item_id:
                    record.status = "queued"
                    record.updated_at = now_iso()
                    self._successors[record.site_key] = record
                    self._persist_locked(record, bind_thread=False)
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
            server = self._ensure_server_locked()
            if record.thread_id:
                server.resume_thread(record.thread_id)
            result = server.start_turn(thread_id=record.thread_id, prompt=_resume_prompt(record, message))
            record.turn_id = _turn_id_from(result)
            record.status = "running"
            self._persist_locked(record)
            return record

    def resume_work_order(self, record: AgentWorkerRecord, *, message: str) -> AgentWorkerRecord:
        """Resume a retained work item after a host or transport restart."""

        with self._lock:
            current = self._active.get(record.site_key)
            if current is not None:
                return self.resume(site_key=record.site_key, message=message) or current
            paused = self._paused.pop(record.site_key, None)
            if paused is not None and paused.work_item_id == record.work_item_id and paused.thread_id:
                server = self._ensure_server_locked()
                server.resume_thread(paused.thread_id)
                result = server.start_turn(thread_id=paused.thread_id, prompt=_resume_prompt(paused, message))
                paused.turn_id = _turn_id_from(result)
                paused.status = "running"
                paused.updated_at = now_iso()
                self._active[paused.site_key] = paused
                self._by_thread[paused.thread_id] = paused
                self._persist_locked(paused)
                return paused

            record.thread_id = ""
            record.turn_id = ""
            record.status = "queued"
            record.resume_message = str(message or "")
            self._persist_locked(record)
            self._scheduler.enqueue(SiteWorkItem(record.site_key, record.batch_id, record))
            self._dispatch_locked()
            return self._active.get(record.site_key, record)

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
            except Exception:
                self._scheduler.complete(item.site_key)
                raise

    def _start_locked(self, record: AgentWorkerRecord) -> None:
        last_error: Exception | None = None
        for attempt in range(self.START_ATTEMPTS):
            try:
                server = self._ensure_server_locked()
                binding = self.load_binding(record.payload_path)
                thread_id = str(binding.get("thread_id") or "")
                if thread_id:
                    server.resume_thread(thread_id)
                else:
                    thread = server.start_thread(cwd=record.payload_path.parent, timeout_seconds=self.THREAD_START_TIMEOUT_SECONDS)
                    thread_id = str((thread.get("thread") or {}).get("id") or thread.get("threadId") or "")
                if not thread_id:
                    raise RuntimeError("external agent transport returned no thread id")
                record.thread_id = thread_id
                prompt = _resume_prompt(record, record.resume_message) if record.resume_message else _work_prompt(record)
                record.resume_message = ""
                result = server.start_turn(thread_id=thread_id, prompt=prompt)
                record.turn_id = _turn_id_from(result)
                record.status = "running"
                record.updated_at = now_iso()
                self._active[record.site_key] = record
                self._by_thread[thread_id] = record
                self._persist_locked(record)
                return
            except RuntimeError as exc:
                last_error = exc
                if self._server is not None:
                    self._server.close()
                    self._server = None
                if attempt + 1 < self.START_ATTEMPTS:
                    continue
        raise RuntimeError(f"external agent worker startup failed after {self.START_ATTEMPTS} attempts: {last_error}") from last_error

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
                record.turn_id = _turn_id_from(result)
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


def worker_record_from_payload(payload_path: Path) -> AgentWorkerRecord:
    payload = read_json(Path(payload_path))
    phase_session = Path(payload_path).parent / "phase_session.json"
    return AgentWorkerRecord(
        site_key=str(payload.get("site_key") or ""),
        batch_id=str(payload.get("batch_id") or ""),
        payload_path=Path(payload_path),
        phase_session_path=phase_session,
        work_item_id=work_item_id_from_payload(payload) or Path(payload_path).parent.name,
        context_revision=int(payload.get("context_revision") or 0),
    )


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


def _record_payload(record: AgentWorkerRecord) -> dict[str, Any]:
    return {
        "site_key": record.site_key,
        "batch_id": record.batch_id,
        "work_item_id": record.work_item_id,
        "thread_id": record.thread_id,
        "turn_id": record.turn_id,
        "status": record.status,
        "context_revision": record.context_revision,
        "updated_at": record.updated_at,
    }
