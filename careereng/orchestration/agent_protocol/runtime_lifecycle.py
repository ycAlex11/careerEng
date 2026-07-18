"""Agent-visible contracts for generic retained-runtime lifecycle operations."""

from __future__ import annotations

from typing import Any


RELEASE_SITE_OPERATION = "release_site"


def release_site_payload(*, site_key: str) -> dict[str, str]:
    """Build the shared request payload for releasing one retained site runtime."""

    normalized_site_key = str(site_key or "").strip()
    if not normalized_site_key:
        raise ValueError("site_key is required")
    return {"site_key": normalized_site_key}


def release_site_tool_schema() -> dict[str, Any]:
    """Describe the lifecycle input shared by CLI and MCP adapters."""

    return {
        "name": RELEASE_SITE_OPERATION,
        "description": "Release one retained site browser/runtime without changing workflow state.",
        "input_schema": {
            "type": "object",
            "required": ["site_key"],
            "properties": {
                "site_key": {
                    "type": "string",
                    "description": "Registered CareerEng site key whose retained runtime should be released.",
                }
            },
            "additionalProperties": False,
        },
    }
