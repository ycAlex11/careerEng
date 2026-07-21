"""Thin JSON-RPC client for a local ``codex app-server`` process.

The App Server owns Codex threads and turns.  CareerEng only translates its
site work-item lifecycle to the stable thread/turn protocol and records the
resulting identifiers.
"""

from __future__ import annotations

import json
import os
import queue
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


class CodexAppServerError(RuntimeError):
    """A local App Server transport or protocol failure."""


@dataclass(frozen=True)
class CodexAppServerEvent:
    method: str
    params: dict[str, Any]


_DESKTOP_CODEX_BINARY = Path("/Applications/ChatGPT.app/Contents/Resources/codex")


def default_app_server_command() -> tuple[str, ...]:
    """Prefer Codex Desktop's bundled App Server, with an explicit override."""
    configured = os.environ.get("CAREERENG_CODEX_APP_SERVER_COMMAND", "").strip()
    if configured:
        return tuple(shlex.split(configured))
    if _DESKTOP_CODEX_BINARY.exists():
        return (str(_DESKTOP_CODEX_BINARY), "app-server")
    return ("codex", "app-server")


DEFAULT_APP_SERVER_COMMAND = default_app_server_command()


class CodexAppServerClient:
    """Own one long-lived App Server process and its JSONL RPC connection."""

    def __init__(
        self,
        *,
        command: Iterable[str] = DEFAULT_APP_SERVER_COMMAND,
        cwd: Path,
        env: dict[str, str] | None = None,
        process_factory: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
        request_timeout_seconds: float = 30.0,
        event_callback: Callable[[CodexAppServerEvent], None] | None = None,
    ):
        self.command = tuple(str(part) for part in command)
        self.cwd = Path(cwd).resolve()
        self.env = dict(env or {})
        self.process_factory = process_factory
        self.request_timeout_seconds = max(1.0, float(request_timeout_seconds or 30.0))
        self.event_callback = event_callback
        self._process: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._write_lock = threading.Lock()
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._events: queue.Queue[CodexAppServerEvent] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._stderr: list[str] = []
        self._started = False

    @property
    def started(self) -> bool:
        return bool(self._started and self._process and self._process.poll() is None)

    def start(self) -> dict[str, Any]:
        if self.started:
            return {"already_started": True}
        if not self.command:
            raise CodexAppServerError("Codex App Server command is empty")
        try:
            self._process = self.process_factory(
                list(self.command),
                cwd=str(self.cwd),
                env={**os.environ, **self.env},
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise CodexAppServerError(f"unable to start Codex App Server: {exc}") from exc
        if self._process.stdin is None or self._process.stdout is None or self._process.stderr is None:
            self.close()
            raise CodexAppServerError("Codex App Server did not expose stdio")
        self._reader = threading.Thread(target=self._read_stdout, name="careereng-codex-rpc", daemon=True)
        self._stderr_reader = threading.Thread(target=self._read_stderr, name="careereng-codex-stderr", daemon=True)
        self._reader.start()
        self._stderr_reader.start()
        try:
            initialized = self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "careereng",
                        "title": "CareerEng",
                        "version": "0.1.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            )
            self.notify("initialized", {})
        except Exception:
            self.close()
            raise
        self._started = True
        return initialized

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdin is None:
            raise CodexAppServerError("Codex App Server is not running")
        if process.poll() is not None:
            raise CodexAppServerError(self._exit_message())
        with self._pending_lock:
            request_id = self._next_id
            self._next_id += 1
            response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[request_id] = response_queue
        try:
            self._write({"id": request_id, "method": str(method), "params": dict(params or {})})
            try:
                timeout = self.request_timeout_seconds if timeout_seconds is None else max(1.0, float(timeout_seconds))
                response = response_queue.get(timeout=timeout)
            except queue.Empty as exc:
                raise CodexAppServerError(f"Codex App Server timed out waiting for {method}") from exc
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)
        if isinstance(response.get("error"), dict):
            error = response["error"]
            raise CodexAppServerError(str(error.get("message") or error))
        result = response.get("result")
        return result if isinstance(result, dict) else {"result": result}

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._write({"method": str(method), "params": dict(params or {})})

    def start_thread(
        self,
        *,
        cwd: Path,
        approval_policy: str = "on-request",
        sandbox: str = "workspace-write",
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return self.request(
            "thread/start",
            {
                "cwd": str(Path(cwd).resolve()),
                "approvalPolicy": approval_policy,
                "sandbox": sandbox,
            },
            timeout_seconds=timeout_seconds,
        )

    def resume_thread(self, thread_id: str) -> dict[str, Any]:
        return self.request("thread/resume", {"threadId": str(thread_id)})

    def start_turn(self, *, thread_id: str, prompt: str) -> dict[str, Any]:
        return self.request(
            "turn/start",
            {
                "threadId": str(thread_id),
                "input": [{"type": "text", "text": str(prompt)}],
            },
        )

    def interrupt_turn(self, *, thread_id: str, turn_id: str) -> dict[str, Any]:
        return self.request("turn/interrupt", {"threadId": str(thread_id), "turnId": str(turn_id)})

    def drain_events(self) -> list[CodexAppServerEvent]:
        rows: list[CodexAppServerEvent] = []
        while True:
            try:
                rows.append(self._events.get_nowait())
            except queue.Empty:
                return rows

    def close(self) -> None:
        process = self._process
        self._started = False
        self._process = None
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        for pending in self._pending.values():
            try:
                pending.put_nowait({"error": {"message": "Codex App Server closed"}})
            except queue.Full:
                pass

    def _write(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise CodexAppServerError("Codex App Server is not running")
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            try:
                process.stdin.write(line + "\n")
                process.stdin.flush()
            except OSError as exc:
                raise CodexAppServerError(self._exit_message()) from exc

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            response_id = payload.get("id")
            if isinstance(response_id, int):
                with self._pending_lock:
                    response_queue = self._pending.get(response_id)
                if response_queue is not None:
                    response_queue.put(payload)
                continue
            method = str(payload.get("method") or "")
            if not method:
                continue
            params = payload.get("params")
            event = CodexAppServerEvent(method=method, params=params if isinstance(params, dict) else {})
            self._events.put(event)
            if self.event_callback is not None:
                self.event_callback(event)

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            self._stderr.append(line.rstrip())
            if len(self._stderr) > 32:
                self._stderr.pop(0)

    def _exit_message(self) -> str:
        process = self._process
        exit_code = process.poll() if process is not None else None
        details = "\n".join(self._stderr[-4:]).strip()
        message = f"Codex App Server exited ({exit_code})" if exit_code is not None else "Codex App Server is unavailable"
        return f"{message}: {details}" if details else message
