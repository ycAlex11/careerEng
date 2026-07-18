"""Workspace-scoped manager process for persistent agent state."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from careereng.adapters.external_agents.contracts import AGENT_BRIDGE_STATUS
from careereng.adapters.bootstrap import build_loop
from careereng.evolution.outer_loop import BatchEvolutionOrchestrator
from careereng.utils import make_id
from .errors import RuntimeHostProtocolMismatchError, RuntimeHostUnavailableError
from .protocol import RUNTIME_HOST_PROTOCOL_VERSION, protocol_version_from, runtime_host_identity, with_runtime_host_protocol


DEFAULT_RUNTIME_HOST_REQUEST_TIMEOUT_SECONDS = 1800.0
# Compatibility for callers that have not yet migrated their import path.
DEFAULT_MANAGER_REQUEST_TIMEOUT_SECONDS = DEFAULT_RUNTIME_HOST_REQUEST_TIMEOUT_SECONDS


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
        factory = loop_factory or build_loop
        self.loop, _ = factory(project_root=self.project_root, workspace=self.workspace)
        self._lock = threading.Lock()
        self._background_batch_running = False

    def close(self) -> None:
        closer = getattr(self.loop, "close", None)
        if callable(closer):
            closer()

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
        if op == "ping":
            return {"ok": True, "reply": "pong", **runtime_host_identity()}
        if op == "shutdown":
            return self._handle_shutdown(payload)
        if op == "start_jobs_batch":
            return self._handle_start_jobs_batch(payload)
        if op == "fresh_snapshot_resume":
            return self._handle_fresh_snapshot_resume(payload)
        if op == "pause_jobs_batch":
            return self._handle_pause_jobs_batch(payload)
        if op in {"agent_bridge_browser_list_tools", "browser_handoff_list_tools"}:
            return self._handle_agent_bridge_browser_list_tools(payload)
        if op in {"agent_bridge_browser_call_tool", "browser_handoff_call_tool"}:
            return self._handle_agent_bridge_browser_call_tool(payload)
        if op == "agent_bridge_state_list_tools":
            return self._handle_agent_bridge_state_list_tools(payload)
        if op == "agent_bridge_state_call_tool":
            return self._handle_agent_bridge_state_call_tool(payload)
        if op != "process_message":
            return {"ok": False, "error": f"unsupported op: {op}"}
        session_id = str(payload.get("session_id") or "cli:default")
        message = str(payload.get("message") or "")
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            return {"ok": False, "error": "workspace manager is busy with another operation"}
        try:
            if self._background_batch_running:
                return {"ok": False, "error": "workspace manager is busy with another job batch"}
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
        else:
            acquired = self._lock.acquire(blocking=False)
            if not acquired:
                return {"ok": False, "error": "workspace manager is busy with another job batch"}
            try:
                if self._background_batch_running:
                    return {"ok": False, "error": "workspace manager is busy with another job batch"}
            finally:
                self._lock.release()
        return {
            "ok": True,
            "shutdown": True,
            "cancelled": len(cancelled),
            "reply": "workspace manager shutting down",
        }

    def _handle_start_jobs_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = str(payload.get("session_id") or "cli:default")
        message = str(payload.get("message") or "")
        operation = str(payload.get("operation") or "job_search")
        apply_requested = bool(payload.get("apply_requested"))
        turn_id = make_id("turn")
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            return {"ok": False, "error": "workspace manager is busy with another operation"}
        try:
            if self._background_batch_running:
                return {"ok": False, "error": "workspace manager is busy with another job batch"}
            batch = self.loop.job_flow.create_batch(
                session_id=session_id,
                turn_id=turn_id,
                user_message=message,
                apply_requested=apply_requested,
                operation=operation,
            )
            if not batch:
                return {"ok": True, "accepted": False, "reply": "当前没有已注册的 active sites。请先完成公司注册。"}
            batch_id = str(batch.get("batch_id") or "")
            self._background_batch_running = True
        finally:
            self._lock.release()

        def _worker() -> None:
            with self._lock:
                try:
                    BatchEvolutionOrchestrator(self.loop.job_flow).run_batch_with_outer_loop(batch_id)
                except BaseException as exc:  # pragma: no cover - defensive manager boundary
                    try:
                        self.loop.job_flow.fail_batch(batch_id=batch_id, error=str(exc))
                    finally:
                        self._background_batch_running = False
                    return
                self._background_batch_running = False

        worker = threading.Thread(target=_worker, name=f"careereng-jobs-{batch_id}", daemon=True)
        worker.start()
        return {
            "ok": True,
            "accepted": True,
            "batch_id": batch_id,
            "turn_id": turn_id,
            "operation": operation,
            "reply": f"batch={batch_id} status=running",
        }

    def _handle_fresh_snapshot_resume(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = str(payload.get("session_id") or "cli:default")
        message = str(payload.get("message") or "")
        turn_id = str(payload.get("turn_id") or make_id("turn"))
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            return {"ok": False, "error": "workspace manager is busy with another operation"}
        try:
            if self._background_batch_running:
                return {"ok": False, "error": "workspace manager is busy with another job batch"}
            reply = self.loop.job_flow.handle_resume_message(
                session_id=session_id,
                message=message,
                turn_id=turn_id,
            )
        finally:
            self._lock.release()
        if reply is None:
            return {"ok": True, "accepted": False, "reply": ""}
        return {"ok": True, "accepted": True, "reply": reply, "turn_id": turn_id}

    def _handle_pause_jobs_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        batch_id = str(payload.get("batch_id") or "").strip()
        site_key = str(payload.get("site_key") or "").strip()
        if not batch_id:
            return {"ok": False, "error": "batch_id is required"}
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            return {"ok": False, "error": "workspace manager is busy with another operation"}
        try:
            if self._background_batch_running:
                return {"ok": False, "error": "workspace manager is busy with another job batch"}
            batch = self.loop.job_flow.pause_batch(batch_id=batch_id, site_key=site_key)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            self._lock.release()
        return {"ok": True, "accepted": True, "batch": batch}

    def _handle_agent_bridge_browser_list_tools(self, payload: dict[str, Any]) -> dict[str, Any]:
        site_key = str(payload.get("site_key") or "").strip()
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            return {"ok": False, "error": "workspace manager is busy with another operation"}
        try:
            if self._background_batch_running:
                return {"ok": False, "error": "workspace manager is busy with another job batch"}
            browser_runner = getattr(self.loop, "browser_runner", None)
            list_tools = getattr(browser_runner, "list_active_browser_tools", None)
            if not callable(list_tools):
                return {"ok": False, "error": "agent bridge browser tool listing is unavailable"}
            tools = list_tools(site_key)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            self._lock.release()
        return {"ok": True, "site_key": site_key, "tools": tools}

    def _handle_agent_bridge_browser_call_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        site_key = str(payload.get("site_key") or "").strip()
        tool_name = str(payload.get("tool_name") or "").strip()
        arguments = payload.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        turn_id = str(payload.get("turn_id") or "").strip()
        phase = str(payload.get("phase") or AGENT_BRIDGE_STATUS).strip() or AGENT_BRIDGE_STATUS
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            return {"ok": False, "error": "workspace manager is busy with another operation"}
        try:
            if self._background_batch_running:
                return {"ok": False, "error": "workspace manager is busy with another job batch"}
            browser_runner = getattr(self.loop, "browser_runner", None)
            call_tool = getattr(browser_runner, "call_active_browser_tool", None)
            if not callable(call_tool):
                return {"ok": False, "error": "agent bridge browser tool call is unavailable"}
            result = call_tool(
                site_key=site_key,
                tool_name=tool_name,
                arguments=arguments,
                turn_id=turn_id,
                phase=phase,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            self._lock.release()
        return {"ok": True, "site_key": site_key, "result": result}

    def _handle_agent_bridge_state_list_tools(self, payload: dict[str, Any]) -> dict[str, Any]:
        site_key = str(payload.get("site_key") or "").strip()
        phase = str(payload.get("phase") or "").strip()
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            return {"ok": False, "error": "workspace manager is busy with another operation"}
        try:
            if self._background_batch_running:
                return {"ok": False, "error": "workspace manager is busy with another job batch"}
            browser_runner = getattr(self.loop, "browser_runner", None)
            list_tools = getattr(browser_runner, "list_active_state_tools", None)
            if not callable(list_tools):
                return {"ok": False, "error": "agent bridge state tool listing is unavailable"}
            tools = list_tools(site_key, phase=phase)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            self._lock.release()
        return {"ok": True, "site_key": site_key, "phase": phase, "tools": tools}

    def _handle_agent_bridge_state_call_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        site_key = str(payload.get("site_key") or "").strip()
        tool_name = str(payload.get("tool_name") or "").strip()
        arguments = payload.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        turn_id = str(payload.get("turn_id") or "").strip()
        phase = str(payload.get("phase") or "").strip()
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            return {"ok": False, "error": "workspace manager is busy with another operation"}
        try:
            if self._background_batch_running:
                return {"ok": False, "error": "workspace manager is busy with another job batch"}
            browser_runner = getattr(self.loop, "browser_runner", None)
            call_tool = getattr(browser_runner, "call_active_state_tool", None)
            if not callable(call_tool):
                return {"ok": False, "error": "agent bridge state tool call is unavailable"}
            result = call_tool(
                site_key=site_key,
                tool_name=tool_name,
                arguments=arguments,
                turn_id=turn_id,
                phase=phase,
            )
            progression = result.get("progression") if isinstance(result, dict) else {}
            completion = progression.get("completion") if isinstance(progression, dict) else {}
            if (
                isinstance(completion, dict)
                and str(completion.get("kind") or "") == "phase_sequence_completion"
                and str(completion.get("batch_id") or "").strip()
            ):
                continue_sequence = getattr(getattr(self.loop, "job_flow", None), "continue_external_phase_sequence", None)
                if callable(continue_sequence):
                    result = {
                        **result,
                        "workflow_continuation": continue_sequence(
                            site_key=str(completion.get("site_key") or site_key),
                            batch_id=str(completion.get("batch_id") or ""),
                            terminal_phase=str(completion.get("terminal_phase") or ""),
                            session_id=str(completion.get("session_id") or ""),
                            turn_id=str(completion.get("turn_id") or turn_id),
                        ),
                    }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            self._lock.release()
        return {"ok": True, "site_key": site_key, "result": result}


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
            response = self.server.manager.handle_request(payload)
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
        # A stale local socket may be removed, but a live incompatible host must
        # never be unlinked from under another process.
        try:
            socket_file.unlink()
        except FileNotFoundError:
            pass
    runtime_host = RuntimeHostService(project_root=project_root, workspace=workspace)
    server = _UnixRuntimeHostServer(str(socket_file), runtime_host)
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
) -> dict[str, Any]:
    socket_path = ensure_workspace_manager(project_root=project_root, workspace=workspace)
    response = _send_request(
        socket_path,
        {
            "op": "fresh_snapshot_resume",
            "session_id": session_id,
            "message": message,
            "turn_id": turn_id,
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
