"""Thin local Playwright MCP stdio runtime management."""

from __future__ import annotations

from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
import os
from pathlib import Path
import queue
import shutil
import sys
import threading
import time
from typing import Any, TextIO

import anyio
from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client


PLAYWRIGHT_MCP_PACKAGE = "@playwright/mcp@0.0.70"


def _workspace_tmp_from_output_dir(output_dir: Path) -> Path:
    parts = list(output_dir.resolve().parents)
    if len(parts) >= 3 and parts[2].name == "tmp":
        return parts[2]
    return output_dir.resolve().parent


def _cached_mcp_cli(output_dir: Path) -> Path | None:
    tmp_dir = _workspace_tmp_from_output_dir(output_dir)
    candidates = [
        tmp_dir / "npm-cache",
        tmp_dir / "npm_cache",
        tmp_dir / "home" / ".npm",
    ]
    for cache_dir in candidates:
        if not cache_dir.exists():
            continue
        matches = sorted(cache_dir.glob("_npx/*/node_modules/@playwright/mcp/cli.js"))
        if matches:
            return matches[-1].resolve()
    return None


@dataclass
class _RuntimeCommand:
    kind: str
    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    future: Future[Any] = field(default_factory=Future)


@dataclass
class PlaywrightMCPProcess:
    site_key: str
    endpoint_url: str
    log_path: Path
    profile_dir: Path
    output_dir: Path
    run_id: str
    server: StdioServerParameters
    command_timeout_seconds: float = 75.0
    _log_handle: TextIO | None = None
    _thread: threading.Thread | None = None
    _commands: queue.Queue[_RuntimeCommand] = field(default_factory=queue.Queue, repr=False)
    _ready_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _stopped_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _call_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _state_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _start_error: BaseException | None = None
    _runtime_error: BaseException | None = None
    _running: bool = False
    _stop_requested: bool = False

    def start(self) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive():
            return
        self._commands = queue.Queue()
        self._ready_event.clear()
        self._stopped_event.clear()
        self._start_error = None
        self._runtime_error = None
        self._stop_requested = False
        with self._state_lock:
            self._running = False
        self._thread = threading.Thread(
            target=self._thread_main,
            name=f"careereng-playwright-mcp-{self.site_key}",
            daemon=True,
        )
        self._thread.start()
        startup_timeout = max(10.0, float(self.command_timeout_seconds or 10.0))
        if not self._ready_event.wait(timeout=startup_timeout):
            self.stop()
            raise TimeoutError(f"playwright mcp failed to initialize within {startup_timeout:.0f}s")
        if self._start_error is not None:
            self.stop()
            raise RuntimeError(str(self._start_error)) from self._start_error
        if not self.is_running():
            error = self._runtime_error or RuntimeError("playwright mcp exited during startup")
            self.stop()
            raise RuntimeError(str(error)) from error

    def _thread_main(self) -> None:
        try:
            anyio.run(self._owner_main)
        except BaseException as exc:  # pragma: no cover - exercised via caller-visible failures
            if self._ready_event.is_set():
                self._runtime_error = exc
            else:
                self._start_error = exc
            self._fail_pending(exc)
            self._write_log_line(f"runtime_error: {exc}")
        finally:
            with self._state_lock:
                self._running = False
            self._ready_event.set()
            self._stopped_event.set()

    async def _owner_main(self) -> None:
        async with stdio_client(self.server, errlog=self._log_handle or sys.stderr) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream, read_timeout_seconds=None) as session:
                await session.initialize()
                with self._state_lock:
                    self._running = True
                self._ready_event.set()
                while True:
                    command = await anyio.to_thread.run_sync(self._commands.get)
                    if command.kind == "stop":
                        if not command.future.done():
                            command.future.set_result(None)
                        return
                    try:
                        if command.kind == "list_tools":
                            result = await session.list_tools()
                            value = list(result.tools or [])
                        elif command.kind == "call_tool":
                            result = await session.call_tool(command.name, command.arguments)
                            value = result.model_dump(mode="json")
                        else:
                            raise RuntimeError(f"unsupported runtime command: {command.kind}")
                    except BaseException as exc:
                        if not command.future.done():
                            command.future.set_exception(exc)
                    else:
                        if not command.future.done():
                            command.future.set_result(value)

    def _submit(self, command: _RuntimeCommand) -> Any:
        self._raise_if_unavailable()
        self._commands.put(command)
        try:
            return command.future.result(timeout=max(10.0, float(self.command_timeout_seconds or 10.0)))
        except FutureTimeoutError as exc:
            raise TimeoutError(f"playwright mcp command timed out: {command.kind}") from exc

    def _raise_if_unavailable(self) -> None:
        if self._start_error is not None:
            raise RuntimeError(str(self._start_error)) from self._start_error
        if self._runtime_error is not None:
            raise RuntimeError(str(self._runtime_error)) from self._runtime_error
        thread = self._thread
        if thread is None or not thread.is_alive() or not self.is_running():
            raise RuntimeError("playwright mcp runtime is not running")

    def _fail_pending(self, exc: BaseException) -> None:
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            if not command.future.done():
                command.future.set_exception(exc)

    def _write_log_line(self, text: str) -> None:
        handle = self._log_handle
        if handle is None:
            return
        try:
            handle.write(text.rstrip() + "\n")
            handle.flush()
        except Exception:
            pass

    def is_running(self) -> bool:
        with self._state_lock:
            return self._running

    def pid(self) -> int:
        return 0

    def list_tools_sync(self) -> list[types.Tool]:
        with self._call_lock:
            value = self._submit(_RuntimeCommand(kind="list_tools"))
        return list(value)

    def call_tool_sync(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = self._normalize_tool_arguments(arguments or {}, tool_name=name)
        with self._call_lock:
            value = self._submit(_RuntimeCommand(kind="call_tool", name=name, arguments=payload))
        return dict(value)

    def _normalize_tool_arguments(self, arguments: dict[str, Any], *, tool_name: str = "") -> dict[str, Any]:
        payload = dict(arguments or {})
        filename = payload.get("filename")
        if isinstance(filename, str):
            raw = filename.strip()
            if raw:
                payload["filename"] = str((self.output_dir / Path(raw).name).resolve())
            else:
                payload["filename"] = ""
        if str(tool_name or "").strip() == "browser_file_upload":
            raw_paths = payload.get("paths")
            if isinstance(raw_paths, str):
                payload["paths"] = [self._stage_upload_path(raw_paths)]
            elif isinstance(raw_paths, list):
                payload["paths"] = [self._stage_upload_path(path) for path in raw_paths]
        return payload

    def _stage_upload_path(self, value: Any) -> str:
        source = Path(str(value or "")).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"browser upload file is unavailable: {source}")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        target = (self.output_dir / source.name).resolve()
        if source != target:
            shutil.copy2(source, target)
        return str(target)

    def stop(self) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive() and not self._stop_requested:
            self._stop_requested = True
            command = _RuntimeCommand(kind="stop")
            self._commands.put(command)
            try:
                command.future.result(timeout=10.0)
            except Exception:
                pass
        if thread is not None:
            thread.join(timeout=15.0)
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except Exception:
                pass
        self._log_handle = None
        self._thread = None
        with self._state_lock:
            self._running = False


def launch_playwright_mcp(
    *,
    site_key: str,
    run_id: str,
    browser_name: str,
    headless: bool,
    profile_dir: Path,
    output_dir: Path,
    timeout_ms: int,
    executable_path: str = "",
) -> PlaywrightMCPProcess:
    profile_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / f"playwright-mcp-{run_id}.log"
    log_handle = log_path.open("a", encoding="utf-8")

    args = [
        "--output-dir",
        str(output_dir),
        "--output-mode",
        "file",
        "--snapshot-mode",
        "full",
        "--shared-browser-context",
        "--save-session",
        "--timeout-action",
        str(max(1000, int(timeout_ms or 5000))),
        "--timeout-navigation",
        str(max(30000, int(timeout_ms or 45000))),
        "--user-data-dir",
        str(profile_dir),
    ]
    executable = str(executable_path or "").strip()
    if executable:
        args.extend(["--executable-path", executable])
    else:
        args.extend(["--browser", str(browser_name or "chrome")])
    if headless:
        args.append("--headless")

    cached_cli = _cached_mcp_cli(output_dir)
    if cached_cli is not None:
        command = "node"
        server_args = [str(cached_cli), *args]
        endpoint_url = f"stdio://node/{cached_cli}"
        env = None
    else:
        command = "npx"
        server_args = [PLAYWRIGHT_MCP_PACKAGE, *args]
        endpoint_url = f"stdio://npx/{PLAYWRIGHT_MCP_PACKAGE}"
        npm_cache_dir = _workspace_tmp_from_output_dir(output_dir) / "npm-cache"
        npm_cache_dir.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "NPM_CONFIG_CACHE": str(npm_cache_dir), "npm_config_cache": str(npm_cache_dir)}

    runtime = PlaywrightMCPProcess(
        site_key=site_key,
        endpoint_url=endpoint_url,
        log_path=log_path,
        profile_dir=profile_dir,
        output_dir=output_dir,
        run_id=run_id,
        server=StdioServerParameters(command=command, args=server_args, cwd=output_dir, env=env),
        command_timeout_seconds=max(45.0, float(timeout_ms or 45000) / 1000.0 + 30.0),
        _log_handle=log_handle,
    )
    runtime.start()
    return runtime


def wait_for_process(runtime: PlaywrightMCPProcess, *, seconds: float = 2.0) -> None:
    deadline = time.monotonic() + max(0.5, float(seconds or 0.5))
    last_error = "playwright mcp did not become ready"
    while time.monotonic() < deadline:
        if runtime.is_running():
            return
        if runtime._start_error is not None:
            last_error = str(runtime._start_error)
            break
        if runtime._runtime_error is not None:
            last_error = str(runtime._runtime_error)
            break
        time.sleep(0.1)
    raise RuntimeError(f"playwright mcp exited early: {last_error} ({runtime.log_path})")
