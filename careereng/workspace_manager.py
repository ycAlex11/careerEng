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

from careereng.runtime import build_loop


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

    def close(self) -> None:
        closer = getattr(self.loop, "close", None)
        if callable(closer):
            closer()

    def handle_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        op = str(payload.get("op") or "process_message")
        if op == "ping":
            return {"ok": True, "reply": "pong"}
        if op != "process_message":
            return {"ok": False, "error": f"unsupported op: {op}"}
        session_id = str(payload.get("session_id") or "cli:default")
        message = str(payload.get("message") or "")
        with self._lock:
            reply = self.loop.process_message(session_id, message)
        return {"ok": True, "reply": reply}


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
