"""Workspace-scoped manager process for persistent agent state."""

from __future__ import annotations

import hashlib
import json
import os
import errno
import re
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from careereng.adapters.external_agents.contracts import AGENT_BRIDGE_STATUS, CODEX_APP_SERVER_MODE
from careereng.config.execution import (
    CODEX_BACKEND,
    PROVIDER_BACKEND,
    execution_backend_from_mode,
    normalize_execution_backend,
    resolve_execution_backend,
)
from careereng.platform.observability.agent_transport_trace import AgentTransportTrace
from careereng.platform.observability.execution_diagnostics import ExecutionDiagnosticStore
from careereng.platform.observability.recorder import PerformanceRecorder
from careereng.platform.sessions import SiteWorkerSessionStore
from careereng.orchestration.agent_protocol.runtime_lifecycle import RELEASE_SITE_OPERATION, release_site_payload
from careereng.adapters.external_agents.work_orders import (
    activate_browser_agent_evolution_solution,
    set_browser_agent_work_order_state,
)
from careereng.orchestration.agent_protocol.work_item_store import WorkItemStore
from careereng.orchestration.worker_control import WorkItemFence
from careereng.career.resume.batch_snapshot import validate_site_resume_snapshot
from careereng.utils import make_id, now_iso, read_json, write_json
from .errors import RuntimeHostAccessDeniedError, RuntimeHostProtocolMismatchError, RuntimeHostUnavailableError
from .protocol import RUNTIME_HOST_PROTOCOL_VERSION, protocol_version_from, runtime_host_identity, with_runtime_host_protocol


# Compatibility injection point for unit tests. Production imports the concrete
# workflow builder only when a browser-owning host is actually constructed.
build_loop: Callable[..., tuple[Any, Any]] | None = None


DEFAULT_RUNTIME_HOST_REQUEST_TIMEOUT_SECONDS = 1800.0
# Compatibility for callers that have not yet migrated their import path.
DEFAULT_MANAGER_REQUEST_TIMEOUT_SECONDS = DEFAULT_RUNTIME_HOST_REQUEST_TIMEOUT_SECONDS
_AUTONOMOUS_EXPLORATION_SUMMARY_TOOLS = frozenset(
    {
        "careereng_submit_evolution_proposal",
        "careereng_apply_evolution_solution",
        "careereng_complete_evolution_solution",
    }
)


def runtime_host_socket_path(workspace: Path) -> Path:
    digest = hashlib.sha1(str(workspace.resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"careereng-runtime-host-{digest}.sock"


def manager_socket_path(workspace: Path) -> Path:
    """Deprecated alias for the runtime-host endpoint path."""

    return runtime_host_socket_path(workspace)


class RuntimeHostService:
    """Workspace-scoped owner of generic browser/session runtime resources.

    The service delegates workflow execution to the injected loop. It does not
    make site, job, form, matching, or evolution-policy decisions itself.
    """

    def __init__(
        self,
        *,
        project_root: Path,
        workspace: Path,
        loop_factory: Callable[..., tuple[Any, Any]] | None = None,
    ):
        self.project_root = Path(project_root).resolve()
        self.workspace = Path(workspace).resolve()
        if loop_factory is None and not callable(build_loop):
            # Building a workflow imports providers and career capabilities. Keep
            # it deferred so runtime lifecycle clients remain lightweight.
            from careereng.adapters.bootstrap import build_loop as default_build_loop

            factory = default_build_loop
        elif loop_factory is None:
            factory = build_loop
        else:
            factory = loop_factory
        self.loop, self.config = factory(project_root=self.project_root, workspace=self.workspace)
        # The control lock protects host-owned registries only. Browser and
        # state work use a per-site lock so independent sites remain parallel.
        self._lock = threading.Lock()
        self._site_locks_guard = threading.Lock()
        self._site_locks: dict[str, threading.Lock] = {}
        self._batch_workers: dict[str, threading.Thread] = {}
        self._background_batch_running = False  # Compatibility-only status bit.
        self._managed_batch_seen = False
        self._idle_shutdown_callback: Callable[[], None] | None = None
        self._site_worker_sessions = SiteWorkerSessionStore(self.workspace)
        self._agent_transport_trace = AgentTransportTrace(self.workspace)
        self._main_agent_bridge = self._build_main_agent_bridge()
        self._codex_workers = self._build_codex_worker_coordinator()

    def close(self) -> None:
        if self._codex_workers is not None:
            self._codex_workers.close()
        if self._main_agent_bridge is not None:
            self._main_agent_bridge.close()
        closer = getattr(self.loop, "close", None)
        if callable(closer):
            closer()

    def _build_main_agent_bridge(self):
        """Attach Codex-only callback delivery to this host's shared event store."""

        event_store = getattr(self.loop, "agent_events", None)
        if event_store is None:
            return None
        try:
            from careereng.adapters.codex.main_agent_bridge import CodexMainAgentBridge

            bridge = CodexMainAgentBridge(project_root=self.project_root, event_store=event_store)
            bridge.attach()
            bridge.retry_pending(force=True)
            return bridge
        except Exception:
            # The durable inbox still works if local callback delivery is unavailable.
            return None

    def set_idle_shutdown_callback(self, callback: Callable[[], None]) -> None:
        """Allow the socket owner to close this host once its work is finished."""

        self._idle_shutdown_callback = callback

    def handle_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        op = str(payload.get("op") or "process_message")
        caller_version = protocol_version_from(payload)
        if caller_version and caller_version != RUNTIME_HOST_PROTOCOL_VERSION:
            return {
                "ok": False,
                "error": "runtime_host_protocol_mismatch",
                "expected_protocol_version": RUNTIME_HOST_PROTOCOL_VERSION,
                "actual_protocol_version": caller_version,
                **runtime_host_identity(),
            }
        # Every scoped agent-bridge request is objective worker progress. Keep
        # this at the protocol boundary so new browser, state, context, or
        # evolution tools cannot be omitted from the no-progress watchdog.
        if op.startswith("agent_bridge_"):
            site_key = str(payload.get("site_key") or "").strip()
            if site_key:
                self._record_codex_activity(site_key)
        if op == "ping":
            return {"ok": True, "reply": "pong", **runtime_host_identity()}
        if op == "shutdown":
            return self._handle_shutdown(payload)
        if op == "start_jobs_batch":
            return self._handle_start_jobs_batch(payload)
        if op == "fresh_snapshot_resume":
            return self._handle_fresh_snapshot_resume(payload)
        if op == "worker_command":
            return self._handle_worker_command(payload)
        if op == "pause_jobs_batch":
            return self._handle_pause_jobs_batch(payload)
        if op == "pause_site":
            return self._handle_pause_site(payload)
        if op == "stop_site":
            return self._handle_stop_site(payload)
        if op == "cancel_site":
            return self._handle_cancel_site(payload)
        if op == "cancel_jobs_batch":
            return self._handle_cancel_jobs_batch(payload)
        if op == "agent_status":
            return self._handle_agent_status(payload)
        if op == "main_agent_registration_updated":
            return self._handle_main_agent_registration_updated()
        if op == RELEASE_SITE_OPERATION:
            return self._handle_release_site(payload)
        if op in {"agent_bridge_browser_list_tools", "browser_handoff_list_tools"}:
            return self._handle_agent_bridge_browser_list_tools(payload)
        if op in {"agent_bridge_browser_call_tool", "browser_handoff_call_tool"}:
            return self._handle_agent_bridge_browser_call_tool(payload)
        if op == "agent_bridge_browser_run_sequence":
            return self._handle_agent_bridge_browser_run_sequence(payload)
        if op == "agent_bridge_state_list_tools":
            return self._handle_agent_bridge_state_list_tools(payload)
        if op == "agent_bridge_read_context_resource":
            return self._handle_agent_bridge_read_context_resource(payload)
        if op == "agent_bridge_state_call_tool":
            return self._handle_agent_bridge_state_call_tool(payload)
        if op == "agent_bridge_evolution_solution_complete":
            return self._handle_agent_bridge_evolution_solution_complete(payload)
        if op == "agent_bridge_submit_evolution_proposal":
            return self._handle_agent_bridge_submit_evolution_proposal(payload)
        if op == "agent_bridge_apply_evolution_solution":
            return self._handle_agent_bridge_apply_evolution_solution(payload)
        if op != "process_message":
            return {"ok": False, "error": f"unsupported op: {op}"}
        session_id = str(payload.get("session_id") or "cli:default")
        message = str(payload.get("message") or "")
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            return {"ok": False, "error": "runtime host control plane is busy"}
        try:
            reply = self.loop.process_message(session_id, message)
        finally:
            self._lock.release()
        return {"ok": True, "reply": reply}

    def _handle_shutdown(self, payload: dict[str, Any]) -> dict[str, Any]:
        cancel_open_batches = bool(payload.get("cancel_open_batches"))
        session_id = str(payload.get("session_id") or "").strip() or None
        cancelled: list[dict[str, Any]] = []
        if cancel_open_batches:
            job_flow = getattr(self.loop, "job_flow", None)
            job_store = getattr(job_flow, "job_store", None)
            clear_open_batches = getattr(job_store, "clear_open_batches", None)
            if callable(clear_open_batches):
                cancelled = list(clear_open_batches(session_id=session_id, status="cancelled") or [])
            if self._codex_workers is not None:
                for worker in self._codex_workers.snapshot().get("active", []):
                    if isinstance(worker, dict) and str(worker.get("site_key") or ""):
                        self._codex_workers.cancel(site_key=str(worker["site_key"]))
        else:
            acquired = self._lock.acquire(blocking=False)
            if not acquired:
                return {"ok": False, "error": "workspace manager is busy with another job batch"}
            self._lock.release()
        return {
            "ok": True,
            "shutdown": True,
            "cancelled": len(cancelled),
            "reply": "workspace manager shutting down",
        }

    def _handle_agent_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return live site execution facts without interpreting site behavior."""

        requested_site = str(payload.get("site_key") or "").strip()
        job_flow = getattr(self.loop, "job_flow", None)
        site_store = getattr(getattr(job_flow, "site_tools", None), "site_store", None)
        if site_store is None:
            return {"ok": True, "sites": []}
        worker_rows: dict[str, dict[str, Any]] = {}
        if self._codex_workers is not None:
            snapshot = self._codex_workers.snapshot()
            for bucket in ("active", "paused", "queued"):
                for row in snapshot.get(bucket, []):
                    if isinstance(row, dict) and str(row.get("site_key") or ""):
                        worker_rows[str(row["site_key"])] = {**row, "scheduler_state": bucket}
        sites: list[dict[str, Any]] = []
        for site in site_store.list_sites():
            site_key = str(site.get("site_key") or site.get("site_id") or "").strip()
            if not site_key or (requested_site and site_key != requested_site):
                continue
            browser = site_store.load_browser_session(site_key)
            worker = worker_rows.get(site_key, {})
            browser_status = str(browser.get("browser_status") or "")
            # A current scheduler record owns this site's live status. The
            # browser session is historical fallback only when no current
            # worker exists for the site.
            worker_status = str(worker.get("status") or "") if worker else str(browser.get("codex_worker_status") or "")
            pending_action = str(browser.get("pending_action") or "")
            if not worker and worker_status in {"completed", "released", "cancelled", "unavailable"}:
                worker_status = ""
            if not worker_status and not pending_action and browser_status not in {"running", "waiting_user", "paused"}:
                continue
            sites.append(
                {
                    "site_key": site_key,
                    "site_name": str(site.get("canonical_company") or site.get("raw_name") or ""),
                    "phase": str(browser.get("agent_bridge_current_phase") or browser.get("resume_phase") or ""),
                    "worker_status": worker_status,
                    "scheduler_state": str(worker.get("scheduler_state") or ""),
                    "thread_id": str(worker.get("thread_id") or browser.get("codex_thread_id") or ""),
                    "turn_id": str(worker.get("turn_id") or browser.get("codex_turn_id") or ""),
                    "batch_id": str(worker.get("batch_id") or ""),
                    "work_item_id": str(worker.get("work_item_id") or ""),
                    "browser_status": browser_status,
                    "pending_action": pending_action,
                    "current_url": str(browser.get("last_known_url") or ""),
                    "last_activity_at": str(worker.get("updated_at") or browser.get("updated_at") or ""),
                    "last_error": str(worker.get("last_error") or browser.get("codex_worker_last_error") or ""),
                }
            )
        return {"ok": True, "sites": sites, **runtime_host_identity()}

    def _handle_main_agent_registration_updated(self) -> dict[str, Any]:
        """Retry durable attention delivery after the Desktop main target changes."""

        bridge = self._main_agent_bridge
        if bridge is None:
            return {"ok": True, "retried": 0, "bridge": "unavailable"}
        return {"ok": True, "retried": bridge.retry_pending(force=True)}

    def _handle_start_jobs_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = str(payload.get("session_id") or "cli:default")
        message = str(payload.get("message") or "")
        operation = str(payload.get("operation") or "job_search")
        apply_requested = bool(payload.get("apply_requested"))
        turn_id = make_id("turn")
        execution_backend, backend_error = self._resolve_execution_backend(
            requested_backend=payload.get("backend")
        )
        if backend_error:
            return {"ok": False, "accepted": False, "error": backend_error}
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            return {"ok": False, "error": "runtime host control plane is busy"}
        try:
            batch = self.loop.job_flow.create_batch(
                session_id=session_id,
                turn_id=turn_id,
                user_message=message,
                apply_requested=apply_requested,
                operation=operation,
                execution_backend=execution_backend,
                separate_batch=bool(payload.get("separate_batch")),
            )
            if not batch:
                return {"ok": True, "accepted": False, "reply": "当前没有已注册的 active sites。请先完成公司注册。"}
            batch_id = str(batch.get("batch_id") or "")
            self._managed_batch_seen = True
            reused_batch = bool(batch.get("_runtime_reused_batch"))
            launch_site_keys = [str(site_key) for site_key in batch.get("_runtime_site_keys") or [] if str(site_key)]
            if execution_backend == PROVIDER_BACKEND:
                self._bind_provider_site_sessions(batch, site_keys=launch_site_keys or None)
            self._background_batch_running = bool(self._batch_workers)
        finally:
            self._lock.release()

        def _worker() -> None:
            try:
                # A normal site run only creates/persists the phase work item.
                # Evolution is a later, explicit review of terminal evidence.
                if not reused_batch:
                    self.loop.job_flow.run_batch(batch_id)
                elif launch_site_keys:
                    self.loop.job_flow.run_batch(batch_id, site_keys=launch_site_keys)
                self._maybe_create_site_run_summary(batch_id)
                if not reused_batch:
                    self._enqueue_codex_workers_for_batch(batch_id, site_keys=launch_site_keys or None)
                elif launch_site_keys:
                    self._enqueue_codex_workers_for_batch(batch_id, site_keys=launch_site_keys)
            except BaseException as exc:  # pragma: no cover - defensive manager boundary
                fail_batch = getattr(self.loop.job_flow, "fail_batch", None)
                if callable(fail_batch):
                    fail_batch(batch_id=batch_id, error=str(exc))
                    self._maybe_create_site_run_summary(batch_id)
            finally:
                with self._lock:
                    self._batch_workers.pop(launch_id, None)
                    self._background_batch_running = bool(self._batch_workers)
                self._shutdown_if_idle()

        launch_id = make_id("host_launch")
        worker = threading.Thread(target=_worker, name=f"careereng-jobs-{batch_id}-{launch_id}", daemon=True)
        with self._lock:
            self._batch_workers[launch_id] = worker
            self._background_batch_running = True
        worker.start()
        return {
            "ok": True,
            "accepted": True,
            "batch_id": batch_id,
            "turn_id": turn_id,
            "operation": operation,
            "execution_backend": execution_backend,
            "reused_batch": reused_batch,
            "appended_site_keys": launch_site_keys,
            "reply": f"batch={batch_id} status=running",
        }

    def _handle_fresh_snapshot_resume(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = str(payload.get("session_id") or "cli:default")
        message = str(payload.get("message") or "")
        turn_id = str(payload.get("turn_id") or make_id("turn"))
        command_id = str(payload.get("command_id") or turn_id).strip()
        site_key = str(payload.get("site_key") or "").strip()
        requested_site_key = site_key
        source_batch_id = str(payload.get("source_batch_id") or "").strip()
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            return {"ok": False, "error": "runtime host control plane is busy"}
        try:
            if source_batch_id:
                source = self.loop.job_flow.job_store.load_batch(source_batch_id)
                if not source:
                    return {"ok": False, "error": f"source job batch not found: {source_batch_id}"}
                if str(source.get("status") or "") not in {"completed", "partial_completed", "failed", "cancelled"}:
                    session_id = str(source.get("session_id") or session_id)
                else:
                    recovered = self.loop.job_flow.create_checkpoint_recovery_batch(
                        source_batch_id=source_batch_id,
                        site_key=site_key,
                        session_id=session_id,
                        turn_id=turn_id,
                        user_message=message,
                        command_id=command_id,
                    )
                    session_id = str(recovered.get("session_id") or session_id)
            resolve_resume_site = getattr(self.loop.job_flow, "resolve_resume_site_key", None)
            if callable(resolve_resume_site):
                site_key = str(
                    resolve_resume_site(session_id=session_id, message=message, site_key=site_key) or ""
                ).strip()
            if not site_key and requested_site_key and not source_batch_id:
                find_source = getattr(self.loop.job_flow, "latest_checkpoint_recovery_source", None)
                source = (
                    find_source(session_id=session_id, site_key=requested_site_key)
                    if callable(find_source)
                    else {}
                )
                if isinstance(source, dict) and str(source.get("batch_id") or ""):
                    recovered = self.loop.job_flow.create_checkpoint_recovery_batch(
                        source_batch_id=str(source.get("batch_id") or ""),
                        site_key=requested_site_key,
                        session_id=session_id,
                        turn_id=turn_id,
                        user_message=message,
                        command_id=command_id,
                    )
                    site_key = requested_site_key
                    session_id = str(recovered.get("session_id") or session_id)
            if not site_key:
                return {"ok": True, "accepted": False, "reply": ""}
            backend_error = self._resume_backend_error(session_id=session_id, site_key=site_key)
            if backend_error:
                return {"ok": False, "error": backend_error}
            self._prepare_recovery_runtime(site_key)
            reply = self.loop.job_flow.handle_resume_message(
                session_id=session_id,
                message=message,
                turn_id=turn_id,
                site_key=site_key,
            )
        except Exception as exc:
            # A failed browser/runtime resume must be reported to the caller;
            # closing the socket makes the work item look silently abandoned.
            return {"ok": False, "error": f"fresh_snapshot_resume_failed: {exc}"}
        finally:
            self._lock.release()
        if reply is not None and self._codex_workers is not None and site_key:
            try:
                record = self._resume_codex_site(site_key=site_key, message=message, command_id=command_id)
            except Exception as exc:
                return {"ok": False, "error": f"codex_worker_resume_failed: {exc}"}
            if record is not None:
                return {
                    "ok": True,
                    "accepted": True,
                    "reply": f"site={site_key} status=running",
                    "codex_thread_id": record.thread_id,
                    "turn_id": turn_id,
                }
        if reply is None:
            return {"ok": True, "accepted": False, "reply": ""}
        return {"ok": True, "accepted": True, "reply": reply, "turn_id": turn_id}

    def _prepare_recovery_runtime(self, site_key: str) -> None:
        """Rebuild a failed browser runtime without releasing durable work."""

        if not site_key:
            return
        job_flow = getattr(self.loop, "job_flow", None)
        site_store = getattr(getattr(job_flow, "site_tools", None), "site_store", None)
        browser_runner = getattr(self.loop, "browser_runner", None)
        if site_store is None or browser_runner is None:
            return
        session = site_store.load_browser_session(site_key)
        if str(session.get("pending_action") or "") != "execution_recovery_exhausted":
            return
        prepare_runtime = getattr(browser_runner, "prepare_site_runtime_for_recovery", None)
        if not callable(prepare_runtime):
            raise RuntimeError("browser recovery preparation is unavailable")
        prepare_runtime(site_key)

    def _handle_worker_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Queue or redirect one running worker without refreshing browser context."""

        site_key = str(payload.get("site_key") or "").strip()
        message = str(payload.get("message") or "").strip()
        kind = str(payload.get("kind") or "guidance").strip().lower()
        command_id = str(payload.get("command_id") or "").strip()
        if not site_key or not message:
            return {"ok": False, "error": "site_key and message are required"}
        if kind not in {"guidance", "redirect"}:
            return {"ok": False, "error": f"unsupported running worker command: {kind}"}
        if self._codex_workers is None:
            return {"ok": False, "error": "worker command transport is unavailable"}
        try:
            record = self._codex_workers.command(
                site_key=site_key,
                kind=kind,
                message=message,
                command_id=command_id,
            )
        except Exception as exc:
            return {"ok": False, "error": f"worker_command_failed: {exc}"}
        if record is None:
            return {"ok": False, "accepted": False, "error": f"no active worker for site={site_key}"}
        return {
            "ok": True,
            "accepted": True,
            "site_key": site_key,
            "batch_id": record.batch_id,
            "work_item_id": record.work_item_id,
            "thread_id": record.thread_id,
            "turn_id": record.turn_id,
            "worker_status": record.status,
        }

    def _resolve_execution_backend(self, *, requested_backend: object = "") -> tuple[str, str]:
        """Validate one configured transport without fallback or switching."""

        browser_runner = getattr(self.loop, "browser_runner", None)
        runtime_mode = str(getattr(browser_runner, "execution_mode", "") or "")
        backend, error = resolve_execution_backend(
            self.config,
            requested_backend=requested_backend,
            runtime_execution_mode=runtime_mode,
        )
        if error:
            return "", error
        runtime_backend = str(getattr(browser_runner, "execution_backend", "") or "")
        runtime_backend = runtime_backend or execution_backend_from_mode(runtime_mode)
        if backend != runtime_backend:
            return "", (
                f"runtime backend mismatch: selected={backend} runtime={runtime_backend}; "
                "restart CareerEng with the selected backend"
            )
        return backend, ""

    def _resume_backend_error(self, *, session_id: str, site_key: str) -> str:
        """Refuse a resume that would switch the persisted batch transport."""

        job_flow = getattr(self.loop, "job_flow", None)
        job_store = getattr(job_flow, "job_store", None)
        if job_store is None:
            return ""
        batch = job_store.latest_open_batch(session_id)
        if not isinstance(batch, dict):
            return ""
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        if site_key and site_key not in sites:
            return ""
        backend, error = self._resolve_execution_backend()
        if error:
            return error
        batch_backend = str(batch.get("execution_backend") or "").strip()
        if not batch_backend:
            batch_backend = self._migrate_legacy_batch_backend(
                batch=batch,
                site_key=site_key,
                job_store=job_store,
            )
        if not batch_backend:
            return "legacy batch has no persisted execution backend evidence; refusing to guess a transport"
        if batch_backend != backend:
            return (
                f"resume backend mismatch: batch={batch_backend} runtime={backend}; "
                "restart CareerEng with the batch backend instead of switching transports"
            )
        return ""

    def _migrate_legacy_batch_backend(self, *, batch: dict[str, Any], site_key: str, job_store: Any) -> str:
        """Backfill a missing backend only from the durable work-item contract."""

        job_flow = getattr(self.loop, "job_flow", None)
        site_store = getattr(getattr(job_flow, "site_tools", None), "site_store", None)
        if site_store is None or not site_key:
            return ""
        try:
            browser_session = site_store.load_browser_session(site_key)
        except Exception:
            return ""
        payload_path = Path(str(browser_session.get("agent_bridge_payload_path") or ""))
        payload = read_json(payload_path) if payload_path.is_file() else {}
        if str(payload.get("batch_id") or "") != str(batch.get("batch_id") or ""):
            return ""
        inferred = normalize_execution_backend(payload.get("execution_backend"))
        if not inferred:
            inferred = execution_backend_from_mode(payload.get("execution_mode"))
        if not inferred:
            return ""
        migrated = {**batch, "execution_backend": inferred}
        job_store.save_batch(migrated)
        job_store.append_event(
            "batch.execution_backend.migrated",
            {
                "batch_id": str(batch.get("batch_id") or ""),
                "site_key": site_key,
                "execution_backend": inferred,
                "evidence": "persisted_work_item_execution_mode",
            },
        )
        return inferred

    def _handle_pause_jobs_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        batch_id = str(payload.get("batch_id") or "").strip()
        site_key = str(payload.get("site_key") or "").strip()
        if not batch_id:
            return {"ok": False, "error": "batch_id is required"}
        try:
            job_flow = getattr(self.loop, "job_flow", None)
            job_store = getattr(job_flow, "job_store", None)
            current = job_store.load_batch(batch_id) if job_store is not None else {}
            current_sites = current.get("sites") if isinstance(current.get("sites"), dict) else {}
            revoked: list[dict[str, Any]] = []
            if site_key:
                def _pause_site() -> dict[str, Any]:
                    revoked.extend(
                        WorkItemStore(self.workspace).revoke_scope(
                            site_key=site_key,
                            batch_id=batch_id,
                            state="pausing",
                            event="pause_requested",
                        )
                    )
                    return self.loop.job_flow.pause_batch(batch_id=batch_id, site_key=site_key)

                batch = self._run_site_operation(site_key, _pause_site)
            else:
                for target in current_sites:
                    revoked.extend(
                        WorkItemStore(self.workspace).revoke_scope(
                            site_key=str(target),
                            batch_id=batch_id,
                            state="pausing",
                            event="pause_requested",
                        )
                    )
                batch = self.loop.job_flow.pause_batch(batch_id=batch_id, site_key="")
            if self._codex_workers is not None:
                sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
                targets = [site_key] if site_key else list(sites.keys())
                for target in targets:
                    self._codex_workers.pause(site_key=str(target))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "accepted": True, "batch": batch, "revoked_work_items": len(revoked)}

    def _handle_pause_site(self, payload: dict[str, Any]) -> dict[str, Any]:
        site_key = str(payload.get("site_key") or "").strip()
        batch_id = str(payload.get("batch_id") or "").strip()
        if not site_key or not batch_id:
            return {"ok": False, "error": "batch_id and site_key are required"}
        return self._handle_pause_jobs_batch({"batch_id": batch_id, "site_key": site_key})

    def _handle_stop_site(self, payload: dict[str, Any]) -> dict[str, Any]:
        site_key = str(payload.get("site_key") or "").strip()
        batch_id = str(payload.get("batch_id") or "").strip()
        if not site_key or not batch_id:
            return {"ok": False, "error": "batch_id and site_key are required"}
        paused = self._handle_pause_site({"batch_id": batch_id, "site_key": site_key})
        if not paused.get("ok"):
            return paused
        try:
            released = self._release_site_runtime(site_key=site_key, dispatch=True)
            WorkItemStore(self.workspace).release_scope(site_key=site_key, batch_id=batch_id, event="site_stopped")
        except Exception as exc:
            return {"ok": False, "error": str(exc), "batch": paused.get("batch")}
        return {"ok": True, "accepted": True, "released": released, "batch": paused.get("batch")}

    def _handle_cancel_site(self, payload: dict[str, Any]) -> dict[str, Any]:
        site_key = str(payload.get("site_key") or "").strip()
        batch_id = str(payload.get("batch_id") or "").strip()
        reason = str(payload.get("reason") or "user_requested_cancel")
        if not site_key or not batch_id:
            return {"ok": False, "error": "batch_id and site_key are required"}
        try:
            cancel_site = getattr(getattr(self.loop, "job_flow", None), "cancel_site", None)
            if not callable(cancel_site):
                raise RuntimeError("site cancellation is unavailable")

            def _cancel_site() -> dict[str, Any]:
                WorkItemStore(self.workspace).revoke_scope(
                    site_key=site_key,
                    batch_id=batch_id,
                    state="cancelling",
                    event="site_cancel_requested",
                )
                return cancel_site(batch_id=batch_id, site_key=site_key, reason=reason)

            batch = self._run_site_operation(site_key, _cancel_site)
            if self._codex_workers is not None:
                self._codex_workers.cancel(site_key=site_key)
            released = self._release_site_runtime(site_key=site_key, dispatch=True)
            WorkItemStore(self.workspace).release_scope(site_key=site_key, batch_id=batch_id, event="site_cancelled")
            self._record_site_worker_batch_outcome(site_key=site_key, batch_id=batch_id)
            self._retract_effective_site_run(site_key=site_key, batch_id=batch_id)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "accepted": True, "released": released, "batch": batch}

    def _handle_cancel_jobs_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        batch_id = str(payload.get("batch_id") or "").strip()
        if not batch_id:
            return {"ok": False, "error": "batch_id is required"}
        reason = str(payload.get("reason") or "user_requested_cancel")
        try:
            job_flow = getattr(self.loop, "job_flow", None)
            cancel_batch = getattr(job_flow, "cancel_batch", None)
            if not callable(cancel_batch):
                raise RuntimeError("batch cancellation is unavailable")
            job_store = getattr(job_flow, "job_store", None)
            current = job_store.load_batch(batch_id) if job_store is not None else {}
            current_sites = current.get("sites") if isinstance(current.get("sites"), dict) else {}
            for target in current_sites:
                WorkItemStore(self.workspace).revoke_scope(
                    site_key=str(target),
                    batch_id=batch_id,
                    state="cancelling",
                    event="batch_cancel_requested",
                )
            batch = cancel_batch(batch_id=batch_id, reason=reason)
            self._managed_batch_seen = True
            sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
            # Cancel the whole batch before dispatching another queued site.
            # A per-site release would otherwise free one slot and immediately
            # launch the next queued item from the batch being cancelled.
            if self._codex_workers is not None:
                for site_key in sites:
                    self._codex_workers.cancel(site_key=str(site_key))
            for site_key in sites:
                self._record_site_worker_batch_outcome(site_key=str(site_key), batch_id=batch_id)
                if self._codex_workers is not None:
                    self._codex_workers.release(site_key=str(site_key), dispatch=False)
                WorkItemStore(self.workspace).release_scope(
                    site_key=str(site_key), batch_id=batch_id, event="batch_cancelled"
                )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        self._shutdown_if_idle()
        return {"ok": True, "accepted": True, "batch": batch}

    def _handle_release_site(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Release one retained site runtime without interpreting workflow state."""

        try:
            request = release_site_payload(site_key=str(payload.get("site_key") or ""))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        try:
            self._run_site_operation(
                request["site_key"],
                lambda: WorkItemStore(self.workspace).revoke_scope(
                    site_key=request["site_key"],
                    state="stopping",
                    event="runtime_release_requested",
                ),
            )
            released = self._release_site_runtime(site_key=request["site_key"], dispatch=True)
            WorkItemStore(self.workspace).release_scope(site_key=request["site_key"])
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "released": bool(released), **request}

    def _release_site_runtime(self, *, site_key: str, dispatch: bool) -> bool:
        def _release() -> bool:
            browser_runner = getattr(self.loop, "browser_runner", None)
            finish_site = getattr(browser_runner, "finish_site", None)
            if not callable(finish_site):
                raise RuntimeError("site runtime release is unavailable")
            outcome = finish_site(site_key)
            return True if outcome is None else bool(outcome)

        released = self._run_site_operation(site_key, _release)
        if self._codex_workers is not None:
            self._codex_workers.release(site_key=site_key, dispatch=dispatch)
        return bool(released)

    def _shutdown_if_idle(self) -> None:
        """Close only after every workspace batch has reached a terminal state."""

        if not self._managed_batch_seen or self._batch_workers:
            return
        if self._codex_workers is not None:
            worker_state = self._codex_workers.snapshot()
            if worker_state.get("active") or worker_state.get("queued"):
                return
        job_flow = getattr(self.loop, "job_flow", None)
        job_store = getattr(job_flow, "job_store", None)
        list_batches = getattr(job_store, "list_batches", None)
        if not callable(list_batches):
            return
        try:
            unfinished = list(list_batches(include_terminal=False) or [])
        except Exception:
            return
        if unfinished:
            return
        callback = self._idle_shutdown_callback
        if callback is not None:
            self._idle_shutdown_callback = None
            callback()

    def _handle_agent_bridge_browser_list_tools(self, payload: dict[str, Any]) -> dict[str, Any]:
        site_key = str(payload.get("site_key") or "").strip()
        try:
            self._validate_work_item_fence(payload)
            def _list() -> list[dict[str, Any]]:
                browser_runner = getattr(self.loop, "browser_runner", None)
                list_tools = getattr(browser_runner, "list_active_browser_tools", None)
                if not callable(list_tools):
                    raise RuntimeError("agent bridge browser tool listing is unavailable")
                return list_tools(site_key)

            tools = self._run_site_operation(site_key, _list)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "site_key": site_key, "tools": tools}

    def _handle_agent_bridge_browser_call_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        site_key = str(payload.get("site_key") or "").strip()
        tool_name = str(payload.get("tool_name") or "").strip()
        arguments = payload.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        turn_id = str(payload.get("turn_id") or "").strip()
        phase = str(payload.get("phase") or AGENT_BRIDGE_STATUS).strip() or AGENT_BRIDGE_STATUS
        try:
            work_item_record = self._validate_work_item_fence(payload)
            self._validate_work_item_resume_upload(
                work_item_record,
                tool_name=tool_name,
                arguments=arguments,
            )
            def _call() -> dict[str, Any]:
                browser_runner = getattr(self.loop, "browser_runner", None)
                call_tool = getattr(browser_runner, "call_active_browser_tool", None)
                if not callable(call_tool):
                    raise RuntimeError("agent bridge browser tool call is unavailable")
                return call_tool(site_key=site_key, tool_name=tool_name, arguments=arguments, turn_id=turn_id, phase=phase)

            result = self._run_site_operation(site_key, _call)
        except Exception as exc:
            self._record_codex_activity(site_key)
            return {"ok": False, "error": str(exc)}
        self._record_codex_activity(site_key)
        return {"ok": True, "site_key": site_key, "result": result}

    def _handle_agent_bridge_browser_run_sequence(self, payload: dict[str, Any]) -> dict[str, Any]:
        site_key = str(payload.get("site_key") or "").strip()
        steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
        turn_id = str(payload.get("turn_id") or "").strip()
        phase = str(payload.get("phase") or AGENT_BRIDGE_STATUS).strip() or AGENT_BRIDGE_STATUS
        try:
            work_item_record = self._validate_work_item_fence(payload)
            for step in steps:
                if not isinstance(step, dict):
                    continue
                self._validate_work_item_resume_upload(
                    work_item_record,
                    tool_name=str(step.get("tool_name") or ""),
                    arguments=step.get("arguments") if isinstance(step.get("arguments"), dict) else {},
                )
            def _run() -> dict[str, Any]:
                browser_runner = getattr(self.loop, "browser_runner", None)
                run_sequence = getattr(browser_runner, "run_active_browser_sequence", None)
                if not callable(run_sequence):
                    raise RuntimeError("agent bridge browser sequence is unavailable")
                return run_sequence(site_key=site_key, steps=steps, turn_id=turn_id, phase=phase)

            result = self._run_site_operation(site_key, _run)
        except Exception as exc:
            self._record_codex_activity(site_key)
            return {"ok": False, "error": str(exc)}
        self._record_codex_activity(site_key)
        return {"ok": True, "site_key": site_key, "result": result}

    def _handle_agent_bridge_state_list_tools(self, payload: dict[str, Any]) -> dict[str, Any]:
        site_key = str(payload.get("site_key") or "").strip()
        phase = str(payload.get("phase") or "").strip()
        try:
            self._validate_work_item_fence(payload)
            def _list() -> list[dict[str, Any]]:
                browser_runner = getattr(self.loop, "browser_runner", None)
                list_tools = getattr(browser_runner, "list_active_state_tools", None)
                if not callable(list_tools):
                    raise RuntimeError("agent bridge state tool listing is unavailable")
                return list_tools(site_key, phase=phase)

            tools = self._run_site_operation(site_key, _list)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "site_key": site_key, "phase": phase, "tools": tools}

    def _handle_agent_bridge_state_call_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        site_key = str(payload.get("site_key") or "").strip()
        tool_name = str(payload.get("tool_name") or "").strip()
        arguments = payload.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        turn_id = str(payload.get("turn_id") or "").strip()
        phase = str(payload.get("phase") or "").strip()
        terminal_batch_id = ""
        phase_result_status = ""
        phase_result_batch_id = ""
        phase_sequence_consumed = False
        try:
            work_item_record = self._validate_work_item_fence(payload)
            if (
                tool_name == "phase_result"
                and phase == "apply"
                and not work_item_record.get("legacy_unversioned")
                and not str(payload.get("apply_target_job_id") or "").strip()
            ):
                raise ValueError("apply phase result requires the active apply target fence")
            def _call() -> dict[str, Any]:
                browser_runner = getattr(self.loop, "browser_runner", None)
                call_tool = getattr(browser_runner, "call_active_state_tool", None)
                if not callable(call_tool):
                    raise RuntimeError("agent bridge state tool call is unavailable")
                return call_tool(site_key=site_key, tool_name=tool_name, arguments=arguments, turn_id=turn_id, phase=phase)

            result = self._run_site_operation(site_key, _call)
            progression = result.get("progression") if isinstance(result, dict) else {}
            if tool_name == "phase_result" and isinstance(progression, dict):
                tool_payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
                structured = tool_payload.get("structuredContent") if isinstance(tool_payload.get("structuredContent"), dict) else {}
                phase_status = str(structured.get("status") or "").strip()
                phase_summary = str(structured.get("summary") or "").strip()
                active_context = progression.get("active_phase_context") if isinstance(progression.get("active_phase_context"), dict) else {}
                progression_completion = progression.get("completion") if isinstance(progression.get("completion"), dict) else {}
                batch_id = str(active_context.get("batch_id") or progression_completion.get("batch_id") or "").strip()
                terminal_batch_id = batch_id
                phase_result_status = phase_status
                phase_result_batch_id = batch_id
                record_progress = getattr(getattr(self.loop, "job_flow", None), "record_external_phase_progress", None)
                if batch_id and phase_status and callable(record_progress):
                    result = {
                        **result,
                        "batch_phase_progress": record_progress(
                            site_key=site_key,
                            batch_id=batch_id,
                            phase=phase,
                            result_status=phase_status,
                            next_phase=str(progression.get("next_phase") or ""),
                            summary=phase_summary,
                        ),
                    }
            completion = progression.get("completion") if isinstance(progression, dict) else {}
            if (
                isinstance(completion, dict)
                and str(completion.get("kind") or "") == "phase_sequence_completion"
                and str(completion.get("batch_id") or "").strip()
            ):
                continue_sequence = getattr(getattr(self.loop, "job_flow", None), "continue_external_phase_sequence", None)
                if callable(continue_sequence):
                    continuation_result = continue_sequence(
                        site_key=str(completion.get("site_key") or site_key),
                        batch_id=str(completion.get("batch_id") or ""),
                        terminal_phase=str(completion.get("terminal_phase") or ""),
                        session_id=str(completion.get("session_id") or ""),
                        turn_id=str(completion.get("turn_id") or turn_id),
                    )
                    self._reject_unhandled_phase_continuation(
                        site_key=site_key,
                        work_item_record=work_item_record,
                        continuation_result=continuation_result,
                    )
                    result = {
                        **result,
                        "workflow_continuation": continuation_result,
                    }
                    terminal_batch_id = str(completion.get("batch_id") or "")
                    phase_sequence_consumed = True
                    continued_site = (
                        continuation_result.get("site")
                        if isinstance(continuation_result, dict) and isinstance(continuation_result.get("site"), dict)
                        else {}
                    )
                    continued_phase = str(continued_site.get("current_phase") or "")
                    terminal_phase = str(completion.get("terminal_phase") or "")
                    if continued_phase and continued_phase != terminal_phase:
                        self._publish_agent_event(
                            kind="site.phase_advanced",
                            attention="notification",
                            summary=f"{site_key} advanced from {terminal_phase} to {continued_phase}.",
                            site_key=site_key,
                            batch_id=terminal_batch_id,
                            thread_id="",
                            turn_id=str(completion.get("turn_id") or turn_id),
                            phase=continued_phase,
                            current_url=str(continued_site.get("current_url") or ""),
                            details={"previous_phase": terminal_phase, "next_phase": continued_phase},
                            dedupe_key=f"site_phase:{terminal_batch_id}:{site_key}:{terminal_phase}:{continued_phase}",
                        )
            # An apply work order has exactly one declared phase. Its terminal
            # result must consume the active target even if an older bridge
            # response omitted the completion payload.
            if (
                tool_name == "phase_result"
                and phase_result_status.lower() == "done"
                and str(phase or "").strip() == "apply"
                and str(progression.get("action") or "") == "complete_sequence"
                and phase_result_batch_id
                and not phase_sequence_consumed
            ):
                continue_sequence = getattr(getattr(self.loop, "job_flow", None), "continue_external_phase_sequence", None)
                if callable(continue_sequence):
                    continuation_result = continue_sequence(
                        site_key=site_key,
                        batch_id=phase_result_batch_id,
                        terminal_phase="apply",
                        session_id="",
                        turn_id=turn_id,
                    )
                    self._reject_unhandled_phase_continuation(
                        site_key=site_key,
                        work_item_record=work_item_record,
                        continuation_result=continuation_result,
                    )
                    result = {
                        **result,
                        "workflow_continuation": continuation_result,
                    }
                    terminal_batch_id = phase_result_batch_id
            if terminal_batch_id:
                # Any external phase can make a site terminal. Persist the
                # phase continuation first. Ready-site batches complete one
                # effective run here; exploration attempts are counted only
                # when their whole bounded cycle is closed after synthesis.
                self._record_site_worker_batch_outcome(site_key=site_key, batch_id=terminal_batch_id)
                self._record_terminal_ready_site_run(site_key=site_key, batch_id=terminal_batch_id)
                summary_created = self._maybe_create_site_run_summary(terminal_batch_id, site_key=site_key)
                if summary_created:
                    self._activate_codex_evolution_solution(site_key=site_key, batch_id=terminal_batch_id)
                    job_flow = getattr(self.loop, "job_flow", None)
                    job_store = getattr(job_flow, "job_store", None)
                    if job_store is not None:
                        terminal_batch = job_store.load_batch(terminal_batch_id)
                        terminal_site = (terminal_batch.get("sites") or {}).get(site_key)
                        release = getattr(job_flow, "_release_site_if_non_resumable", None)
                        if callable(release) and isinstance(terminal_site, dict):
                            release(batch_id=terminal_batch_id, site_key=site_key, site=terminal_site)
                    if self._codex_workers is not None:
                        self._codex_workers.release(site_key=site_key)
                else:
                    current_flow = getattr(self.loop, "job_flow", None)
                    current_store = getattr(current_flow, "job_store", None)
                    terminal_batch = current_store.load_batch(terminal_batch_id) if current_store is not None else {}
                    if self._site_uses_exploration(terminal_batch, site_key=site_key):
                        self._record_effective_site_run(site_key=site_key, batch_id=terminal_batch_id)
            if isinstance(progression, dict) and str(progression.get("action") or "") in {
                "advance_phase",
                "complete_sequence",
            }:
                # The same site-batch work item is refreshed in place. Its
                # Codex worker observes the revision and continues on the
                # existing thread; the host must not queue a phase successor.
                result = {**result, "continue_same_work_item": True}
        except Exception as exc:
            self._record_codex_activity(site_key)
            return {"ok": False, "error": str(exc)}
        self._record_codex_activity(site_key)
        return {"ok": True, "site_key": site_key, "result": result}

    def _reject_unhandled_phase_continuation(
        self,
        *,
        site_key: str,
        work_item_record: dict[str, Any],
        continuation_result: Any,
    ) -> None:
        if not isinstance(continuation_result, dict) or continuation_result.get("handled") is not False:
            return
        self._restore_active_work_item(site_key=site_key, work_item_record=work_item_record)
        reason = str(continuation_result.get("reason") or "phase_continuation_rejected").strip()
        raise ValueError(f"phase completion was rejected: {reason}")

    def _restore_active_work_item(self, *, site_key: str, work_item_record: dict[str, Any]) -> None:
        payload_path = Path(str(work_item_record.get("payload_path") or ""))
        if not payload_path.is_file():
            return
        payload = read_json(payload_path)
        if str(payload.get("work_order_id") or "") != str(work_item_record.get("work_item_id") or ""):
            return
        job_flow = getattr(self.loop, "job_flow", None)
        site_store = getattr(getattr(job_flow, "site_tools", None), "site_store", None)
        browser_session = site_store.load_browser_session(site_key) if site_store is not None else {}
        phase_session_path = Path(str(browser_session.get("phase_session_path") or ""))
        if not phase_session_path.is_file():
            phase_session_path = payload_path.parent / "phase_session.json"
        if not phase_session_path.is_file():
            return
        set_browser_agent_work_order_state(
            workspace=self.workspace,
            payload_path=payload_path,
            phase_session_path=phase_session_path,
            worker_state="active",
        )

    def _validate_work_item_fence(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Reject stale or revoked external-agent tool calls before side effects."""

        has_fence = any(
            payload.get(field)
            for field in ("work_item_id", "batch_id", "control_epoch", "site_revision")
        )
        if not has_fence and not protocol_version_from(payload):
            return {"legacy_unversioned": True}
        fence = WorkItemFence(
            work_item_id=str(payload.get("work_item_id") or "").strip(),
            site_key=str(payload.get("site_key") or "").strip(),
            batch_id=str(payload.get("batch_id") or "").strip(),
            control_epoch=int(payload.get("control_epoch") or 0),
            site_revision=int(payload.get("site_revision") or 0),
        )
        if not all((fence.work_item_id, fence.site_key, fence.batch_id, fence.control_epoch, fence.site_revision)):
            raise ValueError("agent bridge request has incomplete work-item fencing")
        record = WorkItemStore(self.workspace).validate_fence(fence)
        if "context_revision" in payload:
            expected_context_revision = int(payload.get("context_revision") or 0)
            if expected_context_revision <= 0:
                raise ValueError("agent bridge request has invalid context revision")
            if expected_context_revision != int(record.get("context_revision") or 0):
                raise ValueError("work item context revision is stale")
        expected_target = str(payload.get("apply_target_job_id") or "").strip()
        if expected_target:
            work_item_payload = read_json(Path(str(record.get("payload_path") or "")))
            active_targets = {
                str(value or "").strip()
                for value in work_item_payload.get("apply_target_job_ids") or []
                if str(value or "").strip()
            }
            if expected_target not in active_targets:
                raise ValueError("apply target fence does not match the active work item target")
        return record

    def _validate_work_item_resume_upload(
        self,
        work_item_record: dict[str, Any],
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        """Fence file uploads to the immutable resume snapshot declared by the batch."""

        if str(tool_name or "").strip() != "browser_file_upload":
            return
        payload_path = Path(str(work_item_record.get("payload_path") or ""))
        work_item_payload = read_json(payload_path) if payload_path.is_file() else {}
        apply_facts = (
            work_item_payload.get("apply_initial_facts")
            if isinstance(work_item_payload.get("apply_initial_facts"), dict)
            else {}
        )
        staged_resume = (
            apply_facts.get("staged_resume")
            if isinstance(apply_facts.get("staged_resume"), dict)
            else {}
        )
        site_key = str(work_item_payload.get("site_key") or "").strip()
        batch_id = str(work_item_payload.get("batch_id") or "").strip()
        if not staged_resume or not site_key or not batch_id:
            raise ValueError("browser_file_upload requires an available batch resume snapshot")
        validated_snapshot = validate_site_resume_snapshot(
            staged_resume,
            workspace=self.workspace,
            site_key=site_key,
            batch_id=batch_id,
        )
        raw_paths = arguments.get("paths")
        if isinstance(raw_paths, str):
            upload_paths = [raw_paths]
        elif isinstance(raw_paths, list):
            upload_paths = [str(path or "") for path in raw_paths if str(path or "").strip()]
        else:
            single_path = str(arguments.get("path") or "").strip()
            upload_paths = [single_path] if single_path else []
        if not upload_paths:
            raise ValueError("browser_file_upload requires the batch resume snapshot path")
        expected = Path(str(validated_snapshot.get("path") or "")).resolve()
        if any(Path(path).expanduser().resolve() != expected for path in upload_paths):
            raise ValueError("browser_file_upload path does not match the batch resume snapshot")

    def _handle_agent_bridge_read_context_resource(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Read one worker-selected resource through the retained runtime."""

        site_key = str(payload.get("site_key") or "").strip()
        resource_id = str(payload.get("resource_id") or "").strip()
        phase = str(payload.get("phase") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        try:
            def _read() -> dict[str, Any]:
                browser_runner = getattr(self.loop, "browser_runner", None)
                read_resource = getattr(browser_runner, "read_active_context_resource", None)
                if not callable(read_resource):
                    raise RuntimeError("agent bridge context resource read is unavailable")
                return read_resource(
                    site_key=site_key,
                    resource_id=resource_id,
                    phase=phase,
                    reason=reason,
                )

            result = self._run_site_operation(site_key, _read)
        except Exception as exc:
            self._record_codex_activity(site_key)
            return {"ok": False, "error": str(exc)}
        self._record_codex_activity(site_key)
        return {"ok": True, "site_key": site_key, "result": result}

    def _run_site_operation(self, site_key: str, operation: Callable[[], Any]) -> Any:
        """Serialize only one site's retained runtime, never the workspace."""

        normalized_site = str(site_key or "").strip()
        if not normalized_site:
            return operation()
        with self._site_locks_guard:
            lock = self._site_locks.setdefault(normalized_site, threading.Lock())
        lock.acquire()
        try:
            return operation()
        finally:
            lock.release()

    def _maybe_create_site_run_summary(self, batch_id: str, *, site_key: str = "") -> bool:
        """Request one LLM summary after a terminal exploration site run."""

        normalized_batch_id = str(batch_id or "").strip()
        if not normalized_batch_id:
            return False
        job_flow = getattr(self.loop, "job_flow", None)
        job_store = getattr(job_flow, "job_store", None)
        if job_store is None:
            return False
        batch = job_store.load_batch(normalized_batch_id)
        if str(batch.get("status") or "") == "cancelled":
            return False
        try:
            from careereng.evolution.site_run_loop import SiteRunEvolutionCoordinator

            updated, summary_created = self._site_run_coordinator(job_flow).request_summary_if_needed(
                batch,
                site_key=site_key,
            )
            if summary_created:
                append_event = getattr(job_store, "append_event", None)
                if callable(append_event):
                    append_event(
                        "evolution.site_run_summary.requested",
                        {"batch_id": normalized_batch_id, "source": "terminal_exploration"},
                    )
                if str(batch.get("execution_backend") or "") == PROVIDER_BACKEND:
                    self._consume_provider_evolution_solution(batch=updated)
                return True
        except Exception as exc:  # pragma: no cover - terminal evidence must not fail a completed batch
            append_event = getattr(job_store, "append_event", None)
            if callable(append_event):
                append_event(
                    "evolution.site_run_summary.failed",
                    {"batch_id": normalized_batch_id, "error": str(exc)},
                )
            return False
        evolution_config = getattr(self.config, "evolution", None)
        review_config = getattr(evolution_config, "batch_review", None)
        threshold = int(getattr(review_config, "site_run_threshold", 5) or 5)
        loop_config = getattr(evolution_config, "loops", None)
        try:
            from careereng.evolution.triggers import create_site_batch_evolution_reviews

            result = create_site_batch_evolution_reviews(
                project_root=self.project_root,
                workspace=self.workspace,
                batch=batch,
                site_run_threshold=threshold,
                inner_attempt_limit=int(getattr(loop_config, "inner_attempt_limit", 3) or 3),
                outer_batch_limit=int(getattr(loop_config, "outer_batch_limit", 3) or 3),
                publish_event=getattr(job_flow, "publish_agent_event", None),
            )
        except Exception as exc:  # pragma: no cover - never turn an execution result into a failed batch
            job_store.append_event(
                "evolution.batch_review.failed",
                {"batch_id": normalized_batch_id, "error": str(exc)},
            )
            return False
        if int(result.get("triggered_count") or 0):
            job_store.append_event(
                "evolution.batch_review.triggered",
                {
                    "batch_id": normalized_batch_id,
                    "site_run_threshold": threshold,
                    "triggered": result.get("triggered") if isinstance(result.get("triggered"), list) else [],
                },
            )
        return False

    def _consume_provider_evolution_solution(self, *, batch: dict[str, Any]) -> None:
        """Run the same site-run proposal/apply contract through a provider."""

        from careereng.evolution.apply import apply_evolution_run
        from careereng.evolution.solution_bridge import ProviderSolutionBridge

        provider = getattr(self.loop.job_flow, "solution_provider", None)
        model = str(getattr(self.loop.job_flow, "solution_model", "") or "")
        if provider is None or not callable(getattr(provider, "chat", None)):
            return
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        for site_key, row in sites.items():
            if not isinstance(row, dict):
                continue
            run_id = str(row.get("solution_run_id") or "").strip()
            if not run_id:
                continue
            bridge = ProviderSolutionBridge(
                project_root=self.project_root,
                workspace=self.workspace,
                provider=provider,
                model=model,
            )
            bridge.write_proposal_for_run(run_id)
            apply_evolution_run(workspace=self.workspace, project_root=self.project_root, run_id=run_id)
            coordinator = self._site_run_coordinator(self.loop.job_flow)
            updated, decision = coordinator.consume_applied_summary(
                batch=batch,
                site_key=str(site_key),
                run_id=run_id,
            )
            successor = coordinator.create_followup_if_needed(
                batch=updated,
                site_key=str(site_key),
                decision=decision,
            )
            self._record_site_worker_batch_outcome(site_key=str(site_key), batch_id=str(updated.get("batch_id") or ""))
            if not successor:
                self._record_effective_site_run(site_key=str(site_key), batch_id=str(updated.get("batch_id") or ""))
                self._maybe_create_site_run_summary(str(updated.get("batch_id") or ""), site_key=str(site_key))
            if successor:
                next_batch_id = str(successor.get("batch_id") or "")
                self.loop.job_flow.run_batch(next_batch_id, site_keys=[str(site_key)])

    def _activate_codex_evolution_solution(self, *, site_key: str, batch_id: str) -> None:
        """Refresh the retained Codex work item with a completed site's summary task."""

        if self._codex_workers is None:
            return
        job_flow = getattr(self.loop, "job_flow", None)
        job_store = getattr(job_flow, "job_store", None)
        site_store = getattr(getattr(job_flow, "site_tools", None), "site_store", None)
        if job_store is None or site_store is None:
            return
        batch = job_store.load_batch(batch_id)
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        row = sites.get(site_key) if isinstance(sites.get(site_key), dict) else {}
        if str(batch.get("execution_backend") or "") != CODEX_BACKEND:
            return
        run_id = str(row.get("solution_run_id") or "").strip()
        solution_request = str(row.get("solution_request") or "").strip()
        proposal_output_path = str(row.get("proposal_output_path") or "").strip()
        if not run_id or not solution_request or not proposal_output_path:
            return
        session = site_store.load_browser_session(site_key)
        payload_path = Path(str(session.get("agent_bridge_payload_path") or ""))
        phase_session_path = Path(str(session.get("phase_session_path") or ""))
        if not payload_path.is_file() or not phase_session_path.is_file():
            return
        run_payload = read_json(Path(self.workspace) / "evolution" / "runs" / run_id / "run.json")
        activate_browser_agent_evolution_solution(
            workspace=self.workspace,
            payload_path=payload_path,
            phase_session_path=phase_session_path,
            run_id=run_id,
            solution_request=solution_request,
            proposal_output_path=proposal_output_path,
            evidence_pack=str((run_payload.get("outputs") or {}).get("evidence_pack") or ""),
            solution_status=str(run_payload.get("status") or "waiting_solution"),
        )

    def resume_pending_codex_evolution_summaries(self) -> int:
        """Refresh persisted side-work payloads without restarting site workers."""

        if self._codex_workers is None:
            return 0
        job_flow = getattr(self.loop, "job_flow", None)
        job_store = getattr(job_flow, "job_store", None)
        site_store = getattr(getattr(job_flow, "site_tools", None), "site_store", None)
        if job_store is None or site_store is None:
            return 0
        refreshed = 0
        for batch in job_store.list_batches(include_terminal=True):
            if str(batch.get("execution_backend") or "") != CODEX_BACKEND:
                continue
            batch_id = str(batch.get("batch_id") or "")
            sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
            for site_key, row in sites.items():
                if not isinstance(row, dict):
                    continue
                run_id = str(row.get("solution_run_id") or "")
                if not run_id:
                    continue
                run_payload = read_json(Path(self.workspace) / "evolution" / "runs" / run_id / "run.json")
                if str(run_payload.get("status") or "") not in {"waiting_solution", "proposal_written", "applied"}:
                    continue
                batch = self._site_run_coordinator(job_flow).retain_pending_summary(batch)
                self._activate_codex_evolution_solution(site_key=str(site_key), batch_id=batch_id)
                refreshed += 1
        return refreshed

    def _evolution_solution_batch_row(self, *, site_key: str, batch_id: str, run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """Verify that a summary run belongs to the retained terminal site batch."""

        job_flow = getattr(self.loop, "job_flow", None)
        job_store = getattr(job_flow, "job_store", None)
        if job_store is None:
            raise ValueError("workflow store is unavailable")
        batch = job_store.load_batch(batch_id)
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        row = sites.get(site_key) if isinstance(sites.get(site_key), dict) else {}
        if str(row.get("solution_run_id") or "") != run_id:
            raise ValueError("evolution run does not belong to this site batch")
        return batch, row

    def _handle_agent_bridge_submit_evolution_proposal(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist an LLM-authored proposal without making a workflow decision."""

        site_key = str(payload.get("site_key") or "").strip()
        batch_id = str(payload.get("batch_id") or "").strip()
        run_id = str(payload.get("run_id") or "").strip()
        proposal = payload.get("proposal") if isinstance(payload.get("proposal"), dict) else {}
        if not site_key or not batch_id or not run_id:
            return {"ok": False, "error": "site_key, batch_id, and run_id are required"}
        try:
            self._evolution_solution_batch_row(site_key=site_key, batch_id=batch_id, run_id=run_id)
            from careereng.evolution.artifacts import EvolutionProposalArtifactStore
            from careereng.evolution.proposals import EvolutionProposalError, validate_proposal

            run_dir = Path(self.workspace) / "evolution" / "runs" / run_id
            run_path = run_dir / "run.json"
            run_payload = read_json(run_path)
            if not run_payload:
                raise ValueError("evolution run is unavailable")
            if str(proposal.get("run_id") or "") != str(run_payload.get("run_id") or ""):
                raise ValueError("proposal run_id does not match the evolution run")
            if str(proposal.get("candidate_id") or "") != str(run_payload.get("candidate_id") or ""):
                raise ValueError("proposal candidate_id does not match the evolution run")
            validate_proposal(proposal)
            proposal_path = EvolutionProposalArtifactStore().save_json(run_dir, proposal)
            run_payload["status"] = "proposal_written"
            run_payload["updated_at"] = now_iso()
            lifecycle = run_payload.setdefault("lifecycle", [])
            if isinstance(lifecycle, list):
                lifecycle.append({"status": "proposal_written", "at": now_iso(), "summary": "Proposal submitted through the bounded agent work item."})
            write_json(run_path, run_payload)
        except (ValueError, EvolutionProposalError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "run_id": run_id, "proposal_output_path": str(proposal_path), "status": "proposal_written"}

    def _handle_agent_bridge_apply_evolution_solution(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply a previously persisted proposal through the shared apply contract."""

        site_key = str(payload.get("site_key") or "").strip()
        batch_id = str(payload.get("batch_id") or "").strip()
        run_id = str(payload.get("run_id") or "").strip()
        if not site_key or not batch_id or not run_id:
            return {"ok": False, "error": "site_key, batch_id, and run_id are required"}
        try:
            self._evolution_solution_batch_row(site_key=site_key, batch_id=batch_id, run_id=run_id)
            run_path = Path(self.workspace) / "evolution" / "runs" / run_id / "run.json"
            run_payload = read_json(run_path)
            if str(run_payload.get("status") or "") == "applied":
                return {
                    "ok": True,
                    "run_id": run_id,
                    "result": {"run_id": run_id, "status": "already_applied"},
                }
            from careereng.evolution.apply import EvolutionApplyError, apply_evolution_run
            from careereng.evolution.proposals import EvolutionProposalError

            result = apply_evolution_run(workspace=self.workspace, project_root=self.project_root, run_id=run_id)
        except (ValueError, EvolutionApplyError, EvolutionProposalError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "run_id": run_id, "result": result}

    def _handle_agent_bridge_evolution_solution_complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Consume one applied site synthesis and schedule only the existing successor path."""

        site_key = str(payload.get("site_key") or "").strip()
        batch_id = str(payload.get("batch_id") or "").strip()
        run_id = str(payload.get("run_id") or "").strip()
        if not site_key or not batch_id or not run_id:
            return {"ok": False, "error": "site_key, batch_id, and run_id are required"}
        job_flow = getattr(self.loop, "job_flow", None)
        job_store = getattr(job_flow, "job_store", None)
        site_store = getattr(getattr(job_flow, "site_tools", None), "site_store", None)
        if job_store is None or site_store is None:
            return {"ok": False, "error": "workflow stores are unavailable"}
        batch = job_store.load_batch(batch_id)
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        row = sites.get(site_key) if isinstance(sites.get(site_key), dict) else {}
        if str(row.get("solution_run_id") or "") != run_id:
            return {"ok": False, "error": "evolution run does not belong to this site batch"}
        run_payload = read_json(Path(self.workspace) / "evolution" / "runs" / run_id / "run.json")
        if str(run_payload.get("status") or "") != "applied":
            return {"ok": False, "error": "evolution proposal has not been applied"}
        coordinator = self._site_run_coordinator(job_flow)
        updated, decision = coordinator.consume_applied_summary(batch=batch, site_key=site_key, run_id=run_id)
        successor = coordinator.create_followup_if_needed(batch=updated, site_key=site_key, decision=decision) or {}
        self._record_site_worker_batch_outcome(site_key=site_key, batch_id=str(updated.get("batch_id") or ""))
        if not successor:
            self._record_effective_site_run(site_key=site_key, batch_id=str(updated.get("batch_id") or ""))
            self._maybe_create_site_run_summary(str(updated.get("batch_id") or ""), site_key=site_key)
            latest = job_store.load_batch(batch_id)
            latest_site = (latest.get("sites") or {}).get(site_key) if isinstance(latest.get("sites"), dict) else {}
            release = getattr(job_flow, "_release_site_if_non_resumable", None)
            if callable(release) and isinstance(latest_site, dict):
                release(batch_id=batch_id, site_key=site_key, site=latest_site)
        session = site_store.load_browser_session(site_key)
        payload_path = Path(str(session.get("agent_bridge_payload_path") or ""))
        phase_session_path = Path(str(session.get("phase_session_path") or ""))
        if payload_path.is_file() and phase_session_path.is_file():
            set_browser_agent_work_order_state(
                workspace=self.workspace,
                payload_path=payload_path,
                phase_session_path=phase_session_path,
                worker_state="completed",
            )
        next_batch_id = str(successor.get("batch_id") or "")
        if next_batch_id:
            # Completion is a control-plane operation. The next site's browser
            # work can be slow, so schedule it after this RPC returns.
            self._schedule_successor_batch(site_key=site_key, batch_id=next_batch_id)
        return {
            "ok": True,
            "batch_id": batch_id,
            "site_key": site_key,
            "run_id": run_id,
            "next_batch_id": next_batch_id,
            "status": "continued" if next_batch_id else "completed",
        }

    def _schedule_successor_batch(self, *, site_key: str, batch_id: str) -> None:
        """Start one persisted successor without blocking summary completion."""

        def _run() -> None:
            job_flow = getattr(self.loop, "job_flow", None)
            job_store = getattr(job_flow, "job_store", None)
            try:
                if job_flow is None:
                    raise RuntimeError("workflow is unavailable")
                job_flow.run_batch(batch_id, site_keys=[site_key])
                self._enqueue_codex_workers_for_batch(batch_id, site_keys=[site_key])
            except Exception as exc:
                append_event = getattr(job_store, "append_event", None)
                if callable(append_event):
                    append_event(
                        "evolution.successor_schedule.failed",
                        {"batch_id": batch_id, "site_key": site_key, "error": str(exc)},
                    )

        threading.Thread(
            target=_run,
            name=f"careereng-successor-{site_key}",
            daemon=True,
        ).start()

    def _site_run_coordinator(self, job_flow: Any):
        from careereng.evolution.site_run_loop import SiteRunEvolutionCoordinator

        loop_config = getattr(getattr(self.config, "evolution", None), "loops", None)
        return SiteRunEvolutionCoordinator(
            job_flow,
            exploration_attempt_limit=int(getattr(loop_config, "inner_attempt_limit", 3) or 3),
        )

    def _build_codex_worker_coordinator(self):
        browser_runner = getattr(self.loop, "browser_runner", None)
        if str(getattr(browser_runner, "execution_mode", "") or "") != CODEX_APP_SERVER_MODE:
            return None
        from careereng.adapters.codex import CodexAppServerClient, CodexWorkerCoordinator

        agent_config = getattr(self.config, "agent", None)
        worker_limit = int(getattr(agent_config, "site_parallelism", 1) or 1)
        return CodexWorkerCoordinator(
            project_root=self.project_root,
            workspace=self.workspace,
            worker_limit=worker_limit,
            max_effective_batches_per_session=int(
                getattr(getattr(getattr(self.config, "evolution", None), "batch_review", None), "site_run_threshold", 5)
                or 5
            ),
            app_server_factory=lambda callback: CodexAppServerClient(
                cwd=self.project_root,
                event_callback=callback,
            ),
            idle_timeout_seconds=int(getattr(getattr(agent_config, "recovery", None), "idle_timeout_seconds", 180) or 180),
            max_resume_attempts=int(getattr(getattr(agent_config, "recovery", None), "max_resume_attempts", 2) or 0),
            interrupt_ack_timeout_seconds=int(
                getattr(getattr(agent_config, "recovery", None), "interrupt_ack_timeout_seconds", 15) or 15
            ),
            max_interrupt_attempts=int(
                getattr(getattr(agent_config, "recovery", None), "max_interrupt_attempts", 2) or 2
            ),
            on_record=self._record_codex_worker,
            on_usage=self._record_codex_usage,
            on_recovery=self._record_codex_recovery,
            on_transport_event=self._record_codex_transport,
            on_server_request=self._handle_codex_server_request,
        )

    def _handle_codex_server_request(self, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """Accept only the current exploration worker's synthesis tool calls.

        This is a transport permission decision, not an evolution decision. The
        worker still authors the proposal and the existing validation/apply
        contract decides whether it can be persisted. All browser, file, and
        ordinary state tools remain outside this automatic scope.
        """

        if str(method) != "mcpServer/elicitation/request":
            return None
        if str(params.get("serverName") or "") != "careereng":
            return None
        metadata = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        if str(metadata.get("codex_approval_kind") or "") != "mcp_tool_call":
            return None
        tool_params = metadata.get("tool_params") if isinstance(metadata.get("tool_params"), dict) else {}
        tool_name = self._elicited_tool_name(str(params.get("message") or ""))
        if tool_name not in _AUTONOMOUS_EXPLORATION_SUMMARY_TOOLS:
            return None
        thread_id = str(params.get("threadId") or "")
        workers = self._codex_workers
        record_for_thread = getattr(workers, "record_for_thread", None)
        record = record_for_thread(thread_id) if callable(record_for_thread) else None
        if record is None:
            return None
        if str(tool_params.get("work_item_id") or "") != str(getattr(record, "work_item_id", "") or ""):
            return None
        payload = read_json(Path(getattr(record, "payload_path", "")))
        evolution = payload.get("evolution_solution") if isinstance(payload.get("evolution_solution"), dict) else {}
        if str(payload.get("current_phase") or "") != "evolution_summary" or not str(evolution.get("run_id") or ""):
            return None
        requested_run_id = str(tool_params.get("run_id") or "")
        if tool_name == "careereng_submit_evolution_proposal":
            proposal = tool_params.get("proposal") if isinstance(tool_params.get("proposal"), dict) else {}
            requested_run_id = str(proposal.get("run_id") or "")
        if requested_run_id and requested_run_id != str(evolution.get("run_id") or ""):
            return None
        self._record_codex_transport(
            record,
            {
                "event": "mcp_elicitation_auto_approved",
                "tool_name": tool_name,
                "thread_id": thread_id,
                "turn_id": str(params.get("turnId") or ""),
                "work_item_id": str(record.work_item_id),
                "run_id": str(evolution.get("run_id") or ""),
            },
        )
        return {"action": "accept", "content": {}}

    @staticmethod
    def _elicited_tool_name(message: str) -> str:
        match = re.search(r'tool\s+"([^"]+)"', str(message or ""))
        return str(match.group(1) if match else "")

    def _enqueue_codex_workers_for_batch(self, batch_id: str, *, site_keys: list[str] | None = None) -> None:
        if self._codex_workers is None:
            return
        from careereng.adapters.codex.worker_runner import worker_record_from_payload

        job_flow = getattr(self.loop, "job_flow", None)
        job_store = getattr(job_flow, "job_store", None)
        site_store = getattr(getattr(job_flow, "site_tools", None), "site_store", None)
        if job_store is None or site_store is None:
            return
        batch = job_store.load_batch(batch_id)
        if str(batch.get("execution_backend") or "provider") != CODEX_BACKEND:
            return
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        for site_key, row in sites.items():
            if site_keys is not None and str(site_key) not in site_keys:
                continue
            if not isinstance(row, dict):
                continue
            session = site_store.load_browser_session(str(site_key))
            payload_path = Path(str(session.get("agent_bridge_payload_path") or ""))
            if not payload_path.exists():
                continue
            record = worker_record_from_payload(payload_path)
            if record.batch_id != batch_id or not record.site_key:
                continue
            try:
                self._codex_workers.enqueue(record)
            except Exception as exc:
                # A local Codex transport failure is a worker availability
                # problem, not a job/application outcome.
                site_store.save_browser_session(
                    str(site_key),
                    {"codex_worker_status": "unavailable", "last_step_error": str(exc)},
                )
                job_store.append_event(
                    "codex.worker.unavailable",
                    {"batch_id": batch_id, "site_key": str(site_key), "error": str(exc)},
                )

    def _resume_codex_site(self, *, site_key: str, message: str, command_id: str = ""):
        if self._codex_workers is None:
            return None
        from careereng.adapters.codex.worker_runner import worker_record_from_payload

        job_flow = getattr(self.loop, "job_flow", None)
        site_store = getattr(getattr(job_flow, "site_tools", None), "site_store", None)
        if site_store is None:
            return None
        session = site_store.load_browser_session(site_key)
        payload_path = Path(str(session.get("agent_bridge_payload_path") or ""))
        if not payload_path.exists():
            return None
        record = worker_record_from_payload(payload_path)
        batch = job_flow.job_store.load_batch(record.batch_id)
        if str(batch.get("execution_backend") or "provider") != CODEX_BACKEND:
            return None
        WorkItemStore(self.workspace).reissue(
            record.work_item_id,
            event="worker_resume_requested",
            command_id=command_id,
        )
        resumed = self._codex_workers.resume_work_order(record, message=message, command_id=command_id)
        wait_for_turn_start = getattr(self._codex_workers, "wait_for_turn_start", None)
        if callable(wait_for_turn_start):
            return wait_for_turn_start(resumed)
        return resumed

    def _record_codex_worker(self, record: Any) -> None:
        """Mirror adapter lifecycle metadata into existing site/session evidence."""

        job_flow = getattr(self.loop, "job_flow", None)
        site_store = getattr(getattr(job_flow, "site_tools", None), "site_store", None)
        if site_store is None:
            return
        persisted_state = {
            "waiting_user": "waiting_user",
            "paused": "paused",
            "pause_unconfirmed": "pause_unconfirmed",
            "released": "released",
            "cancelled": "cancelled",
        }.get(str(record.status or ""))
        if persisted_state:
            try:
                WorkItemStore(self.workspace).transition(
                    str(record.work_item_id or ""), state=persisted_state, event=f"worker:{record.status}"
                )
            except ValueError:
                pass
        site_store.save_browser_session(
            record.site_key,
            {
                "codex_thread_id": record.thread_id,
                "codex_turn_id": record.turn_id,
                "codex_worker_status": record.status,
                "worker_session_id": record.worker_session_id,
                "worker_session_batch_ordinal": record.session_batch_ordinal,
                "worker_session_reused": record.session_reused,
                "codex_worker_last_error": record.last_error,
            },
        )
        site_store.append_event(
            record.site_key,
            "codex.worker.lifecycle",
            {
                "batch_id": record.batch_id,
                "thread_id": record.thread_id,
                "turn_id": record.turn_id,
                "status": record.status,
                "work_item_id": record.work_item_id,
                "worker_session_id": record.worker_session_id,
                "worker_session_batch_ordinal": record.session_batch_ordinal,
                "worker_session_reused": record.session_reused,
                "worker_session_rotation_reason": record.session_rotation_reason,
                "last_error": record.last_error,
            },
        )
        PerformanceRecorder(self.workspace).record(
            backend="codex_app_server",
            operation="worker_thread",
            site_key=record.site_key,
            batch_id=record.batch_id,
            status=record.status,
            worker_session_id=record.worker_session_id,
            worker_session_batch_ordinal=record.session_batch_ordinal,
        )
        if record.status in {"completed", "unavailable", "interrupted", "cancelled"}:
            self._shutdown_if_idle()

    def _record_codex_usage(self, record: Any, event: dict[str, Any]) -> None:
        """Persist App Server usage facts without interpreting worker behavior."""

        usage = event.get("tokenUsage") if isinstance(event.get("tokenUsage"), dict) else event.get("usage")
        PerformanceRecorder(self.workspace).record(
            backend="codex_app_server",
            operation="worker_token_usage",
            site_key=record.site_key,
            batch_id=record.batch_id,
            phase="",
            status="ok",
            work_item_id=record.work_item_id,
            thread_id=record.thread_id,
            turn_id=record.turn_id,
            worker_session_id=record.worker_session_id,
            worker_session_batch_ordinal=record.session_batch_ordinal,
            token_usage=usage if isinstance(usage, dict) else {},
        )

    def _record_codex_transport(self, record: Any | None, payload: dict[str, Any]) -> None:
        """Persist raw Codex transport facts with any known site-work correlation."""

        event = str(payload.get("event") or "unknown")
        correlation: dict[str, Any] = {}
        if record is not None:
            phase_session = read_json(Path(record.phase_session_path)) if getattr(record, "phase_session_path", None) else {}
            current_phase = phase_session.get("current_phase") if isinstance(phase_session, dict) else {}
            correlation = {
                "site_key": record.site_key,
                "batch_id": record.batch_id,
                "work_item_id": record.work_item_id,
                "thread_id": record.thread_id,
                "turn_id": record.turn_id,
                "worker_session_id": record.worker_session_id,
                "phase": str(current_phase.get("slug") or "") if isinstance(current_phase, dict) else "",
            }
        # The worker may include thread/turn IDs in a raw transport event.  The
        # correlated record is authoritative, and duplicate keyword expansion
        # must never take down the watchdog while it records an interruption.
        details = {
            key: value
            for key, value in payload.items()
            if key != "event" and key not in correlation
        }
        self._agent_transport_trace.record(
            backend="codex_app_server",
            event=event,
            **correlation,
            **details,
        )

    def _record_codex_activity(self, site_key: str) -> None:
        """Refresh only the objective activity clock for the active Codex site."""

        if self._codex_workers is not None:
            self._codex_workers.record_activity(site_key=site_key)

    def _record_codex_recovery(self, record: Any, status: str) -> None:
        """Persist a technical no-progress event without assigning a job outcome."""

        job_flow = getattr(self.loop, "job_flow", None)
        site_store = getattr(getattr(job_flow, "site_tools", None), "site_store", None)
        payload = read_json(Path(record.payload_path)) if getattr(record, "payload_path", None) else {}
        browser_session = site_store.load_browser_session(record.site_key) if site_store is not None else {}
        details = {
            "batch_id": record.batch_id,
            "thread_id": record.thread_id,
            "turn_id": record.turn_id,
            "work_item_id": record.work_item_id,
            "phase": str(payload.get("current_phase") or ""),
            "current_url": str(browser_session.get("last_known_url") or ""),
            "trace_ref": str(browser_session.get("current_trace_ref") or ""),
            "last_browser_error": str(browser_session.get("last_step_error") or ""),
            "recovery_attempts": record.recovery_attempts,
            "last_error": record.last_error,
        }
        if site_store is not None:
            site_store.append_event(record.site_key, "codex.execution.recovery", {"status": status, **details})
        ExecutionDiagnosticStore(self.workspace).record(
            kind="execution_recovery",
            status=status,
            site_key=record.site_key,
            **details,
        )
        metric_details = {key: value for key, value in details.items() if key != "batch_id"}
        PerformanceRecorder(self.workspace).record(
            backend="codex_app_server",
            operation="execution_recovery",
            site_key=record.site_key,
            batch_id=record.batch_id,
            status=status,
            **metric_details,
        )
        if status == "exhausted":
            record_exhausted = getattr(job_flow, "record_external_execution_recovery_exhausted", None)
            if not callable(record_exhausted):
                record_exhausted = getattr(job_flow, "record_external_execution_unavailable", None)
            if callable(record_exhausted):
                record_exhausted(
                    site_key=record.site_key,
                    batch_id=record.batch_id,
                    phase=str(details.get("phase") or ""),
                    summary=str(record.last_error or "External worker execution recovery was exhausted."),
                )
            self._publish_agent_event(
                kind="site.execution_recovery_exhausted",
                attention="review_required",
                summary=str(record.last_error or "Execution recovery was exhausted."),
                site_key=record.site_key,
                batch_id=record.batch_id,
                thread_id=record.thread_id,
                turn_id=record.turn_id,
                phase=str(details.get("phase") or ""),
                current_url=str(details.get("current_url") or ""),
                details={"recovery_attempts": record.recovery_attempts},
            )

    def _publish_agent_event(self, **payload: Any) -> dict[str, Any] | None:
        job_flow = getattr(self.loop, "job_flow", None)
        publisher = getattr(job_flow, "publish_agent_event", None)
        if not callable(publisher):
            return None
        return publisher(**payload)

    def _record_site_worker_batch_outcome(self, *, site_key: str, batch_id: str) -> None:
        """Persist terminal batch facts for an owning worker session."""

        normalized_site = str(site_key or "").strip()
        normalized_batch = str(batch_id or "").strip()
        if not normalized_site or not normalized_batch:
            return
        job_flow = getattr(self.loop, "job_flow", None)
        job_store = getattr(job_flow, "job_store", None)
        if job_store is None:
            return
        batch = job_store.load_batch(normalized_batch)
        batch_status = str(batch.get("status") or "")
        if batch_status not in {"completed", "partial_completed", "failed", "cancelled"}:
            return
        evidence = self._site_worker_sessions.site_evidence(normalized_site, backend="codex_app_server")
        for session in evidence.get("sessions", []):
            if not isinstance(session, dict):
                continue
            bindings = session.get("batch_bindings") if isinstance(session.get("batch_bindings"), list) else []
            if any(str(binding.get("batch_id") or "") == normalized_batch for binding in bindings if isinstance(binding, dict)):
                self._site_worker_sessions.record_batch_outcome(
                    worker_session_id=str(session.get("worker_session_id") or ""),
                    batch_id=normalized_batch,
                    batch_status=batch_status,
                )
                if batch_status == "cancelled":
                    self._retract_effective_site_run(site_key=normalized_site, batch_id=normalized_batch)
                return

    @staticmethod
    def _site_uses_exploration(batch: dict[str, Any], *, site_key: str) -> bool:
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        row = sites.get(site_key) if isinstance(sites.get(site_key), dict) else {}
        scope = row.get("evolution_scope") if isinstance(row.get("evolution_scope"), dict) else {}
        mode = str(scope.get("execution_mode") or row.get("execution_mode") or row.get("site_mode") or "")
        return mode == "exploration"

    def _record_effective_site_run(self, *, site_key: str, batch_id: str) -> None:
        """Record one already-closed site run without evaluating its business result."""

        normalized_site = str(site_key or "").strip()
        normalized_batch = str(batch_id or "").strip()
        if not normalized_site or not normalized_batch:
            return
        job_flow = getattr(self.loop, "job_flow", None)
        job_store = getattr(job_flow, "job_store", None)
        if job_store is None:
            return
        batch = job_store.load_batch(normalized_batch)
        run_id = self._effective_site_run_id(batch, site_key=normalized_site, batch_id=normalized_batch)
        evidence = self._site_worker_sessions.site_evidence(normalized_site)
        for session in evidence.get("sessions", []):
            if not isinstance(session, dict):
                continue
            bindings = session.get("batch_bindings") if isinstance(session.get("batch_bindings"), list) else []
            if not any(str(binding.get("batch_id") or "") == normalized_batch for binding in bindings if isinstance(binding, dict)):
                continue
            updated = self._site_worker_sessions.record_effective_site_run(
                worker_session_id=str(session.get("worker_session_id") or ""),
                batch_id=normalized_batch,
                run_id=run_id,
            )
            if updated is not None:
                job_store.append_event(
                    "site_worker.effective_run_recorded",
                    {
                        "site_key": normalized_site,
                        "batch_id": normalized_batch,
                        "run_id": run_id,
                        "worker_session_id": str(session.get("worker_session_id") or ""),
                        "effective_site_run_count": len(updated.get("effective_run_ids") or []),
                    },
                )
            return

    def _retract_effective_site_run(self, *, site_key: str, batch_id: str) -> None:
        """Retract a counted site run after orchestration cancels its batch."""

        normalized_site = str(site_key or "").strip()
        normalized_batch = str(batch_id or "").strip()
        if not normalized_site or not normalized_batch:
            return
        job_flow = getattr(self.loop, "job_flow", None)
        job_store = getattr(job_flow, "job_store", None)
        if job_store is None:
            return
        batch = job_store.load_batch(normalized_batch)
        run_id = self._effective_site_run_id(batch, site_key=normalized_site, batch_id=normalized_batch)
        evidence = self._site_worker_sessions.site_evidence(normalized_site)
        for session in evidence.get("sessions", []):
            if not isinstance(session, dict):
                continue
            bindings = session.get("batch_bindings") if isinstance(session.get("batch_bindings"), list) else []
            if not any(str(binding.get("batch_id") or "") == normalized_batch for binding in bindings if isinstance(binding, dict)):
                continue
            if run_id not in session.get("effective_run_ids", []):
                return
            updated = self._site_worker_sessions.retract_effective_site_run(
                worker_session_id=str(session.get("worker_session_id") or ""),
                batch_id=normalized_batch,
                run_id=run_id,
            )
            if updated is not None:
                job_store.append_event(
                    "site_worker.effective_run_retracted",
                    {
                        "site_key": normalized_site,
                        "batch_id": normalized_batch,
                        "run_id": run_id,
                        "worker_session_id": str(session.get("worker_session_id") or ""),
                        "effective_site_run_count": len(updated.get("effective_run_ids") or []),
                    },
                )
            return

    @classmethod
    def _effective_site_run_id(cls, batch: dict[str, Any], *, site_key: str, batch_id: str) -> str:
        site_run = batch.get("site_run") if isinstance(batch.get("site_run"), dict) else {}
        if cls._site_uses_exploration(batch, site_key=site_key):
            return f"exploration:{str(site_run.get('root_batch_id') or batch_id)}"
        return f"batch:{batch_id}"

    def _record_terminal_ready_site_run(self, *, site_key: str, batch_id: str) -> None:
        """Count only a normal completed ready-site run.

        A phase result can carry a batch id long before its site has a normal
        terminal outcome.  Counting from that signal would rotate a Codex
        thread after waiting-user, blocked, or technical execution states.
        """

        job_flow = getattr(self.loop, "job_flow", None)
        job_store = getattr(job_flow, "job_store", None)
        if job_store is None:
            return
        batch = job_store.load_batch(str(batch_id or ""))
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        site = sites.get(str(site_key or "")) if isinstance(sites.get(str(site_key or "")), dict) else {}
        if self._site_uses_exploration(batch, site_key=str(site_key or "")):
            return
        if str(site.get("status") or "") != "completed":
            return
        apply = site.get("apply") if isinstance(site.get("apply"), dict) else {}
        if str(apply.get("status") or "") in {"blocked", "failed"}:
            return
        self._record_effective_site_run(site_key=site_key, batch_id=batch_id)

    def _bind_provider_site_sessions(self, batch: dict[str, Any], *, site_keys: list[str] | None) -> None:
        """Give provider execution the same persisted run counter as Codex."""

        batch_id = str(batch.get("batch_id") or "").strip()
        if not batch_id:
            return
        evolution = getattr(self.config, "evolution", None)
        review = getattr(evolution, "batch_review", None)
        limit = int(getattr(review, "site_run_threshold", 5) or 5)
        allowed = set(site_keys or [])
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        for site_key, row in sites.items():
            if not isinstance(row, dict) or str(row.get("status") or "") == "cancelled":
                continue
            normalized_site = str(site_key or "").strip()
            if not normalized_site or (allowed and normalized_site not in allowed):
                continue
            self._site_worker_sessions.bind_batch(
                site_key=normalized_site,
                backend="provider",
                batch_id=batch_id,
                max_effective_batches=limit,
            )


# Legacy public type name retained for code that imports it during migration.
WorkspaceManager = RuntimeHostService


class _ManagerRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline()
        if not raw:
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            response = {"ok": False, "error": f"invalid request: {exc}"}
        else:
            try:
                response = self.server.manager.handle_request(payload)
            except Exception as exc:
                # A control-plane failure must still produce a response. Otherwise
                # the client mistakes an executed operation for a dead host.
                response = {
                    "ok": False,
                    "error": f"runtime host request failed: {type(exc).__name__}: {exc}",
                }
        # The transport contract, not a workflow branch, owns the host identity.
        # Every response carries it so stale MCP/CLI clients fail clearly before
        # interpreting a browser or workflow result.
        if isinstance(response, dict):
            response = {**response, **runtime_host_identity()}
        self.wfile.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
        self.wfile.flush()
        if bool(response.get("shutdown")):
            threading.Thread(
                target=self.server.shutdown,
                name="careereng-manager-shutdown",
                daemon=True,
            ).start()


class _UnixRuntimeHostServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, socket_path: str, runtime_host: RuntimeHostService):
        self.manager = runtime_host
        super().__init__(socket_path, _ManagerRequestHandler)


def serve_runtime_host(*, project_root: Path, workspace: Path, socket_path: Path) -> None:
    socket_file = Path(socket_path)
    socket_file.parent.mkdir(parents=True, exist_ok=True)
    if socket_file.exists():
        if _runtime_host_is_compatible(socket_file):
            raise RuntimeHostUnavailableError(
                f"CareerEng runtime host is already running for this workspace: {socket_file}"
            )
        if not _socket_has_no_listener(socket_file):
            raise RuntimeHostUnavailableError(
                "CareerEng runtime host socket exists but is not healthy. "
                "Stop or restart that host before starting another one."
            )
        try:
            socket_file.unlink()
        except FileNotFoundError:
            pass
    runtime_host = RuntimeHostService(project_root=project_root, workspace=workspace)
    server = _UnixRuntimeHostServer(str(socket_file), runtime_host)
    runtime_host.set_idle_shutdown_callback(
        lambda: threading.Thread(
            target=server.shutdown,
            name="careereng-runtime-host-idle-shutdown",
            daemon=True,
        ).start()
    )
    runtime_host.resume_pending_codex_evolution_summaries()
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        runtime_host.close()
        try:
            socket_file.unlink()
        except FileNotFoundError:
            pass


def serve_workspace_manager(*, project_root: Path, workspace: Path, socket_path: Path) -> None:
    """Deprecated compatibility wrapper for ``serve_runtime_host``."""

    serve_runtime_host(project_root=project_root, workspace=workspace, socket_path=socket_path)


def _send_request(socket_path: Path, payload: dict[str, Any], *, timeout: float = 3.0) -> dict[str, Any]:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(socket_path))
        sock.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
            if b"\n" in chunk:
                break
    finally:
        sock.close()
    text = data.decode("utf-8").strip()
    if not text:
        raise RuntimeHostUnavailableError("runtime host returned an empty response")
    return json.loads(text)


def send_runtime_host_request(socket_path: Path, payload: dict[str, Any], *, timeout: float = 3.0) -> dict[str, Any]:
    """Send one transport request to an already running runtime host."""

    return _send_request(Path(socket_path), payload, timeout=timeout)


def _runtime_host_ping_response(socket_path: Path) -> dict[str, Any] | None:
    if not socket_path.exists():
        return None
    try:
        return _send_request(socket_path, with_runtime_host_protocol({"op": "ping"}), timeout=0.8)
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EPERM}:
            raise RuntimeHostAccessDeniedError(
                "The current process cannot inspect the CareerEng runtime host socket. "
                "This does not mean the host is stopped."
            ) from exc
        return None
    except Exception:
        return None


def _runtime_host_is_compatible(socket_path: Path) -> bool:
    response = _runtime_host_ping_response(socket_path)
    if not response:
        return False
    remote_version = protocol_version_from(response)
    if remote_version and remote_version != RUNTIME_HOST_PROTOCOL_VERSION:
        raise RuntimeHostProtocolMismatchError(
            "CareerEng runtime host protocol mismatch "
            f"(expected={RUNTIME_HOST_PROTOCOL_VERSION}, actual={remote_version}). "
            "Reload/restart the local CareerEng runtime host before continuing."
        )
    if not remote_version:
        raise RuntimeHostProtocolMismatchError(
            "CareerEng runtime host did not report a protocol version. "
            "Reload/restart the local CareerEng runtime host before continuing."
        )
    return bool(response.get("ok")) and str(response.get("reply") or "") == "pong"


def _ping_manager(socket_path: Path) -> bool:
    try:
        return _runtime_host_is_compatible(socket_path)
    except RuntimeHostProtocolMismatchError:
        raise
    except Exception:
        return False


def _wait_for_manager_stop(socket_path: Path, *, timeout: float) -> bool:
    deadline = time.time() + max(0.0, float(timeout or 0.0))
    while time.time() < deadline:
        if not _ping_manager(socket_path):
            return True
        time.sleep(0.1)
    return not _ping_manager(socket_path)


def _socket_has_no_listener(socket_path: Path) -> bool:
    """Return true only when a Unix socket is conclusively stale.

    An unknown or slow socket may still belong to a live host. Never unlink it
    during automatic startup, because doing so could create two host owners for
    one workspace.
    """

    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(0.5)
    try:
        probe.connect(str(socket_path))
    except OSError as exc:
        return exc.errno in {errno.ENOENT, errno.ECONNREFUSED}
    finally:
        probe.close()
    return False


def ensure_runtime_host(*, project_root: Path, workspace: Path, autostart: bool = False) -> Path:
    """Return the workspace host endpoint, optionally starting it in this process context."""

    socket_path = runtime_host_socket_path(workspace)
    if _runtime_host_is_compatible(socket_path):
        return socket_path
    if not autostart:
        raise RuntimeHostUnavailableError(
            "CareerEng runtime host is not running. Start it in the local user environment with "
            "`python -m careereng runtime-host serve`."
        )
    if socket_path.exists():
        if not _socket_has_no_listener(socket_path):
            raise RuntimeHostUnavailableError(
                "CareerEng runtime host socket exists but is not healthy. "
                "Stop or restart that host in the local user environment before continuing."
            )
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass
    cmd = [
        sys.executable,
        "-m",
        "careereng",
        "runtime-host",
        "serve",
        "--project-root",
        str(project_root),
        "--workspace",
        str(workspace),
        "--socket-path",
        str(socket_path),
    ]
    subprocess.Popen(
        cmd,
        cwd=str(project_root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env={**os.environ},
    )
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if _runtime_host_is_compatible(socket_path):
            return socket_path
        time.sleep(0.1)
    raise RuntimeHostUnavailableError(f"runtime host did not start: {socket_path}")


def ensure_workspace_manager(*, project_root: Path, workspace: Path) -> Path:
    """Deprecated compatibility wrapper that preserves CLI auto-start behavior."""

    return ensure_runtime_host(project_root=project_root, workspace=workspace, autostart=True)


def dispatch_manager_message(*, project_root: Path, workspace: Path, session_id: str, message: str) -> str:
    socket_path = ensure_workspace_manager(project_root=project_root, workspace=workspace)
    response = _send_request(
        socket_path,
        {
            "op": "process_message",
            "session_id": session_id,
            "message": message,
        },
        timeout=DEFAULT_MANAGER_REQUEST_TIMEOUT_SECONDS,
    )
    if not bool(response.get("ok")):
        raise RuntimeError(str(response.get("error") or "workspace manager request failed"))
    return str(response.get("reply") or "")


def start_manager_jobs_batch(
    *,
    project_root: Path,
    workspace: Path,
    session_id: str,
    message: str,
    operation: str,
    apply_requested: bool,
) -> dict[str, Any]:
    socket_path = ensure_workspace_manager(project_root=project_root, workspace=workspace)
    payload = {
        "op": "start_jobs_batch",
        "session_id": session_id,
        "message": message,
        "operation": operation,
        "apply_requested": bool(apply_requested),
    }
    response = _send_request(socket_path, payload, timeout=10.0)
    if not bool(response.get("ok")) and "unsupported op" in str(response.get("error") or ""):
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass
        socket_path = ensure_workspace_manager(project_root=project_root, workspace=workspace)
        response = _send_request(socket_path, payload, timeout=10.0)
    if not bool(response.get("ok")):
        raise RuntimeError(str(response.get("error") or "workspace manager request failed"))
    return response


def fresh_snapshot_resume(
    *,
    project_root: Path,
    workspace: Path,
    session_id: str,
    message: str,
    turn_id: str = "",
    command_id: str = "",
) -> dict[str, Any]:
    socket_path = ensure_workspace_manager(project_root=project_root, workspace=workspace)
    response = _send_request(
        socket_path,
        {
            "op": "fresh_snapshot_resume",
            "session_id": session_id,
            "message": message,
            "turn_id": turn_id,
            "command_id": command_id,
        },
        timeout=DEFAULT_MANAGER_REQUEST_TIMEOUT_SECONDS,
    )
    if not bool(response.get("ok")):
        raise RuntimeError(str(response.get("error") or "workspace manager request failed"))
    return response


def pause_manager_jobs_batch(
    *,
    project_root: Path,
    workspace: Path,
    batch_id: str,
    site_key: str = "",
) -> dict[str, Any]:
    socket_path = ensure_workspace_manager(project_root=project_root, workspace=workspace)
    response = _send_request(
        socket_path,
        {"op": "pause_jobs_batch", "batch_id": batch_id, "site_key": site_key},
        timeout=DEFAULT_MANAGER_REQUEST_TIMEOUT_SECONDS,
    )
    if not bool(response.get("ok")):
        raise RuntimeError(str(response.get("error") or "workspace manager request failed"))
    return response


def cancel_manager_jobs_batch(
    *,
    project_root: Path,
    workspace: Path,
    batch_id: str,
    reason: str = "user_requested_cancel",
) -> dict[str, Any]:
    socket_path = ensure_workspace_manager(project_root=project_root, workspace=workspace)
    response = _send_request(
        socket_path,
        {"op": "cancel_jobs_batch", "batch_id": batch_id, "reason": reason},
        timeout=DEFAULT_MANAGER_REQUEST_TIMEOUT_SECONDS,
    )
    if not bool(response.get("ok")):
        raise RuntimeError(str(response.get("error") or "workspace manager request failed"))
    return response


def list_agent_bridge_browser_tools(
    *,
    project_root: Path,
    workspace: Path,
    site_key: str,
) -> dict[str, Any]:
    socket_path = ensure_workspace_manager(project_root=project_root, workspace=workspace)
    response = _send_request(
        socket_path,
        {
            "op": "agent_bridge_browser_list_tools",
            "site_key": site_key,
        },
        timeout=DEFAULT_MANAGER_REQUEST_TIMEOUT_SECONDS,
    )
    if not bool(response.get("ok")):
        raise RuntimeError(str(response.get("error") or "workspace manager request failed"))
    return response


def call_agent_bridge_browser_tool(
    *,
    project_root: Path,
    workspace: Path,
    site_key: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    turn_id: str = "",
    phase: str = AGENT_BRIDGE_STATUS,
) -> dict[str, Any]:
    socket_path = ensure_workspace_manager(project_root=project_root, workspace=workspace)
    response = _send_request(
        socket_path,
        {
            "op": "agent_bridge_browser_call_tool",
            "site_key": site_key,
            "tool_name": tool_name,
            "arguments": arguments or {},
            "turn_id": turn_id,
            "phase": phase,
        },
        timeout=DEFAULT_MANAGER_REQUEST_TIMEOUT_SECONDS,
    )
    if not bool(response.get("ok")):
        raise RuntimeError(str(response.get("error") or "workspace manager request failed"))
    return response


def run_agent_bridge_browser_sequence(
    *,
    project_root: Path,
    workspace: Path,
    site_key: str,
    steps: list[dict[str, Any]],
    turn_id: str = "",
    phase: str = AGENT_BRIDGE_STATUS,
) -> dict[str, Any]:
    socket_path = ensure_workspace_manager(project_root=project_root, workspace=workspace)
    response = _send_request(
        socket_path,
        {
            "op": "agent_bridge_browser_run_sequence",
            "site_key": site_key,
            "steps": steps,
            "turn_id": turn_id,
            "phase": phase,
        },
        timeout=DEFAULT_MANAGER_REQUEST_TIMEOUT_SECONDS,
    )
    if not bool(response.get("ok")):
        raise RuntimeError(str(response.get("error") or "workspace manager request failed"))
    return response


def list_agent_bridge_state_tools(
    *,
    project_root: Path,
    workspace: Path,
    site_key: str,
    phase: str = "",
) -> dict[str, Any]:
    socket_path = ensure_workspace_manager(project_root=project_root, workspace=workspace)
    response = _send_request(
        socket_path,
        {
            "op": "agent_bridge_state_list_tools",
            "site_key": site_key,
            "phase": phase,
        },
        timeout=DEFAULT_MANAGER_REQUEST_TIMEOUT_SECONDS,
    )
    if not bool(response.get("ok")):
        raise RuntimeError(str(response.get("error") or "workspace manager request failed"))
    return response


def call_agent_bridge_state_tool(
    *,
    project_root: Path,
    workspace: Path,
    site_key: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    turn_id: str = "",
    phase: str = "",
) -> dict[str, Any]:
    socket_path = ensure_workspace_manager(project_root=project_root, workspace=workspace)
    response = _send_request(
        socket_path,
        {
            "op": "agent_bridge_state_call_tool",
            "site_key": site_key,
            "tool_name": tool_name,
            "arguments": arguments or {},
            "turn_id": turn_id,
            "phase": phase,
        },
        timeout=DEFAULT_MANAGER_REQUEST_TIMEOUT_SECONDS,
    )
    if not bool(response.get("ok")):
        raise RuntimeError(str(response.get("error") or "workspace manager request failed"))
    return response


def list_browser_handoff_tools(
    *,
    project_root: Path,
    workspace: Path,
    site_key: str,
) -> dict[str, Any]:
    return list_agent_bridge_browser_tools(project_root=project_root, workspace=workspace, site_key=site_key)


def call_browser_handoff_tool(
    *,
    project_root: Path,
    workspace: Path,
    site_key: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    turn_id: str = "",
    phase: str = AGENT_BRIDGE_STATUS,
) -> dict[str, Any]:
    return call_agent_bridge_browser_tool(
        project_root=project_root,
        workspace=workspace,
        site_key=site_key,
        tool_name=tool_name,
        arguments=arguments,
        turn_id=turn_id,
        phase=phase,
    )


def shutdown_runtime_host(
    *,
    workspace: Path,
    cancel_open_batches: bool = False,
    session_id: str | None = None,
    wait_timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    socket_path = runtime_host_socket_path(workspace)
    if not _ping_manager(socket_path):
        return {"ok": True, "running": False, "stopped": True, "cancelled": 0}

    payload = {
        "op": "shutdown",
        "cancel_open_batches": bool(cancel_open_batches),
        "session_id": str(session_id or ""),
    }
    deadline = time.time() + max(0.0, float(wait_timeout_seconds or 0.0))
    response: dict[str, Any] = {}
    while True:
        try:
            response = _send_request(socket_path, with_runtime_host_protocol(payload), timeout=3.0)
        except Exception:
            if not _ping_manager(socket_path):
                return {"ok": True, "running": False, "stopped": True, "cancelled": 0}
            raise
        if bool(response.get("ok")):
            stopped = _wait_for_manager_stop(socket_path, timeout=max(0.0, deadline - time.time()))
            response = dict(response)
            response["running"] = True
            response["stopped"] = stopped
            return response
        error = str(response.get("error") or "")
        if cancel_open_batches or "busy" not in error or time.time() >= deadline:
            raise RuntimeHostUnavailableError(error or "runtime host shutdown failed")
        time.sleep(0.25)


def shutdown_workspace_manager(
    *,
    project_root: Path,
    workspace: Path,
    cancel_open_batches: bool = False,
    session_id: str | None = None,
    wait_timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Deprecated compatibility wrapper for ``shutdown_runtime_host``."""

    del project_root
    return shutdown_runtime_host(
        workspace=workspace,
        cancel_open_batches=cancel_open_batches,
        session_id=session_id,
        wait_timeout_seconds=wait_timeout_seconds,
    )
