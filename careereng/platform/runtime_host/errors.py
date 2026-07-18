"""Runtime-host transport errors without workflow or site policy."""

from __future__ import annotations


class RuntimeHostError(RuntimeError):
    error_code = "runtime_host_error"


class RuntimeHostUnavailableError(RuntimeHostError):
    error_code = "runtime_host_unavailable"


class RuntimeHostProtocolMismatchError(RuntimeHostError):
    error_code = "runtime_host_protocol_mismatch"
