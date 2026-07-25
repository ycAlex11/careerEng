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


# Compatibility names for existing callers. The record and lifecycle belong to
# orchestration; this module only translates the Codex App Server protocol.
CodexWorkerRecord = AgentWorkerRecord


class _CodexThreadTransport:
    """Adapt Codex App Server RPC to the generic agent-thread transport."""

    def __init__(self, server: CodexAppServerClient):
        self._server = server

    def __getattr__(self, name: str) -> Any:
        return getattr(self._server, name)

    def start(self) -> dict[str, Any]:
        return self._server.start()

    def start_thread(self, *, cwd: Path, timeout_seconds: float | None = None) -> dict[str, Any]:
        return self._server.start_thread(cwd=cwd, timeout_seconds=timeout_seconds)

    def resume_thread(self, thread_id: str) -> dict[str, Any]:
        return self._server.resume_thread(thread_id)

    def start_turn(self, *, thread_id: str, prompt: str) -> dict[str, Any]:
        return self._server.start_turn(thread_id=thread_id, prompt=prompt)

    def interrupt_turn(self, *, thread_id: str, turn_id: str) -> dict[str, Any]:
        return self._server.interrupt_turn(thread_id=thread_id, turn_id=turn_id)

    def close(self) -> None:
        self._server.close()


def _normalize_codex_event(event: CodexAppServerEvent) -> AgentWorkerEvent:
    if event.method == "thread/tokenUsage/updated":
        return AgentWorkerEvent(
            kind="usage",
            thread_id=str(event.params.get("threadId") or ""),
            usage=dict(event.params),
        )
    if event.method == "turn/completed":
        turn = event.params.get("turn") if isinstance(event.params.get("turn"), dict) else {}
        return AgentWorkerEvent(
            kind="turn_completed",
            thread_id=str(event.params.get("threadId") or ""),
            turn_id=str(turn.get("id") or ""),
            turn_status=str(turn.get("status") or "completed"),
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
        on_record: Callable[[CodexWorkerRecord], None] | None = None,
        on_usage: Callable[[CodexWorkerRecord, dict[str, Any]], None] | None = None,
    ):
        def transport_factory(on_event: Callable[[AgentWorkerEvent], None]) -> _CodexThreadTransport:
            server = app_server_factory(lambda event: on_event(_normalize_codex_event(event)))
            return _CodexThreadTransport(server)

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
            on_record=on_record,
            on_usage=on_usage,
        )


__all__ = ["CodexWorkerCoordinator", "CodexWorkerRecord", "worker_record_from_payload"]
