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
    request_id: int | str | None = None


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
        trace_callback: Callable[[dict[str, Any]], None] | None = None,
        server_request_handler: Callable[[str, dict[str, Any]], dict[str, Any] | None] | None = None,
    ):
        self.command = tuple(str(part) for part in command)
        self.cwd = Path(cwd).resolve()
        self.env = dict(env or {})
        self.process_factory = process_factory
        self.request_timeout_seconds = max(1.0, float(request_timeout_seconds or 30.0))
        self.event_callback = event_callback
        self.trace_callback = trace_callback
        self.server_request_handler = server_request_handler
        self._process: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._write_lock = threading.Lock()
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._events: queue.Queue[CodexAppServerEvent] = queue.Queue()
        self._callback_queue: queue.Queue[tuple[str, Any] | None] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._callback_dispatcher: threading.Thread | None = None
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
        self._start_callback_dispatcher()
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
            self._trace("process_start_error", error=f"{type(exc).__name__}: {exc}")
            raise CodexAppServerError(f"unable to start Codex App Server: {exc}") from exc
        if self._process.stdin is None or self._process.stdout is None or self._process.stderr is None:
            self.close()
            raise CodexAppServerError("Codex App Server did not expose stdio")
        self._reader = threading.Thread(target=self._read_stdout, name="careereng-codex-rpc", daemon=True)
        self._stderr_reader = threading.Thread(target=self._read_stderr, name="careereng-codex-stderr", daemon=True)
        self._reader.start()
        self._stderr_reader.start()
        self._trace("process_started", command=list(self.command), cwd=str(self.cwd))
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
        request_params = dict(params or {})
        with self._pending_lock:
            request_id = self._next_id
            self._next_id += 1
            response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[request_id] = response_queue
        try:
            self._trace("rpc_request", request_id=request_id, method=str(method), params=request_params)
            self._write({"id": request_id, "method": str(method), "params": request_params})
            try:
                timeout = self.request_timeout_seconds if timeout_seconds is None else max(1.0, float(timeout_seconds))
                response = response_queue.get(timeout=timeout)
            except queue.Empty as exc:
                self._trace("rpc_timeout", request_id=request_id, method=str(method), timeout_seconds=timeout)
                raise CodexAppServerError(f"Codex App Server timed out waiting for {method}") from exc
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)
        if isinstance(response.get("error"), dict):
            error = response["error"]
            self._trace("rpc_error", request_id=request_id, method=str(method), error=error)
            raise CodexAppServerError(str(error.get("message") or error))
        result = response.get("result")
        self._trace("rpc_response", request_id=request_id, method=str(method), result=result)
        return result if isinstance(result, dict) else {"result": result}

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._trace("rpc_notification_sent", method=str(method), params=dict(params or {}))
        self._write({"method": str(method), "params": dict(params or {})})

    def start_thread(
        self,
        *,
        cwd: Path,
        approval_policy: str = "never",
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

    def set_trace_callback(self, callback: Callable[[dict[str, Any]], None] | None) -> None:
        self.trace_callback = callback

    def set_server_request_handler(
        self,
        callback: Callable[[str, dict[str, Any]], dict[str, Any] | None] | None,
    ) -> None:
        """Install the narrow host-side responder for App Server requests."""

        self.server_request_handler = callback

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
        self._trace("process_close_requested")
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
        self._trace("process_closed", returncode=process.poll())
        for pending in self._pending.values():
            try:
                pending.put_nowait({"error": {"message": "Codex App Server closed"}})
            except queue.Full:
                pass
        self._callback_queue.put(None)

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
                self._trace("stdio_write_error", error=f"{type(exc).__name__}: {exc}")
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
            method = str(payload.get("method") or "")
            response_id = payload.get("id")
            if method:
                params = payload.get("params")
                event = CodexAppServerEvent(
                    method=method,
                    params=params if isinstance(params, dict) else {},
                    request_id=response_id if isinstance(response_id, (int, str)) else None,
                )
                is_server_request = event.request_id is not None
                self._trace(
                    "rpc_server_request_received" if is_server_request else "rpc_notification_received",
                    request_id=event.request_id,
                    method=method,
                    params=event.params,
                )
                self._events.put(event)
                self._queue_callback("event", event)
                if is_server_request:
                    self._respond_to_server_request_async(event)
                continue
            if isinstance(response_id, int):
                with self._pending_lock:
                    response_queue = self._pending.get(response_id)
                if response_queue is not None:
                    response_queue.put(payload)
        self._trace("stdout_closed", returncode=process.poll())

    def _respond_to_server_request_async(self, event: CodexAppServerEvent) -> None:
        """Resolve an allowed server request without blocking stdout response IO."""

        handler = self.server_request_handler
        if handler is None or event.request_id is None:
            return

        def _respond() -> None:
            try:
                result = handler(event.method, dict(event.params))
            except Exception as exc:  # pragma: no cover - defensive transport boundary
                self._trace(
                    "rpc_server_request_handler_error",
                    request_id=event.request_id,
                    method=event.method,
                    error=f"{type(exc).__name__}: {exc}",
                )
                return
            if result is None:
                self._trace(
                    "rpc_server_request_deferred",
                    request_id=event.request_id,
                    method=event.method,
                )
                return
            try:
                self._write({"id": event.request_id, "result": dict(result)})
            except Exception as exc:  # pragma: no cover - process may exit while responding
                self._trace(
                    "rpc_server_request_response_error",
                    request_id=event.request_id,
                    method=event.method,
                    error=f"{type(exc).__name__}: {exc}",
                )
                return
            self._trace(
                "rpc_server_request_responded",
                request_id=event.request_id,
                method=event.method,
                result=dict(result),
            )

        threading.Thread(
            target=_respond,
            name="careereng-codex-server-request",
            daemon=True,
        ).start()

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            message = line.rstrip()
            self._stderr.append(message)
            self._trace("stderr", message=message)
            if len(self._stderr) > 32:
                self._stderr.pop(0)

    def _trace(self, event: str, **payload: Any) -> None:
        self._queue_callback("trace", {"event": str(event), **payload})

    def _start_callback_dispatcher(self) -> None:
        dispatcher = self._callback_dispatcher
        if dispatcher is not None and dispatcher.is_alive():
            return
        self._callback_dispatcher = threading.Thread(
            target=self._dispatch_callbacks,
            name="careereng-codex-callbacks",
            daemon=True,
        )
        self._callback_dispatcher.start()

    def _queue_callback(self, kind: str, payload: Any) -> None:
        """Never let an observer block the stdout reader or an RPC response."""

        self._callback_queue.put((str(kind), payload))

    def _dispatch_callbacks(self) -> None:
        while True:
            item = self._callback_queue.get()
            if item is None:
                return
            kind, payload = item
            try:
                if kind == "event" and self.event_callback is not None:
                    self.event_callback(payload)
                elif kind == "trace" and self.trace_callback is not None:
                    self.trace_callback(payload)
            except Exception:
                # Observability and lifecycle consumers cannot break RPC IO.
                pass

    def _exit_message(self) -> str:
        process = self._process
        exit_code = process.poll() if process is not None else None
        details = "\n".join(self._stderr[-4:]).strip()
        message = f"Codex App Server exited ({exit_code})" if exit_code is not None else "Codex App Server is unavailable"
        return f"{message}: {details}" if details else message
