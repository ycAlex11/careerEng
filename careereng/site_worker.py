"""Per-site browser worker for isolated Playwright runtime ownership."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from careereng.config.loader import load_config
from careereng.storage.site_store import SiteStore
from careereng.tools.playwright_tools import PlaywrightSessionOpenError, PlaywrightTools


DEFAULT_SITE_WORKER_REQUEST_TIMEOUT_SECONDS = 600.0


def site_worker_socket_path(workspace: Path, site_key: str) -> Path:
    digest = hashlib.sha1(f"{workspace.resolve()}::{site_key}".encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"careereng-site-worker-{digest}.sock"


class SiteWorkerSession:
    def __init__(self, *, project_root: Path, workspace: Path, site_key: str):
        self.project_root = Path(project_root).resolve()
        self.workspace = Path(workspace).resolve()
        self.site_key = str(site_key)
        config = load_config(self.project_root)
        self.playwright = PlaywrightTools(
            headless=config.browser.headless,
            keep_open=config.browser.keep_open,
            timeout_ms=config.browser.timeout_ms,
            slow_mo_ms=config.browser.slow_mo_ms,
        )
        self.site_store = SiteStore(self.workspace)
        self._run_session = None
        self._current_headless: bool | None = None
        self._target_url = ""

    def close(self) -> None:
        self._close_session()

    def _close_session(self) -> None:
        if self._run_session is None:
            return
        close = getattr(self._run_session, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        self._run_session = None
        self._current_headless = None
        self._target_url = ""

    def _session_payload(self) -> dict[str, Any]:
        return self.site_store.ensure_browser_session(self.site_key)

    def _session_alive_locked(self) -> bool:
        if self._run_session is None:
            return False
        alive = getattr(self._run_session, "is_alive", None)
        if callable(alive):
            try:
                return bool(alive())
            except Exception:
                return False
        return False

    def handle_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        op = str(payload.get("op") or "")
        if op == "ping":
            return {"ok": True, "reply": "pong"}
        if op == "is_alive":
            alive = self._session_alive_locked()
            if not alive:
                self._close_session()
            return {"ok": True, "alive": alive}
        if op == "close_session":
            self._close_session()
            return {"ok": True}
        if op == "open_session":
            requested_headless = payload.get("headless")
            normalized_headless = None if requested_headless is None else bool(requested_headless)
            target_url = str(payload.get("target_url") or "")
            if self._session_alive_locked() and self._current_headless == normalized_headless:
                self._target_url = target_url or self._target_url
                return {"ok": True, "reused": True}
            self._close_session()
            session = self._session_payload()
            profile_dir = str(session.get("profile_dir") or "")
            try:
                self._run_session = self.playwright.open_site_session(
                    profile_dir=profile_dir,
                    target_url=target_url,
                    headless=normalized_headless,
                    allow_launch=True,
                )
            except PlaywrightSessionOpenError as exc:
                self._run_session = None
                return {
                    "ok": False,
                    "status": exc.status,
                    "message": exc.message,
                    "detail": exc.detail,
                }
            self._current_headless = normalized_headless
            self._target_url = target_url
            return {"ok": True}

        if not self._session_alive_locked():
            self._close_session()
            return {"ok": False, "status": "browser_closed", "message": "site browser session is not running"}

        if op == "prepare_session":
            return self._run_session.prepare_session(
                str(payload.get("url") or self._target_url or ""),
                signal_config=payload.get("signal_config"),
                auto_login_config=payload.get("auto_login_config"),
            )
        if op == "discover_jobs_guided":
            return self._run_session.discover_jobs_guided(
                str(payload.get("url") or self._target_url or ""),
                guidance_text=str(payload.get("guidance_text") or ""),
                signal_config=payload.get("signal_config"),
                auto_login_config=payload.get("auto_login_config"),
                max_items=int(payload.get("max_items") or 20),
            )
        if op == "quick_apply":
            return self._run_session.quick_apply(str(payload.get("url") or self._target_url or ""))
        if op == "inspect_authenticated":
            return self._run_session.inspect_authenticated(
                str(payload.get("url") or self._target_url or ""),
                signal_config=payload.get("signal_config"),
            )
        return {"ok": False, "error": f"unsupported op: {op}"}


def _read_request(connection: socket.socket) -> dict[str, Any]:
    data = b""
    while True:
        chunk = connection.recv(65536)
        if not chunk:
            break
        data += chunk
        if b"\n" in chunk:
            break
    text = data.decode("utf-8").strip()
    if not text:
        raise RuntimeError("empty worker request")
    return json.loads(text)


def _write_response(connection: socket.socket, payload: dict[str, Any]) -> None:
    connection.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))


def serve_site_worker(*, project_root: Path, workspace: Path, site_key: str, socket_path: Path) -> None:
    socket_file = Path(socket_path)
    socket_file.parent.mkdir(parents=True, exist_ok=True)
    if socket_file.exists():
        try:
            socket_file.unlink()
        except FileNotFoundError:
            pass
    worker = SiteWorkerSession(project_root=project_root, workspace=workspace, site_key=site_key)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_file))
    server.listen(16)
    try:
        while True:
            connection, _ = server.accept()
            with connection:
                try:
                    payload = _read_request(connection)
                except Exception as exc:
                    response = {"ok": False, "error": f"invalid request: {exc}"}
                else:
                    response = worker.handle_request(payload)
                _write_response(connection, response)
    finally:
        server.close()
        worker.close()
        try:
            socket_file.unlink()
        except FileNotFoundError:
            pass


def _send_request(socket_path: Path, payload: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
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
        raise RuntimeError("site worker returned an empty response")
    return json.loads(text)


def _ping_worker(socket_path: Path) -> bool:
    if not socket_path.exists():
        return False
    try:
        response = _send_request(socket_path, {"op": "ping"}, timeout=0.8)
    except Exception:
        return False
    return bool(response.get("ok")) and str(response.get("reply") or "") == "pong"


def ensure_site_worker(*, project_root: Path, workspace: Path, site_key: str) -> Path:
    socket_path = site_worker_socket_path(workspace, site_key)
    if _ping_worker(socket_path):
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
        "site-worker-serve",
        "--project-root",
        str(project_root),
        "--workspace",
        str(workspace),
        "--site-key",
        str(site_key),
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
        if _ping_worker(socket_path):
            return socket_path
        time.sleep(0.1)
    raise RuntimeError(f"site worker did not start: {socket_path}")


class RemoteRunSession:
    def __init__(self, *, socket_path: Path):
        self.socket_path = Path(socket_path)

    def _request(self, payload: dict[str, Any], *, timeout: float = DEFAULT_SITE_WORKER_REQUEST_TIMEOUT_SECONDS) -> dict[str, Any]:
        return _send_request(self.socket_path, payload, timeout=timeout)

    def is_alive(self) -> bool:
        try:
            response = self._request({"op": "is_alive"}, timeout=2.0)
        except Exception:
            return False
        return bool(response.get("ok")) and bool(response.get("alive"))

    def prepare_session(self, url: str, signal_config=None, auto_login_config=None) -> dict[str, Any]:
        return self._request(
            {
                "op": "prepare_session",
                "url": url,
                "signal_config": signal_config,
                "auto_login_config": auto_login_config,
            }
        )

    def discover_jobs_guided(self, url: str, guidance_text="", signal_config=None, auto_login_config=None, max_items: int = 20) -> dict[str, Any]:
        return self._request(
            {
                "op": "discover_jobs_guided",
                "url": url,
                "guidance_text": guidance_text,
                "signal_config": signal_config,
                "auto_login_config": auto_login_config,
                "max_items": int(max_items or 20),
            }
        )

    def quick_apply(self, url: str) -> dict[str, Any]:
        return self._request({"op": "quick_apply", "url": url})

    def inspect_authenticated(self, url: str, signal_config=None) -> dict[str, Any]:
        return self._request({"op": "inspect_authenticated", "url": url, "signal_config": signal_config})

    def close(self) -> None:
        try:
            self._request({"op": "close_session"}, timeout=5.0)
        except Exception:
            pass


def open_remote_site_session(
    *,
    project_root: Path,
    workspace: Path,
    site_key: str,
    target_url: str = "",
    headless: bool | None = None,
) -> RemoteRunSession | dict[str, Any]:
    socket_path = ensure_site_worker(project_root=project_root, workspace=workspace, site_key=site_key)
    response = _send_request(
        socket_path,
        {
            "op": "open_session",
            "target_url": str(target_url or ""),
            "headless": headless,
        },
        timeout=DEFAULT_SITE_WORKER_REQUEST_TIMEOUT_SECONDS,
    )
    if not bool(response.get("ok")):
        return {
            "ok": False,
            "status": str(response.get("status") or "session_open_failed"),
            "message": str(response.get("message") or ""),
            "detail": response.get("detail") if isinstance(response.get("detail"), dict) else {},
            "target_url": str(target_url or ""),
        }
    return RemoteRunSession(socket_path=socket_path)
