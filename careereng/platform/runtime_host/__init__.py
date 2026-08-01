"""Workspace-scoped local runtime host for browser and phase execution."""

from .client import RuntimeHostClient, runtime_host_client, runtime_host_status
from .errors import RuntimeHostAccessDeniedError, RuntimeHostError, RuntimeHostProtocolMismatchError, RuntimeHostUnavailableError
from .protocol import RUNTIME_HOST_PROTOCOL_VERSION
from .service import (
    RuntimeHostService,
    ensure_runtime_host,
    runtime_host_socket_path,
    serve_runtime_host,
)

__all__ = [
    "RuntimeHostClient",
    "RuntimeHostAccessDeniedError",
    "RuntimeHostError",
    "RuntimeHostProtocolMismatchError",
    "RuntimeHostService",
    "RuntimeHostUnavailableError",
    "RUNTIME_HOST_PROTOCOL_VERSION",
    "ensure_runtime_host",
    "runtime_host_client",
    "runtime_host_socket_path",
    "runtime_host_status",
    "serve_runtime_host",
]
