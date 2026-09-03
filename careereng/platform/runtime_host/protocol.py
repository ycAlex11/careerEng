"""Versioned transport contract for the workspace-scoped runtime host."""

from __future__ import annotations

from typing import Any


RUNTIME_HOST_PROTOCOL_VERSION = "2026-09-03.2"
RUNTIME_HOST_PROTOCOL_FIELD = "runtime_host_protocol_version"
RUNTIME_HOST_NAME = "careereng_runtime_host"


def with_runtime_host_protocol(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Attach the caller protocol version without changing the request shape."""

    enriched = dict(payload or {})
    enriched[RUNTIME_HOST_PROTOCOL_FIELD] = RUNTIME_HOST_PROTOCOL_VERSION
    return enriched


def runtime_host_identity() -> dict[str, str]:
    return {
        "runtime_host": RUNTIME_HOST_NAME,
        RUNTIME_HOST_PROTOCOL_FIELD: RUNTIME_HOST_PROTOCOL_VERSION,
    }


def protocol_version_from(payload: dict[str, Any] | None) -> str:
    return str((payload or {}).get(RUNTIME_HOST_PROTOCOL_FIELD) or "").strip()
