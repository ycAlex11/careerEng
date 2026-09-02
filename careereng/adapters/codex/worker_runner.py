"""Codex App Server translation for generic external-agent work items."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from careereng.orchestration.engine.agent_workers import (
    AgentWorkerEvent,
    AgentWorkerRecord,
    SiteAgentWorkerCoordinator,
    worker_record_from_payload,
)
from careereng.platform.sessions import SiteWorkerSessionStore

from .app_server import CodexAppServerClient, CodexAppServerEvent
from .thread_state import bind_work_order_thread, load_work_order_binding
from .transport import normalize_codex_event, normalize_codex_operation, normalize_codex_trace


# Compatibility names for existing callers. The record and lifecycle belong to
# orchestration; this module only translates the Codex App Server protocol.
CodexWorkerRecord = AgentWorkerRecord


class _CodexThreadTransport:
    """Adapt Codex App Server RPC to the generic agent-thread transport."""

    def __init__(self, server: CodexAppServerClient, *, on_transport: Callable[[dict[str, Any]], None]):
        self._server = server
        set_trace_callback = getattr(server, "set_trace_callback", None)
        if callable(set_trace_callback):
            set_trace_callback(on_transport)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._server, name)

    def start(self) -> dict[str, Any]:
        return self._server.start()

    def start_thread(self, *, cwd: Path, timeout_seconds: float | None = None) -> dict[str, Any]:
        return normalize_codex_operation(
            "thread_start",
            self._server.start_thread(cwd=cwd, timeout_seconds=timeout_seconds),
        )

    def resume_thread(self, thread_id: str) -> dict[str, Any]:
        return normalize_codex_operation(
            "thread_resume",
            self._server.resume_thread(thread_id),
            thread_id=thread_id,
        )

    def start_turn(self, *, thread_id: str, prompt: str) -> dict[str, Any]:
        return normalize_codex_operation(
            "turn_start",
            self._server.start_turn(thread_id=thread_id, prompt=prompt),
            thread_id=thread_id,
        )

    def interrupt_turn(self, *, thread_id: str, turn_id: str) -> dict[str, Any]:
        return self._server.interrupt_turn(thread_id=thread_id, turn_id=turn_id)

    def close(self) -> None:
        self._server.close()


def _normalize_codex_event(event: CodexAppServerEvent) -> AgentWorkerEvent:
    normalized = normalize_codex_event(event)
    kind = str(normalized.get("kind") or "")
    if kind == "usage":
        return AgentWorkerEvent(
            kind="usage",
            thread_id=str(normalized.get("thread_id") or ""),
            usage=dict(normalized.get("payload") or {}),
        )
    if kind == "turn_completed":
        payload = normalized.get("payload") if isinstance(normalized.get("payload"), dict) else {}
        turn = payload.get("turn") if isinstance(payload.get("turn"), dict) else {}
        return AgentWorkerEvent(
            kind="turn_completed",
            thread_id=str(normalized.get("thread_id") or ""),
            turn_id=str(normalized.get("turn_id") or turn.get("id") or ""),
            turn_status=str(turn.get("status") or "completed"),
        )
    if kind in {"thread_started", "notification"}:
        return AgentWorkerEvent(
            kind="transport",
            thread_id=str(normalized.get("thread_id") or ""),
            turn_id=str(normalized.get("turn_id") or ""),
            transport=normalized,
        )
    return AgentWorkerEvent(kind="ignored", thread_id="")


class CodexWorkerCoordinator(SiteAgentWorkerCoordinator):
    """Bind generic work-item lifecycle to the Codex App Server transport."""

    def __init__(
        self,
        *,
        project_root: Path,
        worker_limit: int,
        app_server_factory: Callable[[Callable[[CodexAppServerEvent], None]], CodexAppServerClient],
        workspace: Path | None = None,
        max_effective_batches_per_session: int = 5,
        idle_timeout_seconds: int = 180,
        max_resume_attempts: int = 2,
        interrupt_ack_timeout_seconds: int = 15,
        max_interrupt_attempts: int = 2,
        on_record: Callable[[CodexWorkerRecord], None] | None = None,
        on_usage: Callable[[CodexWorkerRecord, dict[str, Any]], None] | None = None,
        on_recovery: Callable[[CodexWorkerRecord, str], None] | None = None,
        on_transport_event: Callable[[CodexWorkerRecord | None, dict[str, Any]], None] | None = None,
        on_server_request: Callable[[str, dict[str, Any]], dict[str, Any] | None] | None = None,
    ):
        def transport_factory(on_event: Callable[[AgentWorkerEvent], None]) -> _CodexThreadTransport:
            def on_transport(payload: dict[str, Any]) -> None:
                data = normalize_codex_trace(payload)
                on_event(
                    AgentWorkerEvent(
                        kind="transport",
                        thread_id=str(data.get("thread_id") or ""),
                        turn_id=str(data.get("turn_id") or ""),
                        transport=data,
                    )
                )

            server = app_server_factory(lambda event: on_event(_normalize_codex_event(event)))
            set_server_request_handler = getattr(server, "set_server_request_handler", None)
            if callable(set_server_request_handler):
                set_server_request_handler(on_server_request)
            return _CodexThreadTransport(server, on_transport=on_transport)

        def bind_record(record: AgentWorkerRecord) -> None:
            bind_work_order_thread(
                payload_path=record.payload_path,
                phase_session_path=record.phase_session_path,
                thread_id=record.thread_id,
                turn_id=record.turn_id,
                status=record.status,
                worker_session_id=record.worker_session_id,
                session_batch_ordinal=record.session_batch_ordinal,
                session_reused=record.session_reused,
                session_rotation_reason=record.session_rotation_reason,
                last_error=record.last_error,
                recovery_attempts=record.recovery_attempts,
            )

        super().__init__(
            project_root=project_root,
            worker_limit=worker_limit,
            transport_factory=transport_factory,
            load_binding=load_work_order_binding,
            bind_record=bind_record,
            session_store=SiteWorkerSessionStore(workspace or (Path(project_root) / "workspace")),
            backend="codex_app_server",
            max_effective_batches_per_session=max_effective_batches_per_session,
            idle_timeout_seconds=idle_timeout_seconds,
            max_resume_attempts=max_resume_attempts,
            interrupt_ack_timeout_seconds=interrupt_ack_timeout_seconds,
            max_interrupt_attempts=max_interrupt_attempts,
            on_record=on_record,
            on_usage=on_usage,
            on_recovery=on_recovery,
            on_transport_event=on_transport_event,
        )


__all__ = ["CodexWorkerCoordinator", "CodexWorkerRecord", "worker_record_from_payload"]
