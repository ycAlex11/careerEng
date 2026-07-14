"""Thin async bridge over the local Playwright MCP runtime."""

from __future__ import annotations

from contextlib import asynccontextmanager
import json
import re
from typing import Any

import anyio
from mcp import ClientSession, types

from careereng.browser_controls.backends.playwright_mcp import PlaywrightMCPProcess


class MCPToolBridge:
    _SUMMARY_MAX_CHARS = 1200
    _CONTEXT_MAX_CHARS = 12000
    _FEEDBACK_MAX_CHARS = 14000
    _EXTREME_LINE_MAX_CHARS = 4000
    _CAP_MARKER = "\n...[truncated]...\n"

    def __init__(self, runtime: PlaywrightMCPProcess | str, *, timeout_seconds: float = 30.0):
        self.runtime = (
            runtime
            if isinstance(runtime, PlaywrightMCPProcess)
            or (
                hasattr(runtime, "is_running")
                and hasattr(runtime, "list_tools_sync")
                and hasattr(runtime, "call_tool_sync")
            )
            else None
        )
        endpoint = getattr(self.runtime, "endpoint_url", "") if self.runtime is not None else ""
        self.endpoint_url = str(endpoint or runtime).rstrip("/")
        self.timeout_seconds = max(5.0, float(timeout_seconds or 30.0))

    @asynccontextmanager
    async def open_session(self):
        if self.runtime is None:
            raise RuntimeError("Playwright MCP bridge requires a local stdio runtime")
        if not self.runtime.is_running():
            raise RuntimeError(f"playwright mcp runtime is not running: {self.endpoint_url}")
        yield self.runtime

    async def list_tools(self, session: ClientSession) -> list[types.Tool]:
        if self.runtime is not None:
            return await anyio.to_thread.run_sync(self.runtime.list_tools_sync)
        result = await session.list_tools()
        return list(result.tools or [])

    async def call_tool(self, session: ClientSession, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.runtime is not None:
            return await anyio.to_thread.run_sync(self.runtime.call_tool_sync, name, arguments or {})
        result = await session.call_tool(name, arguments or {})
        return result.model_dump(mode="json")

    @staticmethod
    def _normalize_parameters_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(schema, dict):
            return {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            }
        normalized = dict(schema)
        properties = normalized.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        normalized["type"] = "object"
        normalized["properties"] = properties
        required = normalized.get("required")
        if not isinstance(required, list):
            required = []
        required_names = [str(name) for name in required if isinstance(name, str)]
        for key in properties.keys():
            if key not in required_names:
                required_names.append(str(key))
        normalized["required"] = required_names
        if "additionalProperties" not in normalized:
            normalized["additionalProperties"] = False
        return normalized

    @staticmethod
    def tool_to_function_schema(tool: types.Tool) -> dict[str, Any]:
        schema = MCPToolBridge._normalize_parameters_schema(
            tool.inputSchema if isinstance(tool.inputSchema, dict) else None
        )
        return {
            "type": "function",
            "name": tool.name,
            "description": str(tool.description or tool.title or f"Playwright MCP tool {tool.name}"),
            "parameters": schema,
        }

    async def wait_until_ready(self, *, seconds: float = 20.0, poll_interval: float = 0.25) -> list[types.Tool]:
        deadline = anyio.current_time() + max(1.0, float(seconds or 1.0))
        last_error = "mcp endpoint not ready"
        while anyio.current_time() < deadline:
            try:
                async with self.open_session() as session:
                    return await self.list_tools(session)
            except Exception as exc:
                last_error = self._format_exception(exc)
                await anyio.sleep(max(0.05, float(poll_interval or 0.05)))
        raise RuntimeError(last_error)

    @staticmethod
    def _format_exception(exc: BaseException) -> str:
        parts: list[str] = []

        def walk(err: BaseException) -> None:
            children = getattr(err, "exceptions", None)
            if isinstance(children, tuple) and children:
                for child in children:
                    if isinstance(child, BaseException):
                        walk(child)
                return
            text = str(err).strip()
            if not text:
                text = err.__class__.__name__
            else:
                text = f"{err.__class__.__name__}: {text}"
            parts.append(text)

        walk(exc)
        deduped: list[str] = []
        seen: set[str] = set()
        for item in parts:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return " | ".join(deduped)[:2000] if deduped else exc.__class__.__name__

    @staticmethod
    def _extract_text_blocks(payload: dict[str, Any]) -> list[str]:
        if not isinstance(payload, dict):
            return []
        parts: list[str] = []
        content = payload.get("content")
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return parts

    @staticmethod
    def _ignore_match(line: str, ignore_phrases: tuple[str, ...] | list[str] | None = None) -> bool:
        lowered = str(line or "").strip().lower()
        if not lowered:
            return False
        for phrase in ignore_phrases or ():
            text = str(phrase or "").strip().lower()
            if text and text in lowered:
                return True
        return False

    @classmethod
    def _is_snapshot_wrapper(cls, line: str) -> bool:
        stripped = str(line or "").strip()
        return stripped.startswith("- [Screenshot") or stripped.startswith("- [Snapshot")

    @classmethod
    def _is_binary_blob_line(cls, line: str) -> bool:
        stripped = str(line or "").strip()
        lowered = stripped.lower()
        if not stripped:
            return False
        if "mimetype" in lowered or '"data":' in lowered or "base64," in lowered:
            return True
        compact = stripped.replace(" ", "")
        return bool(re.fullmatch(r"[A-Za-z0-9+/=]{1024,}", compact))

    @classmethod
    def _clean_line(
        cls,
        line: str,
        *,
        ignore_phrases: tuple[str, ...] | list[str] | None = None,
    ) -> str:
        stripped = str(line or "").strip()
        if not stripped:
            return ""
        if stripped.startswith("```"):
            return ""
        if cls._ignore_match(stripped, ignore_phrases=ignore_phrases):
            return ""
        if cls._is_snapshot_wrapper(stripped):
            return ""
        if cls._is_binary_blob_line(stripped):
            return ""
        if len(stripped) > cls._EXTREME_LINE_MAX_CHARS:
            stripped = stripped[: cls._EXTREME_LINE_MAX_CHARS].rstrip() + "..."
        return stripped

    @classmethod
    def _mechanically_clean_line(cls, line: str) -> str:
        stripped = str(line or "").strip()
        if not stripped:
            return ""
        if stripped.startswith("```"):
            return ""
        if cls._is_snapshot_wrapper(stripped):
            return ""
        if cls._is_binary_blob_line(stripped):
            return ""
        return stripped

    @classmethod
    def _clean_text(
        cls,
        text: str,
        *,
        ignore_phrases: tuple[str, ...] | list[str] | None = None,
    ) -> str:
        lines: list[str] = []
        for raw_line in str(text or "").splitlines():
            stripped = cls._clean_line(raw_line, ignore_phrases=ignore_phrases)
            if not stripped:
                continue
            lines.append(stripped)
        return "\n".join(lines).strip()

    @classmethod
    def live_page_text(cls, payload: dict[str, Any]) -> str:
        blocks = cls._extract_text_blocks(payload)
        if not blocks:
            return ""
        lines: list[str] = []
        for raw_line in "\n".join(blocks).splitlines():
            stripped = cls._mechanically_clean_line(raw_line)
            if not stripped:
                continue
            lines.append(stripped)
        return "\n".join(lines).strip()

    @classmethod
    def _cap_text(cls, text: str, *, max_chars: int) -> str:
        content = str(text or "").strip()
        if not content:
            return ""
        limit = max(512, int(max_chars or 0))
        if len(content) <= limit:
            return content
        marker = cls._CAP_MARKER
        if limit <= len(marker) + 32:
            return content[:limit]
        return content[: limit - len(marker)].rstrip() + marker

    @classmethod
    def clean_page_text(
        cls,
        payload: dict[str, Any],
        *,
        ignore_phrases: tuple[str, ...] | list[str] | None = None,
        max_chars: int | None = None,
    ) -> str:
        blocks = cls._extract_text_blocks(payload)
        if not blocks:
            return ""
        merged = "\n".join(blocks)
        cleaned = cls._clean_text(merged, ignore_phrases=ignore_phrases)
        if max_chars is None:
            return cleaned
        return cls._cap_text(cleaned, max_chars=max_chars)

    @classmethod
    def tool_output_text(
        cls,
        payload: dict[str, Any],
        *,
        ignore_phrases: tuple[str, ...] | list[str] | None = None,
    ) -> str:
        compact: dict[str, Any] = {
            "ok": not bool(isinstance(payload, dict) and payload.get("isError")),
            "current_url": cls.extract_current_url(payload),
            "page_title": cls.extract_page_title(payload),
            "summary": cls.summarize_tool_output(payload, ignore_phrases=ignore_phrases),
        }
        excerpt = cls.context_excerpt(payload, ignore_phrases=ignore_phrases)
        if excerpt:
            compact["page_excerpt"] = excerpt
        return json.dumps(compact, ensure_ascii=False)

    @classmethod
    def summarize_tool_output(
        cls,
        payload: dict[str, Any],
        *,
        ignore_phrases: tuple[str, ...] | list[str] | None = None,
    ) -> str:
        if not isinstance(payload, dict):
            return str(payload)
        pieces = [cls._clean_text(text, ignore_phrases=ignore_phrases) for text in cls._extract_text_blocks(payload)]
        pieces = [piece for piece in pieces if piece]
        if pieces:
            return cls._cap_text(" | ".join(pieces), max_chars=cls._SUMMARY_MAX_CHARS)
        if payload.get("structuredContent") is not None:
            return cls._cap_text(json.dumps(payload.get("structuredContent"), ensure_ascii=False), max_chars=cls._SUMMARY_MAX_CHARS)
        if payload.get("isError"):
            return cls._cap_text(json.dumps(payload, ensure_ascii=False), max_chars=cls._SUMMARY_MAX_CHARS)
        return cls._cap_text(json.dumps(payload, ensure_ascii=False), max_chars=cls._SUMMARY_MAX_CHARS)

    @classmethod
    def context_excerpt(
        cls,
        payload: dict[str, Any],
        *,
        ignore_phrases: tuple[str, ...] | list[str] | None = None,
    ) -> str:
        return cls.clean_page_text(payload, ignore_phrases=ignore_phrases, max_chars=cls._CONTEXT_MAX_CHARS)

    @classmethod
    def _result_excerpt(
        cls,
        payload: dict[str, Any],
        *,
        ignore_phrases: tuple[str, ...] | list[str] | None = None,
        max_chars: int | None = None,
    ) -> str:
        sections: list[str] = []
        structured = payload.get("structuredContent") if isinstance(payload, dict) else None
        if structured is not None:
            sections.append(json.dumps(structured, ensure_ascii=False))
        for text in cls._extract_text_blocks(payload):
            match = re.search(r"(### Result\b.*?)(?=\n### [A-Za-z]|\Z)", text, flags=re.S)
            if not match:
                continue
            cleaned = cls._clean_text(match.group(1), ignore_phrases=ignore_phrases)
            if cleaned:
                sections.append(cleaned)
        if not sections:
            return ""
        return cls._cap_text("\n".join(sections), max_chars=max_chars or cls._CONTEXT_MAX_CHARS)

    @classmethod
    def build_tool_feedback(
        cls,
        name: str,
        payload: dict[str, Any],
        *,
        ignore_phrases: tuple[str, ...] | list[str] | None = None,
    ) -> str:
        lines = [f"Browser tool result: {name}"]
        lines.append(f"Status: {'error' if bool(payload.get('isError')) else 'ok'}")
        current_url = cls.extract_current_url(payload)
        if current_url:
            lines.append(f"Page URL: {current_url}")
        page_title = cls.extract_page_title(payload)
        if page_title:
            lines.append(f"Page Title: {page_title}")
        excerpt = ""
        normalized_name = str(name or "").strip()
        if normalized_name == "browser_snapshot":
            excerpt = cls.live_page_text(payload)
        elif normalized_name == "browser_evaluate":
            excerpt = cls._result_excerpt(payload, ignore_phrases=ignore_phrases, max_chars=cls._CONTEXT_MAX_CHARS)
        if not excerpt and normalized_name != "browser_snapshot":
            excerpt = cls.context_excerpt(payload, ignore_phrases=ignore_phrases)
        if excerpt:
            if normalized_name == "browser_snapshot":
                lines.append("Current live page snapshot:")
            elif normalized_name == "browser_evaluate":
                lines.append("Tool result:")
            else:
                lines.append("Tool output excerpt:")
            lines.append(excerpt)
        elif normalized_name != "browser_snapshot":
            summary = cls.summarize_tool_output(payload, ignore_phrases=ignore_phrases)
            if summary:
                lines.append(f"Tool output: {summary}")
        feedback = "\n".join(lines)
        if normalized_name == "browser_snapshot":
            return feedback
        return cls._cap_text(feedback, max_chars=cls._FEEDBACK_MAX_CHARS)

    @staticmethod
    def extract_current_url(payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return ""
        structured = payload.get("structuredContent")
        if isinstance(structured, dict):
            for key in ("url", "pageUrl", "page_url", "currentUrl", "current_url"):
                value = structured.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        for key in ("url", "pageUrl", "page_url", "currentUrl", "current_url"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        content = payload.get("content")
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if not isinstance(text, str) or not text.strip():
                    continue
                match = re.search(r"Page URL:\s*(\S+)", text)
                if match:
                    return match.group(1).strip()
        return ""

    @staticmethod
    def extract_page_title(payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return ""
        structured = payload.get("structuredContent")
        if isinstance(structured, dict):
            for key in ("title", "pageTitle", "page_title"):
                value = structured.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        for key in ("title", "pageTitle", "page_title"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for text in MCPToolBridge._extract_text_blocks(payload):
            match = re.search(r"Page Title:\s*(.+)", text)
            if match:
                return match.group(1).strip()
        return ""
