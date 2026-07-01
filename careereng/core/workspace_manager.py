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
from typing import Any

from careereng.core.runtime import build_loop
from careereng.evolution.outer_loop import BatchEvolutionOrchestrator
from careereng.utils import make_id


DEFAULT_MANAGER_REQUEST_TIMEOUT_SECONDS = 1800.0


def manager_socket_path(workspace: Path) -> Path:
    digest = hashlib.sha1(str(workspace.resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"careereng-manager-{digest}.sock"


class WorkspaceManager:
    def __init__(self, *, project_root: Path, workspace: Path):
        self.project_root = Path(project_root).resolve()
        self.workspace = Path(workspace).resolve()
        self.loop, _ = build_loop(project_root=self.project_root, workspace=self.workspace)
        self._lock = threading.Lock()
        self._background_batch_running = False

    def close(self) -> None:
        closer = getattr(self.loop, "close", None)
        if callable(closer):
            closer()

    def handle_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        op = str(payload.get("op") or "process_message")
        if op == "ping":
            return {"ok": True, "reply": "pong"}
        if op == "shutdown":
            return self._handle_shutdown(payload)
        if op == "start_jobs_batch":
            return self._handle_start_jobs_batch(payload)
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
        self.wfile.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
        self.wfile.flush()
        if bool(response.get("shutdown")):
            threading.Thread(
                target=self.server.shutdown,
                name="careereng-manager-shutdown",
                daemon=True,
            ).start()


class _UnixManagerServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, socket_path: str, manager: WorkspaceManager):
        self.manager = manager
        super().__init__(socket_path, _ManagerRequestHandler)


def serve_workspace_manager(*, project_root: Path, workspace: Path, socket_path: Path) -> None:
    socket_file = Path(socket_path)
    socket_file.parent.mkdir(parents=True, exist_ok=True)
    if socket_file.exists():
        try:
            socket_file.unlink()
        except FileNotFoundError:
            pass
    manager = WorkspaceManager(project_root=project_root, workspace=workspace)
    server = _UnixManagerServer(str(socket_file), manager)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        manager.close()
        try:
            socket_file.unlink()
        except FileNotFoundError:
            pass


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
        raise RuntimeError("workspace manager returned an empty response")
    return json.loads(text)


def _ping_manager(socket_path: Path) -> bool:
    if not socket_path.exists():
        return False
    try:
        response = _send_request(socket_path, {"op": "ping"}, timeout=0.8)
    except Exception:
        return False
    return bool(response.get("ok")) and str(response.get("reply") or "") == "pong"


def _wait_for_manager_stop(socket_path: Path, *, timeout: float) -> bool:
    deadline = time.time() + max(0.0, float(timeout or 0.0))
    while time.time() < deadline:
        if not _ping_manager(socket_path):
            return True
        time.sleep(0.1)
    return not _ping_manager(socket_path)


def ensure_workspace_manager(*, project_root: Path, workspace: Path) -> Path:
    socket_path = manager_socket_path(workspace)
    if _ping_manager(socket_path):
        return socket_path
    if socket_path.exists():
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass
    cmd = [
        sys.executable,
        "-m",
        "careereng",
        "manager-serve",
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
        if _ping_manager(socket_path):
            return socket_path
        time.sleep(0.1)
    raise RuntimeError(f"workspace manager did not start: {socket_path}")


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


def shutdown_workspace_manager(
    *,
    project_root: Path,
    workspace: Path,
    cancel_open_batches: bool = False,
    session_id: str | None = None,
    wait_timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    del project_root  # Kept for call-site symmetry with other manager helpers.
    socket_path = manager_socket_path(workspace)
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
            response = _send_request(socket_path, payload, timeout=3.0)
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
            raise RuntimeError(error or "workspace manager shutdown failed")
        time.sleep(0.25)
