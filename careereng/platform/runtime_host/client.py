"""Client for a separately owned CareerEng runtime host."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import RuntimeHostError, RuntimeHostProtocolMismatchError, RuntimeHostUnavailableError
from .protocol import RUNTIME_HOST_PROTOCOL_VERSION, protocol_version_from, with_runtime_host_protocol
from careereng.orchestration.agent_protocol.runtime_lifecycle import RELEASE_SITE_OPERATION, release_site_payload
from .service import (
    DEFAULT_RUNTIME_HOST_REQUEST_TIMEOUT_SECONDS,
    ensure_runtime_host,
    runtime_host_socket_path,
    send_runtime_host_request,
    shutdown_runtime_host,
)


@dataclass(frozen=True)
class RuntimeHostClient:
    """Transport-only client shared by CLI, MCP, and agent adapters.

    ``autostart`` is intentionally explicit. Desktop/remote agent adapters set
    it to false so they never attempt to create a browser-owning process inside
    a constrained agent sandbox.
    """

    project_root: Path
    workspace: Path
    autostart: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", Path(self.project_root).resolve())
        object.__setattr__(self, "workspace", Path(self.workspace).resolve())

    def ping(self) -> dict[str, Any]:
        return self.request("ping", timeout=3.0)

    def request(
        self,
        operation: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = DEFAULT_RUNTIME_HOST_REQUEST_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        try:
            socket_path = ensure_runtime_host(
                project_root=self.project_root,
                workspace=self.workspace,
                autostart=self.autostart,
            )
            response = send_runtime_host_request(
                socket_path,
                with_runtime_host_protocol({"op": operation, **(payload or {})}),
                timeout=timeout,
            )
        except RuntimeHostProtocolMismatchError:
            raise
        except Exception as exc:
            raise RuntimeHostUnavailableError(
                "CareerEng runtime host is unavailable. Start it in the local user environment with "
                "`python -m careereng runtime-host serve`; do not retry browser operations in this agent process. "
                f"details={exc}"
            ) from exc
        self._validate_response(response)
        if not bool(response.get("ok")):
            raise RuntimeHostUnavailableError(str(response.get("error") or "runtime host request failed"))
        return response

    def shutdown(
        self,
        *,
        cancel_open_batches: bool = False,
        session_id: str | None = None,
        wait_timeout_seconds: float = 10.0,
    ) -> dict[str, Any]:
        try:
            return shutdown_runtime_host(
                workspace=self.workspace,
                cancel_open_batches=cancel_open_batches,
                session_id=session_id,
                wait_timeout_seconds=wait_timeout_seconds,
            )
        except RuntimeHostProtocolMismatchError:
            raise
        except Exception as exc:
            raise RuntimeHostUnavailableError(str(exc)) from exc

    def release_site(self, *, site_key: str) -> dict[str, Any]:
        """Release one retained site runtime through the shared host contract."""

        return self.request(RELEASE_SITE_OPERATION, release_site_payload(site_key=site_key))

    @staticmethod
    def _validate_response(response: dict[str, Any]) -> None:
        remote_version = protocol_version_from(response)
        if remote_version != RUNTIME_HOST_PROTOCOL_VERSION:
            rendered = remote_version or "missing"
            raise RuntimeHostProtocolMismatchError(
                "CareerEng runtime host protocol mismatch "
                f"(expected={RUNTIME_HOST_PROTOCOL_VERSION}, actual={rendered}). "
                "Reload/restart the local CareerEng MCP server and runtime host before continuing."
            )


def runtime_host_client(*, project_root: Path, workspace: Path, autostart: bool = False) -> RuntimeHostClient:
    return RuntimeHostClient(project_root=project_root, workspace=workspace, autostart=autostart)


def runtime_host_status(*, project_root: Path, workspace: Path) -> dict[str, Any]:
    """Read host health without auto-starting a process."""

    client = runtime_host_client(project_root=project_root, workspace=workspace, autostart=False)
    try:
        return {"ok": True, "running": True, "socket_path": str(runtime_host_socket_path(workspace)), "host": client.ping()}
    except RuntimeHostError as exc:
        return {
            "ok": False,
            "running": False,
            "socket_path": str(runtime_host_socket_path(workspace)),
            "error_code": exc.error_code,
            "error": str(exc),
        }
