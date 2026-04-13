"""Stateless Responses loop that executes local Playwright MCP function tools."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import json
import re
import time
from typing import Any
from urllib.parse import parse_qsl, urlparse

import anyio
import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from careereng.browser_controls.bridge import MCPToolBridge
from careereng.browser_controls.prompting import PhasePrompt


@dataclass(frozen=True)
class BrowserRuntimeConfig:
    api_base: str
    api_key: str
    model: str
    reasoning_effort: str = "high"
    phase_timeout_seconds: int = 180
    step_timeout_seconds: int = 30
    max_step_retries: int = 1
    max_phase_steps: int = 24


@dataclass(frozen=True)
class BrowserPhaseResult:
    status: str
    reason_tag: str
    summary: str
    current_url: str = ""
    step_count: int = 0
    trace_ref: str = ""
    raw_text: str = ""
    recorded_count: int = 0
    new_count: int = 0


class ResponsesClient:
    def __init__(self, *, api_base: str, api_key: str, timeout_seconds: float):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = max(30.0, float(timeout_seconds or 30.0))
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.api_base,
            timeout=self.timeout_seconds,
        )

    @staticmethod
    def _normalize_stream_input(input_value: Any) -> list[dict[str, Any]]:
        if not isinstance(input_value, list):
            return [{"role": "user", "content": [{"type": "input_text", "text": str(input_value or "")}]}]
        items: list[dict[str, Any]] = []
        for item in input_value:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "user").strip() or "user"
            content = item.get("content")
            if isinstance(content, list):
                blocks: list[dict[str, Any]] = []
                for block in content:
                    if isinstance(block, dict) and str(block.get("type") or "").strip():
                        blocks.append(block)
                if blocks:
                    items.append({"role": role, "content": blocks})
                    continue
            items.append(
                {
                    "role": role,
                    "content": [{"type": "input_text", "text": str(content or "")}],
                }
            )
        return items

    @staticmethod
    def _dump_item(item: Any) -> dict[str, Any]:
        if hasattr(item, "model_dump"):
            dumped = item.model_dump()
            return dumped if isinstance(dumped, dict) else {}
        if isinstance(item, dict):
            return dict(item)
        return {}

    @staticmethod
    def _stream_item_key(item_id: Any, output_index: Any) -> str:
        raw_id = str(item_id or "").strip()
        if raw_id:
            return raw_id
        if isinstance(output_index, int):
            return f"output_index:{output_index}"
        return ""

    async def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        stream_payload: dict[str, Any] = {
            "model": payload.get("model"),
            "input": self._normalize_stream_input(payload.get("input")),
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if isinstance(payload.get("reasoning"), dict) and payload["reasoning"]:
            stream_payload["reasoning"] = payload["reasoning"]
        if isinstance(payload.get("tools"), list) and payload["tools"]:
            stream_payload["tools"] = payload["tools"]
        if payload.get("tool_choice") is not None:
            stream_payload["tool_choice"] = payload["tool_choice"]

        output_items: list[dict[str, Any]] = []
        partial_items: dict[str, dict[str, Any]] = {}
        partial_item_order: list[str] = []
        output_text_parts: list[str] = []
        response_id = ""
        response_status = "completed"
        stream_event_types: list[str] = []
        try:
            async with self.client.responses.stream(**stream_payload) as stream:
                async for event in stream:
                    event_type = str(getattr(event, "type", "") or "")
                    if event_type and event_type not in stream_event_types and len(stream_event_types) < 12:
                        stream_event_types.append(event_type)
                    if event_type == "response.created":
                        response = getattr(event, "response", None)
                        response_id = str(getattr(response, "id", "") or response_id)
                        response_status = str(getattr(response, "status", "") or response_status)
                    elif event_type == "response.output_item.added":
                        item = self._dump_item(getattr(event, "item", None))
                        item_key = self._stream_item_key(item.get("id"), getattr(event, "output_index", None))
                        if item_key:
                            if item_key not in partial_items:
                                partial_item_order.append(item_key)
                            partial_items[item_key] = item
                    elif event_type == "response.output_text.delta":
                        delta = str(getattr(event, "delta", "") or "")
                        if delta:
                            output_text_parts.append(delta)
                    elif event_type == "response.output_text.done":
                        item_key = self._stream_item_key(getattr(event, "item_id", None), getattr(event, "output_index", None))
                        if item_key and item_key in partial_items:
                            item = dict(partial_items[item_key])
                            content = item.get("content")
                            if not isinstance(content, list):
                                content = []
                            text = str(getattr(event, "text", "") or "")
                            if text:
                                content = [block for block in content if not isinstance(block, dict) or str(block.get("type") or "") != "output_text"]
                                content.append({"type": "output_text", "text": text})
                                item["content"] = content
                                partial_items[item_key] = item
                    elif event_type == "response.function_call_arguments.done":
                        item_key = self._stream_item_key(getattr(event, "item_id", None), getattr(event, "output_index", None))
                        if item_key and item_key in partial_items:
                            item = dict(partial_items[item_key])
                            item["arguments"] = str(getattr(event, "arguments", "") or "")
                            partial_items[item_key] = item
                    elif event_type == "response.output_item.done":
                        dumped = self._dump_item(getattr(event, "item", None))
                        if dumped:
                            item_key = self._stream_item_key(dumped.get("id"), getattr(event, "output_index", None))
                            if item_key:
                                if item_key not in partial_items:
                                    partial_item_order.append(item_key)
                                partial_items[item_key] = dumped
                            else:
                                output_items.append(dumped)
                final = await stream.get_final_response()
                response_id = str(getattr(final, "id", "") or response_id)
                response_status = str(getattr(final, "status", "") or response_status)
        except APIStatusError as exc:
            body = getattr(exc, "body", None)
            detail = json.dumps(body, ensure_ascii=False) if body is not None else str(exc)
            raise RuntimeError(detail[:2000] or f"responses api error {getattr(exc, 'status_code', 'unknown')}") from exc
        except (APIConnectionError, APITimeoutError) as exc:
            cause = exc.__cause__ or exc.__context__
            if isinstance(cause, (httpx.ConnectError, httpx.RemoteProtocolError, httpx.TimeoutException)):
                raise cause
            raise httpx.ConnectError(str(exc)) from exc

        output_text = "".join(output_text_parts).strip()
        for item_key in partial_item_order:
            item = partial_items.get(item_key)
            if isinstance(item, dict) and item:
                output_items.append(item)
        data: dict[str, Any] = {
            "id": response_id,
            "status": response_status,
            "output": output_items,
            "stream_event_types": stream_event_types,
        }
        if output_text:
            data["output_text"] = output_text
        return data


class BrowserPhaseRuntime:
    RECENT_TRAJECTORY_LIMIT = 4
    RECENT_TRAJECTORY_ARGUMENTS_MAX_CHARS = 240
    SESSION_PREPARATION_AUTH_PHRASES = (
        "sign in",
        "log in",
        "sign in with",
        "continue with",
        "use another account",
        "pick an account",
        "remembered account",
    )
    SESSION_PREPARATION_BLOCKED_NAVIGATION_FRAGMENTS = (
        "/profile",
        "profile.html",
        "myprofile",
        "/account",
        "candidate/profile",
    )
    JOB_RETRIEVAL_PAGE_ACTION_TOOLS = (
        "browser_click",
        "browser_type",
        "browser_press_key",
        "browser_select_option",
        "browser_navigate",
    )
    PAGE_SETTLE_ACTION_TOOLS = (
        "browser_click",
        "browser_type",
        "browser_press_key",
        "browser_select_option",
        "browser_navigate",
    )
    JOB_RETRIEVAL_PAGE_ACTION_WAIT_SECONDS = 5.0
    PAGE_SETTLE_MAX_SNAPSHOT_RETRIES = 2
    PAGE_SETTLE_SLEEP_SECONDS = 0.75
    RESPONSE_RETRYABLE_STATUS_CODES = ("500", "502", "503", "504")

    def __init__(
        self,
        config: BrowserRuntimeConfig,
        *,
        responses_client: ResponsesClient | None = None,
        sleep_fn=None,
    ):
        self.config = config
        self.responses = responses_client or ResponsesClient(
            api_base=config.api_base,
            api_key=config.api_key,
            timeout_seconds=max(config.phase_timeout_seconds, config.step_timeout_seconds) + 30,
        )
        self.sleep_fn = sleep_fn or anyio.sleep

    async def _sleep(self, seconds: float) -> None:
        result = self.sleep_fn(seconds)
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _is_observation_tool(name: str) -> bool:
        normalized = str(name or "").strip().lower()
        return normalized in {"browser_snapshot", "browser_console_messages", "browser_tabs"}

    @staticmethod
    def _observation_guard_message(*, phase: PhasePrompt, current_url: str) -> str:
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        phase_tail = ""
        if phase.slug == "session_preparation":
            phase_tail = (
                " If a visible sign-in continuation, provider option, or remembered-account step is available, "
                "click it directly with the provided tools. If the page requires password, email entry, MFA, "
                "verification, CAPTCHA, or another human-only challenge, finish with phase_result status=blocked."
            )
        return (
            f"You have already inspected the current page multiple times during `{phase.slug}` without taking a page-changing action.\n"
            f"{url_line}"
            "Do not call another observation-only browser tool right now. "
            "Use the existing page evidence and skill guidance to either take the next safe browser action or finish the phase with phase_result."
            f"{phase_tail}"
        )

    @staticmethod
    def _tool_unavailable_message(*, phase: PhasePrompt, current_url: str, tool_name: str) -> str:
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        phase_tail = "Choose another provided official browser tool or finish the phase with phase_result."
        if phase.slug == "session_preparation":
            phase_tail = (
                "Use the provided official browser tools directly. Do not write custom browser code. "
                "If a visible sign-in continuation, provider option, or remembered-account step is available, click it directly. "
                "If the page requires password, email entry, MFA, verification, CAPTCHA, or another human-only challenge, "
                "finish with phase_result status=blocked. If authentication is already complete, finish with phase_result status=done."
            )
        return (
            f"The tool `{tool_name}` is not available for `{phase.slug}`.\n"
            f"{url_line}"
            f"{phase_tail}"
        )

    @classmethod
    def _payload_has_visible_auth_action(cls, payload: dict[str, Any], *, phase: PhasePrompt) -> bool:
        page_text = MCPToolBridge.live_page_text(payload)
        if not page_text:
            return False
        lowered = page_text.lower()
        if "[ref=" not in lowered:
            return False
        if 'button "' not in lowered and 'link "' not in lowered:
            return False
        return any(phrase in lowered for phrase in cls.SESSION_PREPARATION_AUTH_PHRASES)

    @classmethod
    def _is_blocked_session_navigation_target(cls, target_url: str) -> bool:
        raw = str(target_url or "").strip()
        if not raw:
            return False
        try:
            parsed = urlparse(raw)
        except Exception:
            return False
        haystack = f"{parsed.netloc}{parsed.path}".lower()
        return any(fragment in haystack for fragment in cls.SESSION_PREPARATION_BLOCKED_NAVIGATION_FRAGMENTS)

    @classmethod
    def _navigation_guard_message(cls, *, phase: PhasePrompt, current_url: str, target_url: str) -> str:
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        return (
            f"Do not jump to guessed account or profile URLs during `{phase.slug}` when the current page already shows a visible authentication action.\n"
            f"{url_line}"
            f"Rejected navigation target: {target_url}\n"
            "Stay on the current flow and use the visible browser controls directly. "
            "If a visible sign-in continuation, provider option, or remembered-account step is available, click it. "
            "If the page requires password, email entry, MFA, verification, CAPTCHA, or another human-only challenge, finish with phase_result status=blocked."
        )

    async def _create_response_with_retry(self, payload: dict[str, Any]) -> dict[str, Any]:
        attempts = 0
        max_retries = 2
        while True:
            try:
                return await self.responses.create(payload)
            except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.TimeoutException):
                attempts += 1
                if attempts > max_retries:
                    raise
                await self._sleep(min(3.0, float(attempts)))
            except RuntimeError as exc:
                if not self._is_retryable_response_runtime_error(exc):
                    raise
                attempts += 1
                if attempts > max_retries:
                    raise
                await self._sleep(min(3.0, float(attempts)))

    @classmethod
    def _is_retryable_response_runtime_error(cls, exc: RuntimeError) -> bool:
        text = str(exc or "").strip().lower()
        if not text:
            return False
        if any(f"responses api error {code}" in text for code in cls.RESPONSE_RETRYABLE_STATUS_CODES):
            return True
        if any(
            phrase in text
            for phrase in (
                "gateway timeout",
                "bad gateway",
                "service unavailable",
                "internal server error",
            )
        ):
            return True
        return any(re.search(rf"\b{code}\b", text) for code in cls.RESPONSE_RETRYABLE_STATUS_CODES)

    @staticmethod
    def phase_result_tool() -> dict[str, Any]:
        return {
            "type": "function",
            "name": "phase_result",
            "description": "Report that the current phase is done or blocked.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["done", "blocked"]},
                    "summary": {"type": "string"},
                },
                "required": ["status", "summary"],
                "additionalProperties": False,
            },
        }

    @staticmethod
    def record_jobs_tool() -> dict[str, Any]:
        job_properties = {
            "title": {"type": "string"},
            "url": {"type": "string"},
            "location": {"type": "string"},
            "posted_label": {"type": "string"},
            "employment_type": {"type": "string"},
            "match_label": {"type": "string"},
            "apply_state": {"type": "string"},
            "card_text": {"type": "string"},
            "posted_at": {"type": "string"},
        }
        return {
            "type": "function",
            "name": "record_jobs",
            "description": "Persist the full visible job list from the current page for later retrieval and apply phases.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "jobs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": job_properties,
                            "required": list(job_properties.keys()),
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["jobs"],
                "additionalProperties": False,
            },
        }

    @staticmethod
    def _extract_output_items(response: dict[str, Any]) -> list[dict[str, Any]]:
        output = response.get("output")
        if not isinstance(output, list):
            return []
        return [item for item in output if isinstance(item, dict)]

    @staticmethod
    def _extract_output_text(response: dict[str, Any]) -> str:
        direct = response.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        parts: list[str] = []
        for item in BrowserPhaseRuntime._extract_output_items(response):
            if str(item.get("type") or "") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"].strip())
        return "\n".join(part for part in parts if part).strip()

    @staticmethod
    def _maybe_parse_phase_result_text(text: str) -> dict[str, Any] | None:
        raw = str(text or "").strip()
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        if str(data.get("status") or "") not in {"done", "blocked"}:
            return None
        return data

    def _payload(self, *, input_items: Any, tools: list[dict[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "input": input_items,
            "tools": tools,
            "tool_choice": "required",
        }
        effort = str(self.config.reasoning_effort or "").strip().lower()
        if effort:
            payload["reasoning"] = {"effort": effort}
        return payload

    def _system_prompt(self, *, site_name: str, phase: PhasePrompt) -> str:
        prompt = (
            "You are controlling a live browser through official Playwright MCP tools. "
            "Do not invent any local browser DSL. Use the available function tools directly. "
            "Stay inside the active site workflow. "
            "Use the current live page as the primary source of truth. "
            "Use recent browser trajectory only to remember what was just attempted; do not let it override the current live page. "
            "Once the current phase goal is satisfied, stop exploring and call phase_result with status=done. "
            "When the current phase is complete, call phase_result with status=done. "
            "When the page requires human-only action such as password entry, MFA, verification code, CAPTCHA, or email confirmation, call phase_result with status=blocked."
        )
        if phase.slug == "session_preparation":
            prompt += (
                " During Session Preparation, prefer direct visible browser actions over extra inspection. "
                "Do not write custom browser code. If a visible sign-in continuation, provider option, or remembered-account step is available, click it directly. "
                "Do not continue speculative navigation after the session goal has already been satisfied."
            )
        elif phase.slug == "job_retrieval":
            prompt += (
                " During Job Retrieval, follow any explicit current-page preparation required by the active site skill before recording the page. "
                " If the active site skill does not require a preparatory action, record the current visible results page before opening any single job detail. "
                "Use the attached live snapshot as the default source for current-page list data. "
                "If the live snapshot already shows enough list-level job data, call record_jobs directly from that current page. "
                "If you still need one or more missing list-level fields, gather them from the same current results page and call record_jobs immediately. "
                "Use browser_evaluate only as a fallback for missing current-page fields, not as the default first step. "
                "If you use browser_evaluate during Job Retrieval, use the native official tool flow against the live current results surface. "
                "For current-page broad extraction, first form the current visible results set, then inspect same-page clickable job items and href-bearing job elements broadly. "
                "Current-page job cards may be anchors, links with role=button, buttons, or descendants of those. "
                "Do not assume the snapshot's role label implies a literal DOM tag. "
                "Do not decide validity only from page region names or layout position. "
                "Accept same-page per-role link sources only when they align back to the current visible results set for this page. "
                "If one broad same-page read still leaves current-page role links missing, one current-page result may be selected to expose current-page links, then re-read that same page and record it. "
                "After the current page is recorded, use only a real visible next-page, numbered page, or load-more control. "
                "Do not guess pagination URLs."
            )
        return prompt

    @classmethod
    def _cap_text(cls, text: str, *, max_chars: int) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        if len(raw) <= max_chars:
            return raw
        if max_chars <= 32:
            return raw[:max_chars]
        return raw[: max_chars - 14].rstrip() + "...[truncated]"

    @classmethod
    def _summarize_arguments(cls, arguments: dict[str, Any] | None) -> str:
        if not isinstance(arguments, dict) or not arguments:
            return ""
        preferred_keys = ("element", "url", "ref", "key", "text", "value", "action")
        parts: list[str] = []
        for key in preferred_keys:
            value = arguments.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                continue
            parts.append(f"{key}={text}")
        if not parts:
            try:
                raw = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
            except Exception:
                raw = str(arguments)
            return cls._cap_text(raw, max_chars=cls.RECENT_TRAJECTORY_ARGUMENTS_MAX_CHARS)
        return cls._cap_text(", ".join(parts), max_chars=cls.RECENT_TRAJECTORY_ARGUMENTS_MAX_CHARS)

    @classmethod
    def _format_recent_trajectory(cls, recent_steps: list[dict[str, str]]) -> str:
        lines: list[str] = []
        for index, step in enumerate(recent_steps[-cls.RECENT_TRAJECTORY_LIMIT :], start=1):
            tool = str(step.get("tool") or "").strip() or "unknown"
            lines.append(f"Step {index}: {tool}")
            action = str(step.get("action") or "").strip()
            if action:
                lines.append(f"Action: {action}")
            result = str(step.get("result") or "").strip()
            if result:
                lines.append(f"Result: {result}")
            url = str(step.get("url") or "").strip()
            if url:
                lines.append(f"URL: {url}")
            title = str(step.get("title") or "").strip()
            if title:
                lines.append(f"Title: {title}")
        return "\n".join(lines).strip()

    @classmethod
    def _is_job_retrieval_page_action(cls, name: str) -> bool:
        normalized = str(name or "").strip().lower()
        return normalized in cls.JOB_RETRIEVAL_PAGE_ACTION_TOOLS

    def _user_prompt(
        self,
        *,
        site_name: str,
        entry_url: str,
        phase: PhasePrompt,
        phase_handoff: str = "",
        recent_trajectory: str = "",
    ) -> str:
        guidance = phase.combined_guidance or "No phase-specific guidance was found."
        entry_line = f"Entry URL: {entry_url}\n" if entry_url else ""
        handoff_line = f"Previous phase handoff: {phase_handoff.strip()}\n" if phase_handoff.strip() else ""
        trajectory_line = f"Recent browser trajectory:\n{recent_trajectory.strip()}\n\n" if recent_trajectory.strip() else ""
        return (
            f"Site: {site_name}\n"
            f"Phase: {phase.title}\n"
            f"{entry_line}"
            f"{handoff_line}"
            "Use the current live page as the primary source of truth.\n"
            "Use recent browser trajectory only as lightweight action history; do not continue reasoning from the pre-action page once a fresh live snapshot is available.\n"
            "If the current phase goal is already satisfied, stop exploring and call phase_result.\n\n"
            f"{trajectory_line}"
            f"{guidance}\n\n"
            "Return control only by calling phase_result."
        )

    def _append_recent_step(
        self,
        recent_steps: list[dict[str, str]],
        *,
        tool_name: str,
        arguments: dict[str, Any] | None,
        error_text: str,
        current_url: str,
        payload: dict[str, Any],
        include_page_state: bool = True,
    ) -> None:
        recent_steps.append(
            {
                "tool": str(tool_name or "").strip(),
                "action": self._summarize_arguments(arguments),
                "result": "error" if str(error_text or "").strip() else "ok",
                "url": str(current_url or "").strip() if include_page_state else "",
                "title": MCPToolBridge.extract_page_title(payload) if include_page_state else "",
            }
        )
        if len(recent_steps) > self.RECENT_TRAJECTORY_LIMIT:
            del recent_steps[:-self.RECENT_TRAJECTORY_LIMIT]

    @staticmethod
    def _live_snapshot_primary_message() -> str:
        return (
            "A fresh live browser snapshot from the current page is attached separately. "
            "Use that current live page as the primary source of truth. "
            "Use recent trajectory only to remember what was just attempted."
        )

    async def _capture_snapshot_payload(
        self,
        *,
        bridge: MCPToolBridge,
        session: Any,
        tool_names: set[str],
    ) -> dict[str, Any] | None:
        if "browser_snapshot" not in tool_names:
            return None
        try:
            return await bridge.call_tool(session, "browser_snapshot", {"filename": ""})
        except Exception as exc:
            return {"isError": True, "error": f"snapshot_failed: {exc}", "tool": "browser_snapshot"}

    async def _wait_for_page_settle(
        self,
        *,
        tool_name: str,
        latest_snapshot_payload: dict[str, Any] | None,
        current_url: str,
        bridge: MCPToolBridge,
        session: Any,
        tool_names: set[str],
    ) -> tuple[dict[str, Any] | None, str]:
        if not self._is_page_settle_action(tool_name):
            return latest_snapshot_payload, current_url
        payload = latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else None
        updated_url = str(current_url or "")
        retries = 0
        while payload is not None and self._payload_has_loading_signal(payload) and retries < self.PAGE_SETTLE_MAX_SNAPSHOT_RETRIES:
            retries += 1
            await self._sleep(self.PAGE_SETTLE_SLEEP_SECONDS)
            refreshed = await self._capture_snapshot_payload(
                bridge=bridge,
                session=session,
                tool_names=tool_names,
            )
            if not isinstance(refreshed, dict) or bool(refreshed.get("isError")):
                break
            payload = refreshed
            snapshot_url = MCPToolBridge.extract_current_url(refreshed)
            if snapshot_url:
                updated_url = snapshot_url
        return payload, updated_url

    @staticmethod
    def _context_item(content: str) -> dict[str, str]:
        return {"role": "user", "content": content}

    @staticmethod
    def _normalize_record_job(job: dict[str, Any]) -> dict[str, Any]:
        allowed_keys = (
            "title",
            "url",
            "location",
            "posted_label",
            "employment_type",
            "match_label",
            "apply_state",
            "card_text",
            "posted_at",
        )
        normalized: dict[str, Any] = {}
        for key in allowed_keys:
            value = job.get(key)
            if value is None:
                continue
            if isinstance(value, str):
                normalized[key] = value.strip()
            else:
                normalized[key] = str(value).strip()
        return normalized

    @staticmethod
    def _canonicalize_runtime_url(url: str) -> tuple[str, str, str, tuple[tuple[str, str], ...]] | tuple[()]:
        raw = str(url or "").strip()
        if not raw:
            return ()
        try:
            parsed = urlparse(raw)
        except Exception:
            return ()
        return (
            str(parsed.scheme or "").lower(),
            str(parsed.netloc or "").lower(),
            str(parsed.path or ""),
            tuple(sorted(parse_qsl(parsed.query, keep_blank_values=True))),
        )

    @classmethod
    def _is_unresolved_job_url(cls, *, job_url: str, current_url: str) -> bool:
        raw_job_url = str(job_url or "").strip()
        if not raw_job_url:
            return True
        raw_current_url = str(current_url or "").strip()
        if not raw_current_url:
            return False
        job_key = cls._canonicalize_runtime_url(raw_job_url)
        current_key = cls._canonicalize_runtime_url(raw_current_url)
        if not job_key or not current_key:
            return False
        return job_key == current_key

    @classmethod
    def _record_jobs_missing_url_count(cls, arguments: dict[str, Any] | None, *, current_url: str = "") -> int:
        if not isinstance(arguments, dict):
            return 0
        jobs = arguments.get("jobs")
        if not isinstance(jobs, list):
            return 0
        missing = 0
        for job in jobs:
            if not isinstance(job, dict):
                continue
            url = str(job.get("url") or "").strip()
            if not cls._is_unresolved_job_url(job_url=url, current_url=current_url):
                continue
            has_other_fields = any(
                str(job.get(field) or "").strip()
                for field in (
                    "title",
                    "location",
                    "posted_label",
                    "employment_type",
                    "match_label",
                    "apply_state",
                    "card_text",
                    "posted_at",
                )
            )
            if has_other_fields:
                missing += 1
        return missing

    @staticmethod
    def _payload_has_extracted_jobs(payload: dict[str, Any]) -> bool:
        jobs = BrowserPhaseRuntime._extract_job_records(payload)
        if jobs is not None:
            return bool(jobs)
        if not isinstance(payload, dict):
            return False
        summary = MCPToolBridge.summarize_tool_output(payload)
        if not summary:
            return False
        return bool(re.search(r'"jobsCount"\s*:\s*[1-9]\d*', summary))

    @staticmethod
    def _has_nonempty_ref(arguments: dict[str, Any] | None) -> bool:
        if not isinstance(arguments, dict):
            return False
        ref = arguments.get("ref")
        return isinstance(ref, str) and bool(ref.strip())

    @staticmethod
    def _looks_like_element_callback(function_text: str) -> bool:
        raw = str(function_text or "").strip()
        if not raw:
            return False
        return bool(
            re.match(r"^(?:async\s+)?\(\s*(?:el|element)\s*\)\s*=>", raw)
            or re.match(r"^(?:async\s+)?function\s*\(\s*(?:el|element)\s*\)", raw)
        )

    @classmethod
    def _job_retrieval_requires_pagewide_evaluate(cls, arguments: dict[str, Any] | None) -> bool:
        if not isinstance(arguments, dict):
            return False
        if cls._has_nonempty_ref(arguments):
            return True
        return cls._looks_like_element_callback(str(arguments.get("function") or ""))

    @staticmethod
    def _parse_result_json_blocks(payload: dict[str, Any]) -> list[Any]:
        if not isinstance(payload, dict):
            return []
        parsed: list[Any] = []
        content = payload.get("content")
        if not isinstance(content, list):
            return parsed
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            match = re.search(r"### Result\b\s*(.*?)(?=\n### [A-Za-z]|\Z)", text, flags=re.S)
            if not match:
                continue
            candidate = str(match.group(1) or "").strip()
            if not candidate:
                continue
            try:
                parsed.append(json.loads(candidate))
            except Exception:
                continue
        return parsed

    @staticmethod
    def _coerce_job_records(value: Any) -> list[dict[str, Any]] | None:
        if isinstance(value, list):
            if all(isinstance(item, dict) for item in value):
                return [dict(item) for item in value]
            return None
        if not isinstance(value, dict):
            return None
        for key in ("jobs", "items", "results"):
            jobs = value.get(key)
            if isinstance(jobs, list) and all(isinstance(item, dict) for item in jobs):
                return [dict(item) for item in jobs]
        return None

    @classmethod
    def _filter_current_results_job_records(cls, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [dict(job) for job in jobs]

    @classmethod
    def _extract_job_records(cls, payload: dict[str, Any]) -> list[dict[str, Any]] | None:
        if not isinstance(payload, dict):
            return None
        structured = payload.get("structuredContent")
        for candidate in [structured, *cls._parse_result_json_blocks(payload)]:
            jobs = cls._coerce_job_records(candidate)
            if jobs is not None:
                return cls._filter_current_results_job_records(jobs)
        return None

    @classmethod
    def _browser_evaluate_error_text(cls, payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            return ""
        structured = payload.get("structuredContent")
        candidates = [structured, *cls._parse_result_json_blocks(payload)]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            ok_value = candidate.get("ok")
            error_value = candidate.get("error")
            message_value = candidate.get("message")
            if ok_value is not False and error_value in (None, "", [], {}):
                continue
            if isinstance(error_value, str) and error_value.strip():
                return error_value.strip()
            if error_value not in (None, "", [], {}):
                try:
                    return json.dumps(error_value, ensure_ascii=False)
                except Exception:
                    return str(error_value)
            if ok_value is False:
                if isinstance(message_value, str) and message_value.strip():
                    return message_value.strip()
                return "browser_evaluate returned ok=false"
        return ""

    @staticmethod
    def _is_browser_evaluate_serialization_error(error_text: str) -> bool:
        normalized = str(error_text or "").strip().lower()
        if not normalized:
            return False
        return "well-serializable" in normalized or "serializable" in normalized

    @staticmethod
    def _job_retrieval_record_jobs_message() -> str:
        return (
            "You already have the current page jobs from a non-empty extraction of the current visible results page. "
            "If each current visible role already has its own concrete role link, Call record_jobs now. "
            "Record only the roles that align with the current visible results set for this page. "
            "If any current-page role still lacks its own concrete role link, stay on this page and fill only the missing links before record_jobs."
        )

    @staticmethod
    def _job_retrieval_snapshot_first_message(*, current_url: str, page_label: str) -> str:
        page_line = f"Current page label: {page_label}\n" if page_label else ""
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        return (
            "A fresh live snapshot of the current results page is already attached.\n"
            f"{url_line}"
            f"{page_line}"
            "Use that live snapshot as the primary source for the current visible jobs page. "
            "If the current page titles and concrete role links are already readable there, form the current-page records and call record_jobs. "
            "If one or more current-page role links or list-level fields are still unclear, use browser_evaluate only to fill those current-page gaps from the same page. "
            "When doing that same-page read, first anchor the current visible results set, then keep only same-page per-role data that aligns back to that set. "
            "Do not record from the live snapshot alone while current-page titles or role links are still unclear."
        )

    @staticmethod
    def _job_retrieval_extracted_page_message(*, current_url: str, page_label: str) -> str:
        page_line = f"Current page label: {page_label}\n" if page_label else ""
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        return (
            "The current retrieval page already yielded a non-empty jobs extraction.\n"
            f"{url_line}"
            f"{page_line}"
            "Do not inspect or open a single job on this same page right now. "
            "Use the already extracted current-page jobs to call record_jobs now. "
            "After that, either take a real visible page-changing action or finish with phase_result if the site stop condition has been met."
        )

    @staticmethod
    def _payload_has_empty_extracted_jobs(payload: dict[str, Any]) -> bool:
        jobs = BrowserPhaseRuntime._extract_job_records(payload)
        if jobs is not None:
            return not jobs
        if not isinstance(payload, dict):
            return False
        summary = MCPToolBridge.summarize_tool_output(payload)
        if not summary:
            return False
        return bool(re.search(r'"jobs"\s*:\s*\[\s*\]|\[\s*\]', summary))

    @staticmethod
    def _payload_has_job_results_signal(payload: dict[str, Any] | None) -> bool:
        if not isinstance(payload, dict):
            return False
        structured = payload.get("structuredContent")
        if isinstance(structured, dict):
            for key in ("totalCount", "total_count", "jobsCount", "jobs_count"):
                value = structured.get(key)
                try:
                    if int(value) > 0:
                        return True
                except Exception:
                    pass
            for key in ("hasNext", "has_next"):
                value = structured.get(key)
                if isinstance(value, bool) and value:
                    return True
            for key in ("nextUrl", "next_url", "pageLabel", "page_label", "totalText", "total_text"):
                value = structured.get(key)
                if isinstance(value, str) and value.strip():
                    if key.startswith("next") or re.search(r"\b\d+\s+jobs\b|\bPage\s+\d+\s+of\s+\d+\b", value, flags=re.IGNORECASE):
                        return True
        live_page = MCPToolBridge.live_page_text(payload)
        if live_page and re.search(
            r'"(?:jobsCount|jobs_count|totalCount|total_count)"\s*:\s*[1-9]\d*|\b\d+\s+jobs\b|\bPage\s+\d+\s+of\s+\d+\b|"hasNext"\s*:\s*true|"nextUrl"\s*:\s*"[^"]+',
            live_page,
            flags=re.IGNORECASE,
        ):
            return True
        return False

    @staticmethod
    def _payload_has_loading_signal(payload: dict[str, Any] | None) -> bool:
        if not isinstance(payload, dict):
            return False
        live_page = MCPToolBridge.live_page_text(payload)
        if not live_page:
            return False
        lowered = live_page.lower()
        if any(
            phrase in lowered
            for phrase in (
                "loading jobs",
                "loading job card",
                "loading results",
                'status "loading',
                "aria-busy",
                "progressbar",
                "spinner",
                "skeleton",
            )
        ):
            return True
        return bool(
            re.search(r"\bloading\b", lowered)
            and re.search(r"\b(page\s+\d+\s+of\s+\d+|\d+\s+jobs|next|previous)\b", lowered)
        )

    @classmethod
    def _is_page_settle_action(cls, name: str) -> bool:
        normalized = str(name or "").strip().lower()
        return normalized in cls.PAGE_SETTLE_ACTION_TOOLS

    @classmethod
    def _looks_like_pagination_action(cls, name: str, arguments: dict[str, Any] | None) -> bool:
        normalized = str(name or "").strip().lower()
        if normalized not in cls.PAGE_SETTLE_ACTION_TOOLS or not isinstance(arguments, dict):
            return False
        haystack = " ".join(
            str(arguments.get(key) or "")
            for key in ("element", "key", "keys", "text", "button")
        ).strip().lower()
        if not haystack:
            return False
        return bool(re.search(r"\b(next|previous|prev|pagination|page\b|load more|show more|more jobs)\b", haystack))

    @staticmethod
    def _job_retrieval_empty_extraction_message(*, current_url: str, page_label: str) -> str:
        page_line = f"Current page label: {page_label}\n" if page_label else ""
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        return (
            "The current retrieval page still shows live results signals, but the latest extraction returned zero jobs.\n"
            f"{url_line}"
            f"{page_line}"
            "Stay on the same current results page. "
            "Do not record this page from the live snapshot alone. "
            "Use the attached live snapshot first. "
            "If you use browser_evaluate again, run it page-wide with ref='' and a no-argument function against the current page. "
            "First form the current visible results set, then inspect the same page broadly for any aligned per-role link sources. "
            "Do not open a single current-page role just to recover this page. "
            "Do not paginate away from this page until the current visible roles are concretely formed."
        )

    @staticmethod
    def _job_retrieval_navigation_message(*, current_url: str, page_label: str) -> str:
        page_line = f"Current page label: {page_label}\n" if page_label else ""
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        return (
            "Do not use direct browser_navigate during Job Retrieval.\n"
            f"{url_line}"
            f"{page_line}"
            "Record the current page first, then use only a real visible next-page, numbered page, or load-more control from the live page."
        )

    @staticmethod
    def _page_still_loading_message(*, phase: PhasePrompt, current_url: str) -> str:
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        return (
            "The live page still appears to be loading from the previous action.\n"
            f"{url_line}"
            f"Phase: {phase.title}\n"
            "Do not take another page-changing or result-changing action yet. "
            "Wait for a fresh settled snapshot of the current page before continuing."
        )

    @staticmethod
    def _job_retrieval_page_action_completed_message(*, tool_name: str) -> str:
        return (
            f"The previous page-changing action `{tool_name}` completed. "
            "Continue only from the fresh current page state. "
            "Do not reuse prior page URL, page title, selected-job detail, or PID context as evidence for the current visible results list. "
            "Before any same-page click, same-page pagination, or other same-page action, use the fresh current page to either form the current-page records directly or do one page-wide browser_evaluate that reads broadly and aligns back to the current visible results page."
        )

    @staticmethod
    def _job_retrieval_pagewide_evaluate_message(*, current_url: str, page_label: str) -> str:
        page_line = f"Current page label: {page_label}\n" if page_label else ""
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        return (
            "Do not run browser_evaluate against a scoped ref, section locator, text locator, or element callback during Job Retrieval.\n"
            f"{url_line}"
            f"{page_line}"
            "Run browser_evaluate page-wide with ref='' and a direct no-argument function that queries document or main itself for the current visible jobs page. "
            "Do not wrap an element callback inside another function. "
            "First form the current visible results set from this page, then inspect same-page clickable job items and href-bearing job elements broadly. Current-page job cards may be a, button, [role=\"button\"], or descendants of those. "
            "Do not assume a snapshot role label such as `button` implies the live DOM tag is literally button. "
            "Do not reject a same-page candidate only because it appears in a different panel, column, or section of the same page. "
            "Keep only same-page per-role links or fields that align back to the current visible results set for this page. "
            "Extract only the current visible results page, then call record_jobs immediately."
        )

    @staticmethod
    def _job_retrieval_missing_urls_message(*, current_url: str, page_label: str, missing_count: int) -> str:
        page_line = f"Current page label: {page_label}\n" if page_label else ""
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        count_text = f"{max(1, int(missing_count or 0))} current-page job record(s)" if missing_count else "current-page job records"
        return (
            f"Some {count_text} are still missing URLs.\n"
            f"{url_line}"
            f"{page_line}"
            "Stay on this same visible results page. "
            "Use the attached live snapshot first. "
            "If a specific current-page URL is still missing, do one focused supplemental read only to fill the missing URLs for the current visible jobs, then call record_jobs immediately. "
            "When supplementing, look broadly across the same page for aligned per-role link sources instead of excluding candidates by layout position alone. "
            "Do not assume the snapshot's role labels imply literal DOM tag names. "
            "Do not paginate away from this page while the missing current-page URLs remain unresolved."
        )

    @staticmethod
    def _job_retrieval_missing_urls_pagewide_message(*, current_url: str, page_label: str) -> str:
        page_line = f"Current page label: {page_label}\n" if page_label else ""
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        return (
            "While supplementing missing current-page URLs, do not run browser_evaluate against a scoped ref, text locator, section locator, or element callback.\n"
            f"{url_line}"
            f"{page_line}"
            "Run browser_evaluate page-wide with ref='' and a direct no-argument function against the current live page. "
            "Do not wrap an element callback inside another function. "
            "First form the current visible results set, then inspect the same page broadly for clickable job items and any href-bearing job elements. "
            "Current-page job cards may be a, button, [role=\"button\"], or descendants of those. "
            "Do not assume the snapshot's role labels imply literal DOM tag names. "
            "Do not reject a same-page candidate only because it appears in another panel or section of the same page. "
            "Keep only the per-role links that align back to the current visible results set. "
            "Do not open a single current-page role just to fill the missing URLs. "
            "After supplementing the current-page URLs, call record_jobs immediately."
        )

    @staticmethod
    def _job_retrieval_serialization_error_message(*, current_url: str, page_label: str) -> str:
        page_line = f"Current page label: {page_label}\n" if page_label else ""
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        return (
            "The previous browser_evaluate call was rejected before page execution because the function was not serializable.\n"
            f"{url_line}"
            f"{page_line}"
            "Stay on this same visible results page. "
            "Retry browser_evaluate page-wide with ref='' and a direct no-argument function against document or main. "
            "Do not wrap an element callback inside another function. "
            "First form the current visible results set for this page, then inspect the same page broadly for aligned per-role links or missing list fields, and call record_jobs immediately after a successful extraction."
        )

    @staticmethod
    def _job_retrieval_click_then_pagewide_message(*, current_url: str, page_label: str) -> str:
        page_line = f"Current page label: {page_label}\n" if page_label else ""
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        return (
            "The same current results page still shows jobs, but repeated broad extraction on this page did not concretely form the current-page roles.\n"
            f"{url_line}"
            f"{page_line}"
            "Stay on this same visible results page. "
            "Select one visible current-page result card only if that same-page selection is needed to expose current-page role links or list details. "
            "Then run one more page-wide browser_evaluate with ref='' and a no-argument function against document or main. "
            "Re-read only the current visible results page, gather same-page per-role data broadly, keep only what aligns back to the current visible results set, then call record_jobs immediately. "
            "Do not paginate away from this page."
        )

    @staticmethod
    def _job_retrieval_missing_urls_click_message(*, current_url: str, page_label: str, missing_count: int) -> str:
        page_line = f"Current page label: {page_label}\n" if page_label else ""
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        count_text = f"{max(1, int(missing_count or 0))} current-page job record(s)" if missing_count else "current-page job records"
        return (
            f"Some {count_text} are still missing concrete role links after one broad same-page read.\n"
            f"{url_line}"
            f"{page_line}"
            "Stay on this same visible results page. "
            "Select one visible current-page result card only if that same-page selection is needed to expose current-page role links for this page. "
            "Then run one more page-wide browser_evaluate with ref='' and a no-argument function against document or main, gather same-page per-role data broadly, keep only the still-missing URLs that align back to the current visible results set, and call record_jobs immediately. "
            "Do not paginate away from this page while those URLs remain unresolved."
        )

    @staticmethod
    def _extract_page_label(payload: dict[str, Any] | None) -> str:
        if not isinstance(payload, dict):
            return ""
        structured = payload.get("structuredContent")
        if isinstance(structured, dict):
            for key in ("page_label", "pageLabel", "current_page_label", "currentPageLabel"):
                value = structured.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        texts: list[str] = []
        live_page = MCPToolBridge.live_page_text(payload)
        if live_page:
            texts.append(live_page)
        for text in texts:
            match = re.search(r"\bPage\s+\d+\s+of\s+\d+\b", text, flags=re.IGNORECASE)
            if match:
                return match.group(0).strip()
        return ""

    @classmethod
    def _job_retrieval_page_fingerprint(
        cls,
        *,
        current_url: str,
        latest_snapshot_payload: dict[str, Any] | None,
        last_payload: dict[str, Any] | None,
    ) -> str:
        url = str(current_url or "").strip()
        page_label = cls._extract_page_label(latest_snapshot_payload) or cls._extract_page_label(last_payload)
        if url and page_label:
            return f"{url}#page={page_label.lower()}"
        if page_label:
            return f"page={page_label.lower()}"
        return url

    @staticmethod
    def _job_retrieval_page_recorded_message(*, current_url: str, page_label: str) -> str:
        page_line = f"Current page label: {page_label}\n" if page_label else ""
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        return (
            "The current retrieval page has already been recorded.\n"
            f"{url_line}"
            f"{page_line}"
            "Do not extract or inspect the same page again. "
            "From here, either take a page-changing action to the next results page / load-more control, or finish with phase_result status=done if there are no more results or the stop condition has been met."
        )

    def _record_jobs_payload(
        self,
        *,
        site_store: Any,
        site_key: str,
        session_id: str,
        turn_id: str,
        batch_id: str,
        current_url: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        raw_jobs = arguments.get("jobs")
        if not isinstance(raw_jobs, list):
            raw_jobs = []
        jobs: list[dict[str, Any]] = []
        for job in raw_jobs:
            if not isinstance(job, dict):
                continue
            normalized = self._normalize_record_job(job)
            if normalized:
                jobs.append(normalized)
        list_jobs = getattr(site_store, "list_jobs", None)
        preview_new_flags = getattr(site_store, "preview_history_new_flags", None)
        before_rows = list_jobs(site_key) if callable(list_jobs) else []
        before_ids = {str(row.get("job_id") or "") for row in before_rows if isinstance(row, dict)}
        if callable(preview_new_flags):
            try:
                new_flags = list(preview_new_flags(site_key, jobs))
            except Exception:
                new_flags = []
        else:
            new_flags = []
        saved_rows = site_store.append_jobs(site_key, jobs, session_id or "", turn_id, batch_id)
        saved_ids: list[str] = []
        new_ids: list[str] = []
        for idx, row in enumerate(saved_rows):
            if not isinstance(row, dict):
                continue
            record_id = str(row.get("observation_id") or row.get("job_id") or "").strip()
            if not record_id:
                continue
            saved_ids.append(record_id)
            is_new = idx < len(new_flags) and bool(new_flags[idx])
            if not new_flags and str(row.get("job_id") or "").strip() not in before_ids:
                is_new = True
            if is_new:
                new_ids.append(record_id)
        recorded_count = len(saved_ids)
        new_count = len(new_ids)
        summary = f"Recorded {recorded_count} jobs from the current page ({new_count} new)."
        return {
            "isError": False,
            "current_url": current_url,
            "structuredContent": {
                "current_url": current_url,
                "recorded_count": recorded_count,
                "new_count": new_count,
                "job_ids": saved_ids,
                "new_job_ids": new_ids,
            },
            "content": [{"type": "text", "text": summary}],
        }

    async def run_phase(
        self,
        *,
        site_key: str,
        site_name: str,
        entry_url: str,
        phase: PhasePrompt,
        bridge: MCPToolBridge,
        session: Any,
        site_store: Any,
        session_id: str = "",
        turn_id: str,
        batch_id: str = "",
        response_tools: list[dict[str, Any]],
        tool_names: set[str],
        phase_handoff: str = "",
        phase_timeout_seconds: int | None = None,
        max_phase_steps: int | None = None,
    ) -> BrowserPhaseResult:
        history_items: list[dict[str, Any]] = []
        tools = list(response_tools) + [self.phase_result_tool()]
        trace_ref = ""
        step_count = 0
        effective_phase_timeout = int(phase_timeout_seconds or self.config.phase_timeout_seconds or 180)
        effective_max_phase_steps = int(max_phase_steps or self.config.max_phase_steps or 24)
        deadline = time.monotonic() + max(5.0, float(effective_phase_timeout))
        current_url = str(entry_url or "")
        observation_streak = 0
        last_observation_url = ""
        recorded_job_ids: set[str] = set()
        new_job_ids: set[str] = set()
        recorded_page_fingerprints: set[str] = set()
        extracted_page_key = ""
        primary_read_page_key = ""
        pagewide_retry_page_key = ""
        click_recovery_page_key = ""
        latest_snapshot_payload = await self._capture_snapshot_payload(
            bridge=bridge,
            session=session,
            tool_names=tool_names,
        )
        if isinstance(latest_snapshot_payload, dict) and not bool(latest_snapshot_payload.get("isError")):
            snapshot_url = MCPToolBridge.extract_current_url(latest_snapshot_payload)
            if snapshot_url:
                current_url = snapshot_url
        last_payload: dict[str, Any] | None = (
            latest_snapshot_payload
            if isinstance(latest_snapshot_payload, dict) and not bool(latest_snapshot_payload.get("isError"))
            else None
        )
        recent_steps: list[dict[str, str]] = []

        while time.monotonic() < deadline and step_count < max(1, effective_max_phase_steps):
            loop_context_items = list(history_items)
            if (
                isinstance(latest_snapshot_payload, dict)
                and latest_snapshot_payload
                and not bool(latest_snapshot_payload.get("isError"))
            ):
                loop_context_items.append(
                    self._context_item(
                        MCPToolBridge.build_tool_feedback(
                            "browser_snapshot",
                            latest_snapshot_payload,
                            ignore_phrases=phase.ignore_phrases,
                        )
                        )
                    )
            base_items: list[dict[str, Any]] = [
                {"role": "system", "content": self._system_prompt(site_name=site_name, phase=phase)},
                {
                    "role": "user",
                    "content": self._user_prompt(
                        site_name=site_name,
                        entry_url=entry_url,
                        phase=phase,
                        phase_handoff=phase_handoff,
                        recent_trajectory=self._format_recent_trajectory(recent_steps),
                    ),
                },
            ]
            response = await self._create_response_with_retry(self._payload(input_items=base_items + loop_context_items, tools=tools))
            output_items = self._extract_output_items(response)
            output_text = self._extract_output_text(response)
            fallback_result = self._maybe_parse_phase_result_text(output_text)
            if fallback_result is not None:
                return BrowserPhaseResult(
                    status=str(fallback_result.get("status") or "blocked"),
                    reason_tag="message_phase_result",
                    summary=str(fallback_result.get("summary") or ""),
                    current_url=current_url,
                    step_count=step_count,
                    trace_ref=trace_ref,
                    raw_text=output_text,
                    recorded_count=len(recorded_job_ids),
                    new_count=len(new_job_ids),
                )

            if not output_items:
                stream_event_text = ",".join(
                    str(item).strip() for item in response.get("stream_event_types", []) if str(item).strip()
                )
                summary = "model returned no output items"
                if stream_event_text:
                    summary = f"{summary} (stream_events={stream_event_text})"
                return BrowserPhaseResult(
                    status="failed",
                    reason_tag="missing_tool_call",
                    summary=summary,
                    current_url=current_url,
                    step_count=step_count,
                    trace_ref=trace_ref,
                    raw_text=output_text,
                    recorded_count=len(recorded_job_ids),
                    new_count=len(new_job_ids),
                )

            retry_requested = False
            handled_tool = False
            for item in output_items:
                if str(item.get("type") or "") != "function_call":
                    continue
                handled_tool = True
                name = str(item.get("name") or "")
                raw_arguments = str(item.get("arguments") or "{}")
                try:
                    arguments = json.loads(raw_arguments)
                except Exception:
                    arguments = {}

                is_observation_tool = self._is_observation_tool(name)
                current_page_key = ""
                current_page_label = ""
                if phase.slug == "job_retrieval":
                    current_page_key = self._job_retrieval_page_fingerprint(
                        current_url=current_url,
                        latest_snapshot_payload=latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else None,
                        last_payload=last_payload if isinstance(last_payload, dict) else None,
                    )
                    current_page_label = self._extract_page_label(latest_snapshot_payload) or self._extract_page_label(last_payload)

                if name == "phase_result":
                    if (
                        phase.slug == "job_retrieval"
                        and extracted_page_key
                        and current_page_key
                        and current_page_key == extracted_page_key
                    ):
                        history_items = [
                            self._context_item(
                                self._job_retrieval_extracted_page_message(
                                    current_url=current_url,
                                    page_label=current_page_label,
                                )
                            )
                        ]
                        retry_requested = True
                        break
                    return BrowserPhaseResult(
                        status=str(arguments.get("status") or "blocked"),
                        reason_tag="phase_result",
                        summary=str(arguments.get("summary") or ""),
                        current_url=current_url,
                        step_count=step_count,
                        trace_ref=trace_ref,
                        raw_text=output_text,
                        recorded_count=len(recorded_job_ids),
                        new_count=len(new_job_ids),
                    )

                if name not in tool_names:
                    history_items = [
                        self._context_item(
                            self._tool_unavailable_message(
                                phase=phase,
                                current_url=current_url,
                                tool_name=name,
                            )
                        )
                    ]
                    retry_requested = True
                    break

                if (
                    phase.slug != "job_retrieval"
                    and self._is_page_settle_action(name)
                    and self._payload_has_loading_signal(
                        latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else None
                    )
                ):
                    history_items = [
                        self._context_item(
                            self._page_still_loading_message(
                                phase=phase,
                                current_url=current_url,
                            )
                        )
                    ]
                    retry_requested = True
                    break

                if (
                    phase.slug == "session_preparation"
                    and name == "browser_navigate"
                    and isinstance(arguments, dict)
                    and self._is_blocked_session_navigation_target(str(arguments.get("url") or ""))
                    and last_payload is not None
                    and self._payload_has_visible_auth_action(last_payload, phase=phase)
                ):
                    history_items = [
                        self._context_item(
                            self._navigation_guard_message(
                                phase=phase,
                                current_url=current_url,
                                target_url=str(arguments.get("url") or ""),
                            )
                        )
                    ]
                    retry_requested = True
                    break

                if (
                    phase.slug == "job_retrieval"
                    and current_page_key
                    and current_page_key in recorded_page_fingerprints
                    and (is_observation_tool or name == "browser_evaluate")
                ):
                    history_items = [
                        self._context_item(
                            self._job_retrieval_page_recorded_message(
                                current_url=current_url,
                                page_label=current_page_label,
                            )
                        )
                    ]
                    retry_requested = True
                    break
                if (
                    phase.slug == "job_retrieval"
                    and primary_read_page_key
                    and current_page_key
                    and current_page_key == primary_read_page_key
                    and name not in {"browser_evaluate", "record_jobs", "browser_snapshot"}
                    and not is_observation_tool
                ):
                    history_items = [
                        self._context_item(
                            self._job_retrieval_snapshot_first_message(
                                current_url=current_url,
                                page_label=current_page_label,
                            )
                        )
                    ]
                    retry_requested = True
                    break
                if phase.slug == "job_retrieval" and name == "record_jobs":
                    signal_payload = (
                        latest_snapshot_payload
                        if isinstance(latest_snapshot_payload, dict)
                        else last_payload
                        if isinstance(last_payload, dict)
                        else None
                    )
                    missing_url_count = self._record_jobs_missing_url_count(
                        arguments if isinstance(arguments, dict) else None,
                        current_url=current_url,
                    )
                    if missing_url_count and self._payload_has_job_results_signal(signal_payload):
                        page_label = self._extract_page_label(signal_payload)
                        page_key = self._job_retrieval_page_fingerprint(
                            current_url=current_url,
                            latest_snapshot_payload=latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else None,
                            last_payload=last_payload if isinstance(last_payload, dict) else None,
                        )
                        history_items = [
                            self._context_item(
                                self._job_retrieval_missing_urls_message(
                                    current_url=current_url,
                                    page_label=page_label,
                                    missing_count=missing_url_count,
                                )
                            )
                        ]
                        if "browser_evaluate" in tool_names:
                            if page_key and page_key == pagewide_retry_page_key and page_key != click_recovery_page_key and "browser_click" in tool_names:
                                click_recovery_page_key = page_key
                                history_items.append(
                                    self._context_item(
                                        self._job_retrieval_missing_urls_click_message(
                                            current_url=current_url,
                                            page_label=page_label,
                                            missing_count=missing_url_count,
                                        )
                                    )
                                )
                            else:
                                if page_key:
                                    pagewide_retry_page_key = page_key
                                history_items.append(
                                    self._context_item(
                                        self._job_retrieval_missing_urls_pagewide_message(
                                            current_url=current_url,
                                            page_label=page_label,
                                        )
                                    )
                                )
                        retry_requested = True
                        break
                if (
                    phase.slug == "job_retrieval"
                    and extracted_page_key
                    and current_page_key
                    and current_page_key == extracted_page_key
                    and name != "record_jobs"
                ):
                    history_items = [
                        self._context_item(
                            self._job_retrieval_extracted_page_message(
                                current_url=current_url,
                                page_label=current_page_label,
                            )
                        )
                    ]
                    retry_requested = True
                    break
                if is_observation_tool and observation_streak >= 2 and (
                    not last_observation_url or current_url == last_observation_url
                ):
                    history_items = [
                        self._context_item(self._observation_guard_message(phase=phase, current_url=current_url))
                    ]
                    retry_requested = True
                    break

                if (
                    phase.slug == "job_retrieval"
                    and name == "browser_evaluate"
                    and self._job_retrieval_requires_pagewide_evaluate(arguments)
                ):
                    history_items = [
                        self._context_item(
                            self._job_retrieval_pagewide_evaluate_message(
                                current_url=current_url,
                                page_label=current_page_label,
                            )
                        )
                    ]
                    retry_requested = True
                    break

                step_count += 1
                for attempt in range(1, max(1, int(self.config.max_step_retries or 0)) + 2):
                    site_store.save_browser_session(
                        site_key,
                        {
                            "current_step_id": f"{phase.slug}:{name}",
                            "current_step_attempt": attempt,
                            "current_step_status": "running",
                            "expected_outcome": f"{phase.slug}:{name}",
                            "last_step_error": "",
                            "browser_status": "running",
                        },
                    )
                    try:
                        if name == "record_jobs":
                            payload = self._record_jobs_payload(
                                site_store=site_store,
                                site_key=site_key,
                                session_id=session_id,
                                turn_id=turn_id,
                                batch_id=batch_id,
                                current_url=current_url,
                                arguments=arguments if isinstance(arguments, dict) else {},
                            )
                        else:
                            payload = await bridge.call_tool(session, name, arguments if isinstance(arguments, dict) else {})
                    except Exception as exc:
                        payload = {"isError": True, "error": str(exc), "tool": name}

                    error_text = ""
                    if bool(payload.get("isError")):
                        error_text = MCPToolBridge.summarize_tool_output(payload, ignore_phrases=phase.ignore_phrases)
                    elif name == "browser_evaluate":
                        logical_error = self._browser_evaluate_error_text(payload)
                        if logical_error:
                            payload = {**payload, "isError": True, "error": logical_error}
                            error_text = logical_error
                    structured = payload.get("structuredContent")
                    if isinstance(structured, dict) and name == "record_jobs":
                        recorded_page_key = self._job_retrieval_page_fingerprint(
                            current_url=current_url,
                            latest_snapshot_payload=latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else None,
                            last_payload=last_payload if isinstance(last_payload, dict) else None,
                        )
                        if recorded_page_key:
                            recorded_page_fingerprints.add(recorded_page_key)
                        for job_id in structured.get("job_ids") or []:
                            if isinstance(job_id, str) and job_id.strip():
                                recorded_job_ids.add(job_id.strip())
                        for job_id in structured.get("new_job_ids") or []:
                            if isinstance(job_id, str) and job_id.strip():
                                new_job_ids.add(job_id.strip())
                    current_url = MCPToolBridge.extract_current_url(payload) or current_url or str(entry_url or "")

                    trace_ref = site_store.append_step_trace(
                        site_key,
                        turn_id,
                        {
                            "phase": phase.slug,
                            "step_id": f"{phase.slug}:{name}",
                            "attempt": attempt,
                            "tool_name": name,
                            "arguments": arguments,
                            "result": "error" if error_text else "ok",
                            "output": MCPToolBridge.summarize_tool_output(payload, ignore_phrases=phase.ignore_phrases),
                        },
                    )
                    site_store.save_browser_session(
                        site_key,
                        {
                            "current_step_id": f"{phase.slug}:{name}",
                            "current_step_attempt": attempt,
                            "current_step_status": "error" if error_text else "done",
                            "last_step_error": error_text,
                            "current_trace_ref": trace_ref,
                            "last_known_url": current_url,
                        },
                    )
                    self._append_recent_step(
                        recent_steps,
                        tool_name=name,
                        arguments=arguments,
                        error_text=error_text,
                        current_url=current_url,
                        payload=payload,
                    )
                    tool_feedback = self._context_item(
                        MCPToolBridge.build_tool_feedback(
                            name,
                            payload,
                            ignore_phrases=phase.ignore_phrases,
                        )
                    )
                    fresh_snapshot_captured = False
                    if name == "browser_snapshot":
                        if not bool(payload.get("isError")):
                            latest_snapshot_payload = payload
                            fresh_snapshot_captured = True
                    elif not error_text:
                        if phase.slug == "job_retrieval" and self._is_job_retrieval_page_action(name):
                            await self._sleep(self.JOB_RETRIEVAL_PAGE_ACTION_WAIT_SECONDS)
                        live_snapshot_payload = await self._capture_snapshot_payload(
                            bridge=bridge,
                            session=session,
                            tool_names=tool_names,
                        )
                        if (
                            isinstance(live_snapshot_payload, dict)
                            and live_snapshot_payload
                            and not bool(live_snapshot_payload.get("isError"))
                        ):
                            latest_snapshot_payload = live_snapshot_payload
                            snapshot_url = MCPToolBridge.extract_current_url(live_snapshot_payload)
                            if snapshot_url:
                                current_url = snapshot_url
                            fresh_snapshot_captured = True
                    if fresh_snapshot_captured and not error_text and phase.slug != "job_retrieval":
                        settled_payload, settled_url = await self._wait_for_page_settle(
                            tool_name=name,
                            latest_snapshot_payload=latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else None,
                            current_url=current_url,
                            bridge=bridge,
                            session=session,
                            tool_names=tool_names,
                        )
                        if isinstance(settled_payload, dict):
                            latest_snapshot_payload = settled_payload
                        if settled_url:
                            current_url = settled_url
                    if is_observation_tool:
                        if observation_streak > 0 and (
                            not last_observation_url or current_url == last_observation_url
                        ):
                            observation_streak += 1
                        else:
                            observation_streak = 1
                        last_observation_url = current_url
                    else:
                        observation_streak = 0
                        last_observation_url = ""
                    latest_page_key = ""
                    if phase.slug == "job_retrieval":
                        latest_page_key = self._job_retrieval_page_fingerprint(
                            current_url=current_url,
                            latest_snapshot_payload=latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else None,
                            last_payload=last_payload if isinstance(last_payload, dict) else None,
                        )
                        if name == "record_jobs" and not error_text:
                            extracted_page_key = ""
                            primary_read_page_key = ""
                            pagewide_retry_page_key = ""
                            click_recovery_page_key = ""
                        elif (
                            name == "browser_evaluate"
                            and not error_text
                            and self._payload_has_extracted_jobs(payload)
                        ):
                            extracted_page_key = latest_page_key or current_page_key
                            primary_read_page_key = ""
                            pagewide_retry_page_key = ""
                            click_recovery_page_key = ""
                        elif (
                            name == "browser_evaluate"
                            and not error_text
                            and primary_read_page_key
                            and latest_page_key
                            and latest_page_key == primary_read_page_key
                        ):
                            primary_read_page_key = ""
                        elif (
                            extracted_page_key
                            and latest_page_key
                            and latest_page_key != extracted_page_key
                            and not error_text
                        ):
                            extracted_page_key = ""
                        if (
                            primary_read_page_key
                            and latest_page_key
                            and latest_page_key != primary_read_page_key
                            and not error_text
                        ):
                            primary_read_page_key = ""
                        if (
                            pagewide_retry_page_key
                            and latest_page_key
                            and latest_page_key != pagewide_retry_page_key
                            and not error_text
                        ):
                            pagewide_retry_page_key = ""
                        if (
                            click_recovery_page_key
                            and latest_page_key
                            and latest_page_key != click_recovery_page_key
                            and not error_text
                        ):
                            click_recovery_page_key = ""
                    page_label = self._extract_page_label(latest_snapshot_payload) or self._extract_page_label(last_payload)
                    signal_payload = (
                        latest_snapshot_payload
                        if isinstance(latest_snapshot_payload, dict)
                        else last_payload
                        if isinstance(last_payload, dict)
                        else payload
                    )
                    if (
                        phase.slug == "job_retrieval"
                        and name == "browser_evaluate"
                        and error_text
                        and self._is_browser_evaluate_serialization_error(error_text)
                    ):
                        history_items = [tool_feedback]
                        history_items.append(
                            self._context_item(
                                self._job_retrieval_serialization_error_message(
                                    current_url=current_url,
                                    page_label=page_label,
                                )
                            )
                        )
                        last_payload = latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else payload
                        retry_requested = True
                        break
                    if (
                        phase.slug != "job_retrieval"
                        and not error_text
                        and self._is_job_retrieval_page_action(name)
                        and self._payload_has_loading_signal(latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else None)
                    ):
                        history_items = [tool_feedback]
                        if fresh_snapshot_captured and name != "browser_snapshot":
                            history_items.append(self._context_item(self._live_snapshot_primary_message()))
                        history_items.append(
                            self._context_item(
                                self._page_still_loading_message(
                                    phase=phase,
                                    current_url=current_url,
                                )
                            )
                        )
                        last_payload = latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else payload
                        retry_requested = True
                        break
                    if (
                        phase.slug == "job_retrieval"
                        and name == "browser_evaluate"
                        and not error_text
                        and self._payload_has_empty_extracted_jobs(payload)
                        and self._payload_has_job_results_signal(signal_payload)
                    ):
                        page_key = latest_page_key or current_page_key
                        if page_key and page_key != pagewide_retry_page_key:
                            pagewide_retry_page_key = page_key
                            history_items = [tool_feedback]
                            if fresh_snapshot_captured and name != "browser_snapshot":
                                history_items.append(self._context_item(self._live_snapshot_primary_message()))
                            history_items.append(
                                self._context_item(
                                    self._job_retrieval_pagewide_evaluate_message(
                                        current_url=current_url,
                                        page_label=page_label,
                                    )
                                )
                            )
                            last_payload = latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else payload
                            retry_requested = True
                            break
                        if page_key and page_key != click_recovery_page_key and "browser_click" in tool_names:
                            click_recovery_page_key = page_key
                            history_items = [tool_feedback]
                            if fresh_snapshot_captured and name != "browser_snapshot":
                                history_items.append(self._context_item(self._live_snapshot_primary_message()))
                            history_items.append(
                                self._context_item(
                                    self._job_retrieval_click_then_pagewide_message(
                                        current_url=current_url,
                                        page_label=page_label,
                                    )
                                )
                            )
                            last_payload = latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else payload
                            retry_requested = True
                            break
                        history_items = [tool_feedback]
                        if fresh_snapshot_captured and name != "browser_snapshot":
                            history_items.append(self._context_item(self._live_snapshot_primary_message()))
                        history_items.append(
                            self._context_item(
                                self._job_retrieval_empty_extraction_message(
                                    current_url=current_url,
                                    page_label=page_label,
                                )
                            )
                        )
                        last_payload = latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else payload
                        retry_requested = True
                        break
                    if (
                        phase.slug == "job_retrieval"
                        and not error_text
                        and self._is_job_retrieval_page_action(name)
                    ):
                        extracted_page_key = ""
                        primary_read_page_key = latest_page_key or current_page_key
                        pagewide_retry_page_key = ""
                        click_recovery_page_key = ""
                        recent_steps.clear()
                        history_items = [tool_feedback]
                        if fresh_snapshot_captured and name != "browser_snapshot":
                            history_items.append(self._context_item(self._live_snapshot_primary_message()))
                        history_items.append(
                            self._context_item(
                                self._job_retrieval_page_action_completed_message(tool_name=name)
                            )
                        )
                        if primary_read_page_key:
                            history_items.append(
                                self._context_item(
                                    self._job_retrieval_snapshot_first_message(
                                        current_url=current_url,
                                        page_label=self._extract_page_label(latest_snapshot_payload) or self._extract_page_label(last_payload),
                                    )
                                )
                            )
                        last_payload = latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else payload
                    else:
                        history_items = [tool_feedback]
                        if fresh_snapshot_captured and name != "browser_snapshot":
                            history_items.append(self._context_item(self._live_snapshot_primary_message()))
                        if (
                            phase.slug == "job_retrieval"
                            and name == "browser_evaluate"
                            and not error_text
                            and self._payload_has_extracted_jobs(payload)
                        ):
                            history_items.append(self._context_item(self._job_retrieval_record_jobs_message()))
                        if phase.slug == "job_retrieval" and name == "record_jobs" and not error_text:
                            history_items.append(
                                self._context_item(
                                    self._job_retrieval_page_recorded_message(
                                        current_url=current_url,
                                        page_label=self._extract_page_label(latest_snapshot_payload) or self._extract_page_label(last_payload),
                                    )
                                )
                            )
                    last_payload = latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else payload
                    if not error_text:
                        break
                    if attempt > int(self.config.max_step_retries or 0):
                        return BrowserPhaseResult(
                            status="failed",
                            reason_tag="tool_call_failed",
                            summary=error_text,
                            current_url=current_url,
                            step_count=step_count,
                            trace_ref=trace_ref,
                            raw_text=output_text,
                            recorded_count=len(recorded_job_ids),
                            new_count=len(new_job_ids),
                        )
                    await self._sleep(min(2.0, max(0.25, float(self.config.step_timeout_seconds or 1) / 10.0)))
                    snapshot_payload = await self._capture_snapshot_payload(
                        bridge=bridge,
                        session=session,
                        tool_names=tool_names,
                    )
                    snapshot_text = ""
                    if (
                        isinstance(snapshot_payload, dict)
                        and snapshot_payload
                        and not bool(snapshot_payload.get("isError"))
                    ):
                        latest_snapshot_payload = snapshot_payload
                        snapshot_url = MCPToolBridge.extract_current_url(snapshot_payload)
                        if snapshot_url:
                            current_url = snapshot_url
                    history_items = [
                        tool_feedback,
                        self._context_item(
                            (
                                f"The previous tool call {name} failed with: {error_text}. "
                                "Wait briefly if needed, then continue from the current page state with the official tools."
                            )
                        ),
                    ]
                    if (
                        isinstance(snapshot_payload, dict)
                        and snapshot_payload
                        and not bool(snapshot_payload.get("isError"))
                    ):
                        history_items.append(self._context_item(self._live_snapshot_primary_message()))
                    retry_requested = True
                    break
                if retry_requested:
                    break
            if retry_requested:
                continue
            if not handled_tool:
                return BrowserPhaseResult(
                    status="failed",
                    reason_tag="missing_tool_call",
                    summary="model returned no tool calls",
                    current_url=current_url,
                    step_count=step_count,
                    trace_ref=trace_ref,
                    raw_text=output_text,
                    recorded_count=len(recorded_job_ids),
                    new_count=len(new_job_ids),
                )

        return BrowserPhaseResult(
            status="failed",
            reason_tag="phase_timeout",
            summary=f"phase {phase.slug} exceeded limits",
            current_url=current_url,
            step_count=step_count,
            trace_ref=trace_ref,
            recorded_count=len(recorded_job_ids),
            new_count=len(new_job_ids),
        )
