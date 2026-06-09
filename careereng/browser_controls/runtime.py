"""Stateless Responses loop that executes local Playwright MCP function tools."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import json
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import parse_qsl, urlparse

import anyio
import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from careereng.browser_context import BrowserPhaseMemory
from careereng.browser_controls.bridge import MCPToolBridge
from careereng.browser_controls.prompting import PhasePrompt
from careereng.evolution.browser_control.events import append_phase_event
from careereng.metrics import LLMUsageRecorder, extract_usage


@dataclass(frozen=True)
class BrowserRuntimeConfig:
    api_base: str
    api_key: str
    model: str
    reasoning_effort: str = "high"
    phase_timeout_seconds: int = 180
    step_timeout_seconds: int = 90
    max_step_retries: int = 1
    max_phase_steps: int = 24
    metrics_workspace: str = ""
    retrieval_history_stop_success_ratio: float = 0.4
    retrieval_history_stop_min_page_jobs: int = 10
    same_url_no_progress_tool_call_limit: int = 0
    same_url_no_progress_token_limit: int = 0
    apply_same_url_no_progress_tool_call_limit: int = 0
    apply_same_url_no_progress_token_limit: int = 0
    recovery_snapshot_timeout_seconds: int = 90
    recovery_max_attempts: int = 3


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
    def __init__(
        self,
        *,
        api_base: str,
        api_key: str,
        timeout_seconds: float,
        metrics_recorder: LLMUsageRecorder | None = None,
    ):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = max(30.0, float(timeout_seconds or 30.0))
        self.metrics_recorder = metrics_recorder
        self.client = self._build_client(timeout_seconds=self.timeout_seconds)
        self._closed = False

    def _build_client(self, *, timeout_seconds: float) -> AsyncOpenAI:
        return AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.api_base,
            timeout=max(30.0, float(timeout_seconds or 30.0)),
        )

    def ensure_timeout(self, timeout_seconds: float) -> None:
        desired = max(30.0, float(timeout_seconds or 30.0))
        if desired <= self.timeout_seconds:
            return
        self.timeout_seconds = desired

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self.client, "close", None)
        if not callable(close):
            return
        result = close()
        if inspect.isawaitable(result):
            await result

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

    @staticmethod
    def _has_salvageable_stream_output(
        *,
        output_text_parts: list[str],
        output_items: list[dict[str, Any]],
        partial_items: dict[str, dict[str, Any]],
    ) -> bool:
        if any(str(part or "").strip() for part in output_text_parts):
            return True
        if any(isinstance(item, dict) and item for item in output_items):
            return True
        for item in partial_items.values():
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "") == "function_call" and str(item.get("arguments") or "").strip():
                return True
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and str(block.get("text") or "").strip():
                    return True
        return False

    @staticmethod
    def _is_malformed_final_response_error(exc: TypeError) -> bool:
        message = str(exc)
        return "NoneType" in message and "iterable" in message

    async def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        request_timeout_seconds = max(
            1.0,
            float(payload.get("_request_timeout_seconds") or self.timeout_seconds or 30.0),
        )
        metrics_context = payload.get("_metrics_context") if isinstance(payload.get("_metrics_context"), dict) else {}
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
        final_usage: Any = None
        stream_final_parse_warning = ""
        with_options = getattr(self.client, "with_options", None)
        stream_client = with_options(timeout=request_timeout_seconds) if callable(with_options) else self.client
        try:
            with anyio.fail_after(request_timeout_seconds):
                async with stream_client.responses.stream(**stream_payload) as stream:
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
                    final_usage = getattr(final, "usage", None)
        except TypeError as exc:
            if not self._is_malformed_final_response_error(exc) or not self._has_salvageable_stream_output(
                output_text_parts=output_text_parts,
                output_items=output_items,
                partial_items=partial_items,
            ):
                raise
            stream_final_parse_warning = str(exc)
        except TimeoutError as exc:
            self._record_metric(
                model=str(payload.get("model") or ""),
                started=started,
                status="error",
                error_type="timeout",
                context=metrics_context,
                stream_event_types=stream_event_types,
            )
            raise httpx.TimeoutException(
                f"responses stream timed out after {request_timeout_seconds:.1f}s"
            ) from exc
        except APIStatusError as exc:
            self._record_metric(
                model=str(payload.get("model") or ""),
                started=started,
                status="error",
                error_type=f"http_{getattr(exc, 'status_code', 'unknown')}",
                context=metrics_context,
                stream_event_types=stream_event_types,
            )
            body = getattr(exc, "body", None)
            detail = json.dumps(body, ensure_ascii=False) if body is not None else str(exc)
            raise RuntimeError(detail[:2000] or f"responses api error {getattr(exc, 'status_code', 'unknown')}") from exc
        except (APIConnectionError, APITimeoutError) as exc:
            self._record_metric(
                model=str(payload.get("model") or ""),
                started=started,
                status="error",
                error_type=exc.__class__.__name__,
                context=metrics_context,
                stream_event_types=stream_event_types,
            )
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
        usage_payload = extract_usage(final_usage)
        if usage_payload:
            data["usage"] = usage_payload
        if output_text:
            data["output_text"] = output_text
        if stream_final_parse_warning:
            data["stream_final_parse_warning"] = stream_final_parse_warning
        self._record_metric(
            model=str(payload.get("model") or ""),
            started=started,
            status="ok",
            usage=final_usage,
            error_type="stream_final_parse_warning" if stream_final_parse_warning else "",
            context=metrics_context,
            stream_event_types=stream_event_types,
            tool_call_count=sum(1 for item in output_items if isinstance(item, dict) and item.get("type") == "function_call"),
        )
        return data

    def _record_metric(
        self,
        *,
        model: str,
        started: float,
        status: str,
        usage: Any = None,
        error_type: str = "",
        context: dict[str, Any] | None = None,
        stream_event_types: list[str] | None = None,
        tool_call_count: int | None = None,
    ) -> None:
        recorder = self.metrics_recorder
        if recorder is None:
            return
        context_payload = dict(context or {})
        if tool_call_count is None:
            tool_call_count = 0
        recorder.record(
            provider="openai",
            model=model,
            api_type="responses_stream",
            operation="browser_phase",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            status=status,
            error_type=error_type,
            stream_event_types=list(stream_event_types or []),
            tool_call_count=int(tool_call_count),
            **context_payload,
            **extract_usage(usage),
        )


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
    FORM_STATE_ACTION_TOOLS = (
        "browser_fill_form",
        "browser_type",
        "browser_select_option",
        "browser_file_upload",
    )
    OBSERVATION_ONLY_TOOLS = (
        "browser_snapshot",
        "browser_console_messages",
    )
    NO_PROGRESS_INTERNAL_TOOLS = (
        "request_context",
        "update_phase_memory",
    )
    JOB_RETRIEVAL_EMPTY_EVALUATE_MAX_SAME_PAGE_ATTEMPTS_WITH_CARRY_FORWARD = 8
    JOB_RETRIEVAL_EMPTY_EVALUATE_MAX_SAME_PAGE_ATTEMPTS_WITHOUT_CARRY_FORWARD = 15
    SAME_URL_NO_PROGRESS_TOOL_CALL_LIMIT = 5
    SAME_URL_NO_PROGRESS_TOKEN_LIMIT = 60000
    RETRIEVAL_POLICY_PAGINATION_VIOLATION_LIMIT = 2
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
        self._owns_responses_client = responses_client is None
        self.responses = responses_client or ResponsesClient(
            api_base=config.api_base,
            api_key=config.api_key,
            timeout_seconds=max(config.phase_timeout_seconds, config.step_timeout_seconds) + 30,
            metrics_recorder=LLMUsageRecorder(config.metrics_workspace) if config.metrics_workspace else None,
        )
        self.sleep_fn = sleep_fn or anyio.sleep

    async def aclose(self) -> None:
        if not self._owns_responses_client:
            return
        aclose = getattr(self.responses, "aclose", None)
        if not callable(aclose):
            return
        result = aclose()
        if inspect.isawaitable(result):
            await result

    async def _sleep(self, seconds: float) -> None:
        result = self.sleep_fn(seconds)
        if inspect.isawaitable(result):
            await result

    def _same_url_no_progress_tool_call_limit(self, phase: PhasePrompt) -> int:
        base = int(self.config.same_url_no_progress_tool_call_limit or self.SAME_URL_NO_PROGRESS_TOOL_CALL_LIMIT)
        if phase.slug == "apply":
            return int(self.config.apply_same_url_no_progress_tool_call_limit or base)
        return base

    def _same_url_no_progress_token_limit(self, phase: PhasePrompt) -> int:
        base = int(self.config.same_url_no_progress_token_limit or self.SAME_URL_NO_PROGRESS_TOKEN_LIMIT)
        if phase.slug == "apply":
            return int(self.config.apply_same_url_no_progress_token_limit or base)
        return base

    @staticmethod
    def _is_observation_tool(name: str) -> bool:
        normalized = str(name or "").strip().lower()
        return normalized in BrowserPhaseRuntime.OBSERVATION_ONLY_TOOLS

    @staticmethod
    def _is_no_progress_internal_tool(name: str) -> bool:
        normalized = str(name or "").strip().lower()
        return normalized in BrowserPhaseRuntime.NO_PROGRESS_INTERNAL_TOOLS

    @staticmethod
    def _observation_guard_applies(
        *,
        phase: PhasePrompt,
        observation_streak: int,
        current_url: str,
        last_observation_url: str,
        apply_upload_requires_observation: bool,
        apply_upload_modal_unresolved: bool,
    ) -> bool:
        if observation_streak < 2:
            return False
        if last_observation_url and current_url and current_url != last_observation_url:
            return False
        if phase.slug == "apply" and (apply_upload_requires_observation or apply_upload_modal_unresolved):
            return False
        return True

    @staticmethod
    def _same_page_evaluate_guard_applies(
        *,
        phase: PhasePrompt,
        same_page_evaluate_streak: int,
        current_url: str,
        last_evaluate_url: str,
        apply_upload_requires_observation: bool,
        apply_upload_modal_unresolved: bool,
    ) -> bool:
        if phase.slug == "job_retrieval":
            return False
        if same_page_evaluate_streak < 2:
            return False
        if last_evaluate_url and current_url and current_url != last_evaluate_url:
            return False
        if phase.slug == "apply" and (apply_upload_requires_observation or apply_upload_modal_unresolved):
            return False
        return True

    @staticmethod
    def _no_progress_internal_guard_applies(
        *,
        phase: PhasePrompt,
        no_progress_internal_streak: int,
        current_url: str,
        last_no_progress_internal_url: str,
        apply_upload_requires_observation: bool,
        apply_upload_modal_unresolved: bool,
    ) -> bool:
        if no_progress_internal_streak < 2:
            return False
        if last_no_progress_internal_url and current_url and current_url != last_no_progress_internal_url:
            return False
        if phase.slug == "apply" and (apply_upload_requires_observation or apply_upload_modal_unresolved):
            return False
        return True

    @classmethod
    def _filter_tools_for_observation_guard(
        cls, response_tools: list[dict[str, Any]], available_tool_names: set[str]
    ) -> tuple[list[dict[str, Any]], set[str]]:
        filtered_tools: list[dict[str, Any]] = []
        filtered_names = set(available_tool_names)
        for tool in response_tools:
            tool_name = str(tool.get("name") or "").strip()
            if cls._is_observation_tool(tool_name):
                filtered_names.discard(tool_name)
                continue
            filtered_tools.append(tool)
        return filtered_tools, filtered_names

    @staticmethod
    def _filter_tool_by_name(
        response_tools: list[dict[str, Any]], available_tool_names: set[str], blocked_tool_name: str
    ) -> tuple[list[dict[str, Any]], set[str]]:
        blocked = str(blocked_tool_name or "").strip().lower()
        if not blocked:
            return response_tools, available_tool_names
        filtered_tools: list[dict[str, Any]] = []
        filtered_names = set(available_tool_names)
        filtered_names.discard(blocked)
        for tool in response_tools:
            tool_name = str(tool.get("name") or "").strip().lower()
            if tool_name == blocked:
                continue
            filtered_tools.append(tool)
        return filtered_tools, filtered_names

    @staticmethod
    def _filter_tools_by_names(
        response_tools: list[dict[str, Any]], available_tool_names: set[str], blocked_tool_names: set[str]
    ) -> tuple[list[dict[str, Any]], set[str]]:
        blocked = {str(name or "").strip().lower() for name in blocked_tool_names if str(name or "").strip()}
        if not blocked:
            return response_tools, available_tool_names
        filtered_tools: list[dict[str, Any]] = []
        filtered_names = set(available_tool_names)
        for name in blocked:
            filtered_names.discard(name)
        for tool in response_tools:
            tool_name = str(tool.get("name") or "").strip().lower()
            if tool_name in blocked:
                continue
            filtered_tools.append(tool)
        return filtered_tools, filtered_names

    @staticmethod
    def _apply_file_upload_use_staged_path_message(*, current_url: str, staged_path: str, attempted_paths: list[str]) -> str:
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        attempted = ", ".join(attempted_paths[:3]) if attempted_paths else "(none)"
        return (
            "The last browser_file_upload call used a local path that is not allowed for this active browser run.\n"
            f"{url_line}"
            f"Use only this run-local staged PDF path for upload: {staged_path}\n"
            f"Do not retry the old source path(s): {attempted}\n"
            "If upload is still needed, call browser_file_upload again with exactly that staged path."
        )

    @staticmethod
    def _apply_file_upload_empty_paths_message(*, current_url: str, staged_path: str) -> str:
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        staged_line = f"Use this run-local staged PDF path if upload is needed: {staged_path}\n" if staged_path else ""
        return (
            "The last browser_file_upload call provided no file paths.\n"
            f"{url_line}"
            f"{staged_line}"
            "Do not call browser_file_upload with an empty paths list."
        )

    @staticmethod
    def _apply_file_upload_observe_message(*, current_url: str, staged_path: str) -> str:
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        staged_line = f"Uploaded file path: {staged_path}\n" if staged_path else ""
        return (
            "Runtime note: browser_file_upload already succeeded for the current apply page in this run.\n"
            f"{url_line}"
            f"{staged_line}"
            "Continue from the active skills, current phase memory, and fresh current live page."
        )

    @staticmethod
    def _apply_file_upload_confirmed_message(*, current_url: str, staged_path: str) -> str:
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        staged_line = f"Confirmed uploaded file path: {staged_path}\n" if staged_path else ""
        return (
            "Runtime note: the current apply page now confirms the staged file is uploaded.\n"
            f"{url_line}"
            f"{staged_line}"
            "Continue from the active skills, current phase memory, and current live page."
        )

    @staticmethod
    def _apply_file_upload_repeat_message(*, current_url: str, staged_path: str) -> str:
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        staged_line = f"Already uploaded file path: {staged_path}\n" if staged_path else ""
        return (
            "Runtime note: this unchanged apply page already completed a browser_file_upload call with the run-local staged PDF.\n"
            f"{url_line}"
            f"{staged_line}"
            "Continue from the active skills, current phase memory, and current live page."
        )

    @staticmethod
    def _apply_file_upload_requires_ready_message(*, current_url: str, staged_path: str) -> str:
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        staged_line = f"Use this run-local staged PDF path when upload is ready: {staged_path}\n" if staged_path else ""
        return (
            "The current live page is not upload-ready yet.\n"
            f"{url_line}"
            f"{staged_line}"
            "Do not call browser_file_upload yet. First use the page's own upload flow until the current live page clearly shows either "
            "an active file chooser or a direct file-upload field, then call browser_file_upload."
        )

    @staticmethod
    def _apply_file_upload_modal_unresolved_message(*, current_url: str, staged_path: str, chooser_count: int) -> str:
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        staged_line = f"Last staged PDF path: {staged_path}\n" if staged_path else ""
        chooser_line = f"Active file choosers still visible: {max(1, int(chooser_count or 0))}\n"
        return (
            "Runtime note: the last upload attempt did not return to a normal form page.\n"
            f"{url_line}"
            f"{staged_line}"
            f"{chooser_line}"
            "The current live page still shows unresolved file chooser modal state. This runtime only allows a blocked phase_result from this state unless the modal resolves."
        )

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
    def _same_page_evaluate_guard_message(*, phase: PhasePrompt, current_url: str) -> str:
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        return (
            f"You have evaluated the same page multiple times during `{phase.slug}` without a page-changing action or state update.\n"
            f"{url_line}"
            "Do not call browser_evaluate again right now. Use the current page evidence, active skill guidance, "
            "and available phase tools to take one recovery step: use an official browser action, navigate/back/re-enter the flow, "
            "record the observed state with the phase recording tool if available, or finish the phase as blocked/done with phase_result."
        )

    @staticmethod
    def _no_progress_internal_guard_message(*, phase: PhasePrompt, current_url: str) -> str:
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        return (
            f"You have repeatedly used non-browser context or memory tools during `{phase.slug}` without changing the page or writing terminal state.\n"
            f"{url_line}"
            "Do not call request_context or update_phase_memory again right now. Use the current live page, active skills, and phase memory to "
            "take a concrete recovery step: use an official browser action, go back/re-enter the flow, record the terminal state with the phase-specific "
            "recording tool if available, or finish the phase as blocked/done with phase_result."
        )

    @staticmethod
    def _job_retrieval_enrichment_required_message(*, current_url: str, enrichment_needed_count: int, enrichment_job_ids: list[str]) -> str:
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        ids_line = f"Jobs needing enrichment: {', '.join(enrichment_job_ids[:5])}\n" if enrichment_job_ids else ""
        return (
            "Runtime note: the last record_jobs call found existing jobs on this same results page that still need enrichment.\n"
            f"{url_line}"
            f"Enrichment-needed count: {max(1, int(enrichment_needed_count or 0))}\n"
            f"{ids_line}"
            "Do not paginate away from this results page yet. Open or inspect the needed current-page job(s), fill missing JD/URL/posted/location with update_jobs, "
            "or finish job_retrieval as blocked if the site prevents enrichment."
        )

    @staticmethod
    def _same_url_no_progress_message(*, phase: PhasePrompt, current_url: str, tool_calls: int, tokens: int) -> str:
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        token_line = f"Same-page no-progress tokens: {tokens}\n" if tokens > 0 else ""
        return (
            f"Repeated no-progress tool calls were detected during `{phase.slug}`.\n"
            f"{url_line}"
            f"Same-page no-progress tool calls: {tool_calls}\n"
            f"{token_line}"
            "The runtime stopped this phase to avoid an expensive browser loop. "
            "Use the trace and evolution event to refine the active skill or recovery path."
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
    def _apply_recovery_page_key(
        cls,
        *,
        current_url: str,
        payload: dict[str, Any] | None,
    ) -> str:
        url_key = cls._canonicalize_runtime_url(current_url)
        title = ""
        if isinstance(payload, dict):
            title = str(MCPToolBridge.extract_page_title(payload) or "").strip().lower()
        if not url_key and not title:
            return ""
        return json.dumps({"url": url_key, "title": title}, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _apply_auth_recovery_message(*, current_url: str) -> str:
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        return (
            "Runtime note: the current apply flow is on an authentication step with a visible sign-in continuation.\n"
            f"{url_line}"
            "Do not keep calling update_jobs from this same sign-in page. "
            "Continue from the active skills, current phase memory, and current live page."
        )

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
        request_timeout_seconds = float(payload.get("_request_timeout_seconds") or 0.0)
        while True:
            try:
                return await self.responses.create(payload)
            except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.TimeoutException):
                if request_timeout_seconds > 0:
                    raise TimeoutError("model response turn timed out")  # noqa: TRY301
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

    def _response_turn_timeout_seconds(self, *, deadline: float) -> float:
        remaining = max(0.1, float(deadline - time.monotonic()))
        step_timeout = max(1.0, float(self.config.step_timeout_seconds or 30))
        recovery_timeout = max(1.0, float(self.config.recovery_snapshot_timeout_seconds or step_timeout))
        return max(0.1, min(remaining, step_timeout, recovery_timeout))

    @staticmethod
    def _browser_recovery_message(
        *,
        phase: PhasePrompt,
        reason: str,
        current_url: str,
        previous_url: str = "",
        attempt: int = 1,
        max_attempts: int = 3,
        timeout_seconds: float = 0.0,
        detail: str = "",
    ) -> str:
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        previous_line = f"Previous page URL before recovery snapshot: {previous_url}\n" if previous_url else ""
        changed_line = ""
        if previous_url and current_url:
            changed_line = f"Page URL changed during recovery: {'yes' if previous_url != current_url else 'no'}\n"
        timeout_line = f"Recovery timeout budget: {timeout_seconds:.1f}s\n" if timeout_seconds > 0 else ""
        detail_line = f"Recovery detail: {detail}\n" if detail else ""
        return (
            "Runtime recovery: a fresh live browser snapshot was captured after an interrupted or stale browser-control turn.\n"
            f"Phase: {phase.slug}\n"
            f"Reason: {reason}\n"
            f"Recovery attempt: {max(1, int(attempt or 1))}/{max(1, int(max_attempts or 1))}\n"
            f"{timeout_line}"
            f"{previous_line}"
            f"{url_line}"
            f"{changed_line}"
            f"{detail_line}"
            "Treat the attached fresh snapshot as the only source of truth for the current page. "
            "Treat old refs, previous tool arguments, and pre-recovery page observations as stale history. "
            "Do not repeat stale refs. "
            "If the current page shows a terminal state for this phase, call the phase-specific state update tool or phase_result now. "
            "If it is not terminal, continue from the current page with one concrete next action."
        )

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
            "site_job_id": {"type": "string"},
            "posted_at": {"type": "string"},
        }
        return {
            "type": "function",
            "name": "record_jobs",
            "description": "Persist the full visible job list from the current page for later retrieval and apply phases.",
            "strict": False,
            "parameters": {
                "type": "object",
                "properties": {
                    "jobs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": job_properties,
                            "required": ["title", "url"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["jobs"],
                "additionalProperties": False,
            },
        }

    @staticmethod
    def update_jobs_tool() -> dict[str, Any]:
        job_properties = {
            "job_id": {"type": "string"},
            "title": {"type": "string"},
            "url": {"type": "string"},
            "location": {"type": "string"},
            "posted_label": {"type": "string"},
            "employment_type": {"type": "string"},
            "match_label": {"type": "string"},
            "apply_state": {"type": "string"},
            "site_job_id": {"type": "string"},
            "posted_at": {"type": "string"},
            "description": {"type": "string"},
            "jd_sync_status": {"type": "string"},
            "decision_status": {"type": "string"},
            "decision_rule_source": {"type": "string"},
            "decision_rule_name": {"type": "string"},
            "site_match_signal_raw": {"type": "string"},
            "match_score_initial": {"type": "number"},
            "match_reason_initial": {"type": "string"},
            "match_score_final": {"type": "number"},
            "match_reason_final": {"type": "string"},
            "fit_apply": {"type": "boolean"},
            "fit_confidence": {"type": "number"},
            "fit_reason": {"type": "string"},
            "fit_source": {"type": "string"},
            "application_status": {"type": "string"},
            "last_apply_error": {"type": "string"},
        }
        return {
            "type": "function",
            "name": "update_jobs",
            "description": "Persist current per-job JD, decision, and application state for the active batch run.",
            "strict": False,
            "parameters": {
                "type": "object",
                "properties": {
                    "jobs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": job_properties,
                            "required": ["job_id"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["jobs"],
                "additionalProperties": False,
            },
        }

    @staticmethod
    def record_application_reviews_tool() -> dict[str, Any]:
        review_properties = {
            "title": {"type": "string"},
            "url": {"type": "string"},
            "site_job_id": {"type": "string"},
            "application_review_status": {
                "type": "string",
                "enum": ["active", "inactive", "rejected", "closed", "withdrawn", "unknown", "blocked"],
            },
            "application_review_status_raw": {"type": "string"},
            "application_review_stage": {"type": "string"},
        }
        return {
            "type": "function",
            "name": "record_application_reviews",
            "description": "Persist website-visible submitted-application review statuses for the active site.",
            "strict": False,
            "parameters": {
                "type": "object",
                "properties": {
                    "reviews": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": review_properties,
                            "required": ["title", "application_review_status"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["reviews"],
                "additionalProperties": False,
            },
        }

    @staticmethod
    def request_context_tool() -> dict[str, Any]:
        return {
            "type": "function",
            "name": "request_context",
            "description": (
                "Request an additional preloaded context bundle for the current apply phase when the live page, "
                "site skill, and lightweight facts are insufficient. This does not operate the browser."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "bundle": {"type": "string", "enum": ["apply_facts", "full_cv", "full_persona"]},
                    "reason": {"type": "string"},
                },
                "required": ["bundle", "reason"],
                "additionalProperties": False,
            },
        }

    @staticmethod
    def update_phase_memory_tool() -> dict[str, Any]:
        entry_schema = {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["key", "text"],
            "additionalProperties": False,
        }
        return {
            "type": "function",
            "name": "update_phase_memory",
            "description": (
                "Record phase-local completed, confirmed, pending, and do-not-repeat facts for the current phase. "
                "This does not operate the browser."
            ),
            "strict": False,
            "parameters": {
                "type": "object",
                "properties": {
                    "completed": {"type": "array", "items": entry_schema},
                    "confirmed": {"type": "array", "items": entry_schema},
                    "pending": {"type": "array", "items": entry_schema},
                    "do_not_repeat": {"type": "array", "items": entry_schema},
                    "metrics": {
                        "type": "object",
                        "properties": {
                            "results_count": {"type": "integer"},
                            "total_pages": {"type": "integer"},
                            "page_size": {"type": "integer"},
                        },
                        "additionalProperties": False,
                    },
                    "clear_keys": {"type": "array", "items": {"type": "string"}},
                },
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

    def _payload(
        self,
        *,
        input_items: Any,
        tools: list[dict[str, Any]],
        request_timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "input": input_items,
            "tools": tools,
            "tool_choice": "required",
        }
        effort = str(self.config.reasoning_effort or "").strip().lower()
        if effort:
            payload["reasoning"] = {"effort": effort}
        if request_timeout_seconds is not None:
            payload["_request_timeout_seconds"] = max(0.1, float(request_timeout_seconds))
        return payload

    @staticmethod
    def _response_total_tokens(response: dict[str, Any]) -> int:
        usage = response.get("usage") if isinstance(response, dict) else None
        if not isinstance(usage, dict):
            return 0
        value = usage.get("total_tokens")
        try:
            return max(0, int(value or 0))
        except Exception:
            return 0

    @classmethod
    def _phase_progress_key(
        cls,
        *,
        phase: PhasePrompt,
        current_url: str,
        latest_snapshot_payload: dict[str, Any] | None,
        last_payload: dict[str, Any] | None,
    ) -> str:
        if phase.slug == "job_retrieval":
            return cls._job_retrieval_page_fingerprint(
                current_url=current_url,
                latest_snapshot_payload=latest_snapshot_payload,
                last_payload=last_payload,
            )
        url = str(current_url or "").strip()
        title = ""
        for payload in (latest_snapshot_payload, last_payload):
            title = MCPToolBridge.extract_page_title(payload) if isinstance(payload, dict) else ""
            if title:
                break
        if url and title:
            return f"{url}#title={title.strip().lower()}"
        return url or title.strip().lower()

    @staticmethod
    def _record_jobs_policy_from_structured(
        structured: dict[str, Any],
        *,
        current_url: str,
        page_key: str,
    ) -> dict[str, Any]:
        if not isinstance(structured, dict):
            return {}

        def int_value(key: str) -> int:
            try:
                return max(0, int(structured.get(key) or 0))
            except Exception:
                return 0

        enrichment_job_ids = [
            str(job_id).strip()
            for job_id in structured.get("enrichment_job_ids") or []
            if str(job_id).strip()
        ]
        return {
            "current_url": str(current_url or "").strip(),
            "page_key": str(page_key or "").strip(),
            "recorded_count": int_value("recorded_count"),
            "new_count": int_value("new_count"),
            "existing_count": int_value("existing_count"),
            "existing_complete_count": int_value("existing_complete_count"),
            "enrichment_needed_count": int_value("enrichment_needed_count"),
            "enrichment_job_ids": enrichment_job_ids,
            "stop_recommended": bool(structured.get("stop_recommended")),
            "stop_reason": str(structured.get("stop_reason") or "").strip(),
        }

    @staticmethod
    def _record_jobs_policy_applies(
        policy: dict[str, Any],
        *,
        current_url: str,
        current_page_key: str,
    ) -> bool:
        if not isinstance(policy, dict) or not policy:
            return False
        policy_page_key = str(policy.get("page_key") or "").strip()
        if policy_page_key and current_page_key and policy_page_key == current_page_key:
            return True
        policy_url = str(policy.get("current_url") or "").strip()
        return bool(policy_url and current_url and policy_url == current_url)

    @staticmethod
    def _structured_positive_count(structured: dict[str, Any], *keys: str) -> bool:
        if not isinstance(structured, dict):
            return False
        for key in keys:
            value = structured.get(key)
            if isinstance(value, list) and value:
                return True
            try:
                if int(value or 0) > 0:
                    return True
            except Exception:
                continue
        return False

    @classmethod
    def _tool_call_made_runtime_progress(
        cls,
        *,
        phase: PhasePrompt,
        tool_name: str,
        error_text: str,
        pre_progress_key: str,
        post_progress_key: str,
        payload: dict[str, Any] | None,
    ) -> bool:
        if str(error_text or "").strip():
            return False
        if pre_progress_key and post_progress_key and pre_progress_key != post_progress_key:
            return True
        structured = payload.get("structuredContent") if isinstance(payload, dict) else None
        if tool_name == "record_jobs" and isinstance(structured, dict):
            return cls._structured_positive_count(structured, "recorded_count", "job_ids")
        if tool_name == "update_jobs" and isinstance(structured, dict):
            return cls._structured_positive_count(structured, "updated_count", "jobs", "terminal_job_ids")
        if tool_name == "record_application_reviews" and isinstance(structured, dict):
            return cls._structured_positive_count(structured, "recorded_count", "matched_count", "unmatched_count")
        if tool_name == "request_context" and isinstance(structured, dict):
            return str(structured.get("status") or "").strip().lower() == "attached"
        if tool_name == "update_phase_memory" and isinstance(structured, dict):
            return cls._structured_positive_count(
                structured,
                "completed_count",
                "confirmed_count",
                "pending_count",
                "do_not_repeat_count",
                "metrics_count",
                "cleared_count",
            )
        if phase.slug == "apply" and tool_name == "browser_click":
            return True
        if tool_name in cls.FORM_STATE_ACTION_TOOLS:
            return True
        return False

    def _record_browser_control_event(
        self,
        *,
        site_store: Any,
        event_type: str,
        batch_id: str,
        site_key: str,
        phase: PhasePrompt,
        turn_id: str,
        current_url: str,
        guard_name: str,
        trigger_values: dict[str, Any] | None = None,
        last_record_jobs_policy: dict[str, Any] | None = None,
        trace_ref: str = "",
        summary: str = "",
    ) -> None:
        workspace = getattr(site_store, "workspace", None)
        if not workspace:
            return
        try:
            append_phase_event(
                workspace=workspace,
                event_type=event_type,
                batch_id=batch_id,
                site_key=site_key,
                phase=phase.slug,
                turn_id=turn_id,
                current_url=current_url,
                guard_name=guard_name,
                trigger_values=trigger_values,
                last_record_jobs_policy=last_record_jobs_policy,
                trace_ref=trace_ref,
                summary=summary,
            )
        except Exception:
            return

    def _system_prompt(self, *, site_name: str, phase: PhasePrompt) -> str:
        return (
            "You are controlling a live browser through official Playwright MCP tools. "
            "Do not invent any local browser DSL. Use the available function tools directly. "
            "Stay inside the active site workflow. "
            "Use the current live page as the primary source of truth. "
            "Use recent browser trajectory only to remember what was just attempted; do not let it override the current live page. "
            "Use the current live page together with the active site and project skills to decide the next step. "
            "When the page requires human-only action such as password entry, MFA, verification code, CAPTCHA, or email confirmation, call phase_result with status=blocked."
        )

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
        phase_memory_summary: str = "",
    ) -> str:
        guidance = phase.combined_guidance or "No phase-specific guidance was found."
        entry_line = f"Entry URL: {entry_url}\n" if entry_url else ""
        handoff_line = f"Previous phase handoff: {phase_handoff.strip()}\n" if phase_handoff.strip() else ""
        trajectory_line = f"Recent browser trajectory:\n{recent_trajectory.strip()}\n\n" if recent_trajectory.strip() else ""
        if phase_memory_summary.strip():
            phase_memory_line = f"Current phase memory:\n{phase_memory_summary.strip()}\n\n"
        else:
            phase_memory_line = ""
        return (
            f"Site: {site_name}\n"
            f"Phase: {phase.title}\n"
            f"{entry_line}"
            f"{handoff_line}"
            "Use the current live page as the primary source of truth.\n"
            "Use recent browser trajectory only as lightweight action history; do not continue reasoning from the pre-action page once a fresh live snapshot is available.\n"
            "Use current phase memory as confirmed in-run progress, already-observed facts, pending goals, and do-not-repeat constraints; "
            "do not repeat a completed sub-step just because the fresh page no longer shows the earlier evidence directly.\n"
            "Use the active site and project skills together with the current live page to decide the next step or whether phase_result is appropriate.\n\n"
            f"{trajectory_line}"
            f"{phase_memory_line}"
            f"{guidance}\n\n"
            "Return control only by calling phase_result."
        )

    @staticmethod
    def _live_snapshot_primary_message() -> str:
        return (
            "A fresh live browser snapshot from the current page is attached separately. "
            "Use that current live page as the primary source of truth. "
            "Use recent trajectory only to remember what was just attempted."
        )

    @staticmethod
    def _staged_resume_context_items(resume_pdf_path: str) -> list[dict[str, str]]:
        path_text = str(resume_pdf_path or "").strip()
        if not path_text:
            return []
        basename = Path(path_text).name.strip()
        items = [
            {
                "role": "user",
                "content": (
                    "Current staged resume PDF for this phase "
                    f"(use this exact local path if upload is needed): {path_text}"
                ),
            }
        ]
        if basename:
            items.append(
                {
                    "role": "user",
                    "content": (
                        "Current staged resume filename for this phase "
                        f"(compare the live page's selected resume name against this basename): {basename}"
                    ),
                }
            )
        return items

    @staticmethod
    def _action_looks_like_close(arguments: dict[str, Any] | None) -> bool:
        if not isinstance(arguments, dict):
            return False
        haystack = " ".join(
            str(arguments.get(key) or "")
            for key in ("element", "button", "text", "value", "action")
        ).strip().lower()
        if not haystack:
            return False
        return bool(re.search(r"\b(close|dismiss|cancel|done|x)\b", haystack))

    def _tool_outcome_summary(
        self,
        *,
        phase: PhasePrompt,
        tool_name: str,
        arguments: dict[str, Any] | None,
        error_text: str,
        current_url: str,
        payload: dict[str, Any] | None,
        latest_snapshot_payload: dict[str, Any] | None,
        staged_resume_pdf_path: str,
    ) -> str:
        if str(error_text or "").strip():
            return self._cap_text(f"Failed: {error_text}", max_chars=180)
        signal_payload = latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else payload
        if tool_name == "record_jobs" and isinstance(payload, dict):
            structured = payload.get("structuredContent")
            if isinstance(structured, dict):
                count = len(structured.get("job_ids") or [])
                if count:
                    return f"Recorded {count} job(s) from the current results page."
        if tool_name == "update_jobs" and isinstance(payload, dict):
            structured = payload.get("structuredContent")
            if isinstance(structured, dict):
                count = len(structured.get("jobs") or [])
                if count:
                    return f"Updated {count} current job state record(s)."
        if tool_name == "request_context" and isinstance(arguments, dict):
            bundle = str(arguments.get("bundle") or "").strip()
            if bundle:
                return f"Attached additional context bundle `{bundle}`."
        if tool_name == "update_phase_memory" and isinstance(payload, dict):
            structured = payload.get("structuredContent")
            if isinstance(structured, dict):
                count_parts: list[str] = []
                for field in ("completed", "confirmed", "pending", "do_not_repeat"):
                    count = int(structured.get(f"{field}_count") or 0)
                    if count > 0:
                        count_parts.append(f"{count} {field.replace('_', ' ')}")
                cleared_count = int(structured.get("cleared_count") or 0)
                if cleared_count > 0:
                    count_parts.append(f"{cleared_count} cleared")
                if count_parts:
                    return f"Updated phase memory: {', '.join(count_parts)}."
                if str(structured.get("status") or "").strip() == "no_change":
                    return "Phase memory unchanged."
        if staged_resume_pdf_path and self._payload_confirms_staged_file_upload(signal_payload, staged_path=staged_resume_pdf_path):
            file_name = Path(staged_resume_pdf_path).name.strip()
            if file_name:
                return f"Confirmed staged file `{file_name}` is visible."
        if tool_name == "browser_file_upload":
            file_name = Path(staged_resume_pdf_path).name.strip()
            if file_name:
                return f"Submitted staged file `{file_name}` to the live page."
        if tool_name == "browser_click" and self._action_looks_like_close(arguments):
            return "Closed the currently visible dialog or overlay."
        page_title = MCPToolBridge.extract_page_title(signal_payload) if isinstance(signal_payload, dict) else ""
        if phase.slug == "session_preparation" and tool_name == "browser_click":
            element = ""
            if isinstance(arguments, dict):
                element = str(arguments.get("element") or "").strip()
            if element:
                return self._cap_text(f"Completed click on `{element}`.", max_chars=180)
        if page_title:
            return self._cap_text(f"Reached live page titled `{page_title}`.", max_chars=180)
        if current_url:
            return self._cap_text(f"Continued on `{current_url}`.", max_chars=180)
        return ""

    def _update_phase_memory(
        self,
        *,
        phase_memory: BrowserPhaseMemory | None,
        phase: PhasePrompt,
        tool_name: str,
        arguments: dict[str, Any] | None,
        error_text: str,
        current_url: str,
        payload: dict[str, Any] | None,
        latest_snapshot_payload: dict[str, Any] | None,
        staged_resume_pdf_path: str,
        include_page_state: bool = True,
    ) -> None:
        if phase_memory is None:
            return
        outcome = self._tool_outcome_summary(
            phase=phase,
            tool_name=tool_name,
            arguments=arguments,
            error_text=error_text,
            current_url=current_url,
            payload=payload,
            latest_snapshot_payload=latest_snapshot_payload,
            staged_resume_pdf_path=staged_resume_pdf_path,
        )
        phase_memory.record_action(
            tool=str(tool_name or "").strip(),
            action=self._summarize_arguments(arguments),
            status="error" if str(error_text or "").strip() else "ok",
            url=str(current_url or "").strip() if include_page_state else "",
            title=MCPToolBridge.extract_page_title(payload) if include_page_state and isinstance(payload, dict) else "",
            outcome=outcome,
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
        while (
            payload is not None
            and self._payload_requires_page_settle_retry(payload)
            and retries < self.PAGE_SETTLE_MAX_SNAPSHOT_RETRIES
        ):
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
            "site_job_id",
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

    @staticmethod
    def _is_objective_stale_context_error(error_text: str) -> bool:
        normalized = str(error_text or "").strip().lower()
        if not normalized:
            return False
        patterns = (
            r"\bref\s+\S+\s+not\s+found\s+in\s+the\s+current\s+page\s+snapshot\b",
            r"\b(?:stale|old)\s+ref\b",
            r"\bexecution context was destroyed\b",
            r"\bframe was detached\b",
            r"\belement is not attached to the dom\b",
        )
        return any(re.search(pattern, normalized) for pattern in patterns)

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
                    "posted_at",
                )
            )
            if has_other_fields:
                missing += 1
        return missing

    @staticmethod
    def _record_jobs_payload_count(arguments: dict[str, Any] | None) -> int:
        if not isinstance(arguments, dict):
            return 0
        jobs = arguments.get("jobs")
        return len(jobs) if isinstance(jobs, list) else 0

    @staticmethod
    def _is_apply_terminal_job_state(row: dict[str, Any] | None) -> bool:
        if not isinstance(row, dict):
            return False
        decision_status = str(row.get("decision_status") or "").strip().lower()
        application_status = str(row.get("application_status") or "").strip().lower()
        return decision_status in {"filtered_out", "already_applied"} or application_status in {
            "already_applied",
            "filtered_out",
            "submitted",
            "apply_failed",
            "blocked",
        }

    @staticmethod
    def _is_history_operation_success(row: dict[str, Any] | None) -> bool:
        if not isinstance(row, dict):
            return False
        decision_status = str(row.get("decision_status") or "").strip().lower()
        application_status = str(row.get("application_status") or "").strip().lower()
        application_review_status = str(row.get("application_review_status") or "").strip().lower()
        apply_state = str(row.get("apply_state") or "").strip().lower()
        successful_decisions = {"filtered_out", "skipped_as_not_fit", "not_fit", "already_applied", "submitted"}
        successful_application_statuses = {
            "filtered_out",
            "skipped_as_not_fit",
            "not_fit",
            "active",
            "in_process",
            "in_review",
            "resume_review",
            "assessment",
            "interview",
            "offer",
            "submitted",
            "already_applied",
            "application_received",
            "received",
            "rejected",
            "closed",
            "withdrawn",
        }
        successful_review_statuses = {
            "active",
            "in_process",
            "in_review",
            "resume_review",
            "assessment",
            "interview",
            "offer",
            "submitted",
            "application_received",
            "received",
            "rejected",
            "closed",
            "withdrawn",
        }
        successful_apply_states = {
            "filtered_out",
            "terminal_filtered_out",
            "terminal_submitted",
            "terminal_already_applied",
            "terminal_application_received",
        }
        return (
            decision_status in successful_decisions
            or application_status in successful_application_statuses
            or application_review_status in successful_review_statuses
            or apply_state in successful_apply_states
        )

    @classmethod
    def _terminal_job_ids_from_update_payload(cls, payload: dict[str, Any] | None) -> list[str]:
        if not isinstance(payload, dict):
            return []
        structured = payload.get("structuredContent")
        if not isinstance(structured, dict):
            return []
        terminal_ids = structured.get("terminal_job_ids")
        if not isinstance(terminal_ids, list):
            return []
        return [str(job_id).strip() for job_id in terminal_ids if str(job_id).strip()]

    @staticmethod
    def _normalize_file_upload_paths(arguments: dict[str, Any] | None) -> list[str]:
        if not isinstance(arguments, dict):
            return []
        raw_paths = arguments.get("paths")
        if isinstance(raw_paths, list):
            return [str(path).strip() for path in raw_paths if str(path).strip()]
        raw_path = str(arguments.get("path") or "").strip()
        return [raw_path] if raw_path else []

    @staticmethod
    def _canonicalize_local_path(path_text: str) -> str:
        raw = str(path_text or "").strip()
        if not raw:
            return ""
        try:
            return str(Path(raw).expanduser().resolve())
        except Exception:
            return raw

    @classmethod
    def _apply_upload_attempt_key(cls, *, current_url: str, upload_paths: list[str]) -> tuple[str, str] | tuple[()]:
        if not current_url or not upload_paths:
            return ()
        page_key = cls._canonicalize_runtime_url(current_url)
        if not page_key:
            return ()
        normalized_paths = sorted(
            cls._canonicalize_local_path(path)
            for path in upload_paths
            if str(path or "").strip()
        )
        normalized_paths = [path for path in normalized_paths if path]
        if not normalized_paths:
            return ()
        return (json.dumps(page_key, ensure_ascii=False, sort_keys=True), "|".join(normalized_paths))

    @classmethod
    def _payload_confirms_staged_file_upload(cls, payload: dict[str, Any] | None, *, staged_path: str) -> bool:
        if not isinstance(payload, dict) or not staged_path:
            return False
        file_name = Path(staged_path).name.strip().lower()
        if not file_name:
            return False
        text = "\n".join(
            part
            for part in (
                MCPToolBridge.live_page_text(payload),
                MCPToolBridge.summarize_tool_output(payload),
            )
            if part
        ).lower()
        if file_name not in text:
            return False
        return bool(re.search(r"\b(successfully uploaded|uploaded|attached|selected)\b", text))

    @classmethod
    def _matches_staged_upload_path(cls, candidate_path: str, staged_path: str) -> bool:
        left = cls._canonicalize_local_path(candidate_path)
        right = cls._canonicalize_local_path(staged_path)
        return bool(left and right and left == right)

    @staticmethod
    def _payload_upload_state_text(payload: dict[str, Any] | None) -> str:
        if not isinstance(payload, dict):
            return ""
        parts: list[str] = []
        for key in ("modalState", "page", "inlineSnapshot"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        live_page = MCPToolBridge.live_page_text(payload)
        if live_page:
            parts.append(live_page)
        summary = MCPToolBridge.summarize_tool_output(payload)
        if summary:
            parts.append(summary)
        merged = "\n".join(part for part in parts if part).strip()
        return merged

    @classmethod
    def _payload_file_chooser_count(cls, payload: dict[str, Any] | None) -> int:
        text = cls._payload_upload_state_text(payload)
        if not text:
            return 0
        explicit = len(re.findall(r"\[\s*file chooser\s*\]", text, flags=re.IGNORECASE))
        if explicit > 0:
            return explicit
        generic = len(re.findall(r"\bfile chooser\b", text, flags=re.IGNORECASE))
        return generic

    @classmethod
    def _payload_has_file_chooser(cls, payload: dict[str, Any] | None) -> bool:
        return cls._payload_file_chooser_count(payload) > 0

    @staticmethod
    def _payload_has_direct_file_upload_signal(payload: dict[str, Any] | None) -> bool:
        text = BrowserPhaseRuntime._payload_upload_state_text(payload)
        if not text:
            return False
        direct_patterns = (
            r"<input\b[^>\n]{0,240}\btype\s*=\s*[\"']?file[\"']?",
            r"\binput\b[^\n]{0,120}\btype\s*[:=]\s*[\"']?file[\"']?",
            r"\bdrag(?:\s+and\s+drop|\s*&\s*drop)\b[^\n]{0,120}\b(file|files|document|documents|resume|cv|attachment)\b",
            r"(^|\n)-\s*(?:button|link|input|label|generic)\b[^\n]{0,200}\b(upload|attach|browse|select)\b[^\n]{0,120}\b(file|files|document|documents|resume|cv|attachment)\b",
        )
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in direct_patterns):
            return True
        has_upload_entry = bool(
            re.search(
                r"(^|\n)-\s*(?:button|link|input|label|generic)\b[^\n]{0,200}\b(upload|attach|browse|select)\b",
                text,
                flags=re.IGNORECASE,
            )
        )
        has_file_target = bool(
            re.search(
                r"\b(file|files|document|documents|resume|cv|attachment)\b",
                text,
                flags=re.IGNORECASE,
            )
        )
        return has_upload_entry and has_file_target

    @classmethod
    def _payload_is_upload_ready(cls, payload: dict[str, Any] | None) -> bool:
        return cls._payload_has_file_chooser(payload) or cls._payload_has_direct_file_upload_signal(payload)

    @classmethod
    def _should_use_fresh_snapshot_primary_context(
        cls,
        *,
        tool_name: str,
        fresh_snapshot_captured: bool,
        error_text: str,
    ) -> bool:
        return bool(
            fresh_snapshot_captured
            and not str(error_text or "").strip()
            and tool_name != "browser_snapshot"
            and cls._is_page_settle_action(tool_name)
        )

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

    @classmethod
    def _job_retrieval_empty_evaluate_limit(cls, phase_memory: BrowserPhaseMemory | None) -> int:
        if isinstance(phase_memory, BrowserPhaseMemory) and phase_memory.get_text("retrieval_carry_forward").strip():
            return cls.JOB_RETRIEVAL_EMPTY_EVALUATE_MAX_SAME_PAGE_ATTEMPTS_WITH_CARRY_FORWARD
        return cls.JOB_RETRIEVAL_EMPTY_EVALUATE_MAX_SAME_PAGE_ATTEMPTS_WITHOUT_CARRY_FORWARD

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
    def _payload_has_empty_snapshot_signal(payload: dict[str, Any] | None) -> bool:
        if not isinstance(payload, dict) or bool(payload.get("isError")):
            return False
        content = payload.get("content")
        if not isinstance(content, list):
            return False
        text_blocks: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                text_blocks.append(text)
        if not text_blocks:
            return False
        merged = "\n".join(text_blocks)
        match = re.search(r"### Snapshot\b(.*?)(?=\n### [A-Za-z]|\Z)", merged, flags=re.S)
        if not match:
            return False
        snapshot_body = match.group(1)
        cleaned_lines: list[str] = []
        for raw_line in snapshot_body.splitlines():
            stripped = str(raw_line or "").strip()
            if not stripped or stripped.startswith("```"):
                continue
            if stripped.startswith("- [Screenshot") or stripped.startswith("- [Snapshot"):
                continue
            lowered = stripped.lower()
            if "mimetype" in lowered or '"data":' in lowered or "base64," in lowered:
                continue
            compact = stripped.replace(" ", "")
            if re.fullmatch(r"[A-Za-z0-9+/=]{1024,}", compact):
                continue
            cleaned_lines.append(stripped)
        return not cleaned_lines

    @classmethod
    def _payload_requires_page_settle_retry(cls, payload: dict[str, Any] | None) -> bool:
        return cls._payload_has_empty_snapshot_signal(payload)

    @classmethod
    def _is_page_settle_action(cls, name: str) -> bool:
        normalized = str(name or "").strip().lower()
        return normalized in cls.PAGE_SETTLE_ACTION_TOOLS

    @classmethod
    def _page_action_signature(cls, name: str, arguments: dict[str, Any] | None) -> str:
        if not cls._is_page_settle_action(name):
            return ""
        if not isinstance(arguments, dict):
            return str(name or "").strip().lower()
        try:
            normalized_arguments = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except Exception:
            normalized_arguments = str(arguments)
        return f"{str(name or '').strip().lower()}::{normalized_arguments}"

    @classmethod
    def _phase_memory_do_not_repeat_violation(
        cls,
        *,
        phase_memory: BrowserPhaseMemory,
        tool_name: str,
        arguments: dict[str, Any] | None,
        current_url: str,
    ) -> str:
        if not isinstance(phase_memory, BrowserPhaseMemory):
            return ""
        if not cls._is_page_settle_action(tool_name):
            return ""
        if not isinstance(arguments, dict):
            arguments = {}
        action_text = " ".join(
            str(arguments.get(key) or "")
            for key in ("element", "key", "keys", "text", "button", "url")
        ).strip().lower()
        if not action_text:
            return ""
        current_url_lower = str(current_url or "").strip().lower()
        for rule_text in phase_memory.do_not_repeat.values():
            rule = str(rule_text or "").strip()
            rule_lower = rule.lower()
            if not rule_lower:
                continue
            if "newest" in rule_lower and "newest" not in current_url_lower:
                continue
            if "relevance" in rule_lower and "relevance" not in current_url_lower:
                continue
            if "china" in rule_lower and "china" not in current_url_lower and "chnc" not in current_url_lower:
                continue
            if ("filter" in rule_lower and "filter" in action_text) or (
                "sort" in rule_lower and "sort" in action_text
            ) or ("location" in rule_lower and "location" in action_text):
                return rule
            rule_tokens = {
                token
                for token in re.findall(r"[a-z0-9]+", rule_lower)
                if len(token) >= 4 and token not in {"from", "this", "that", "with", "page", "button"}
            }
            action_tokens = set(re.findall(r"[a-z0-9]+", action_text))
            if rule_tokens.intersection(action_tokens):
                return rule
        return ""

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
            "Runtime note: the latest extraction returned zero jobs while the live page still shows retrieval result signals.\n"
            f"{url_line}"
            f"{page_line}"
            "Continue from the active skills, current phase memory, and current live page."
        )

    @staticmethod
    def _page_action_failure_recovery_message(*, current_url: str, tool_name: str) -> str:
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        tool_line = f"Failed page action: {tool_name}\n" if tool_name else ""
        return (
            "The previous page action failed.\n"
            f"{url_line}"
            f"{tool_line}"
            "A fresh live browser snapshot of the current page is already attached separately. "
            "Treat the failed action arguments, old ref, and pre-failure trajectory only as stale history. "
            "Re-read the current live page and resolve the next action from that fresh page state. "
            "Do not repeat the same failed page action or keep updating state from the stale pre-failure page."
        )

    @staticmethod
    def _job_retrieval_missing_urls_message(*, current_url: str, page_label: str, missing_count: int) -> str:
        page_line = f"Current page label: {page_label}\n" if page_label else ""
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        count_text = f"{max(1, int(missing_count or 0))} current-page job record(s)" if missing_count else "current-page job records"
        return (
            f"Runtime note: {count_text} are missing concrete per-job URLs or used the current results-page URL.\n"
            f"{url_line}"
            f"{page_line}"
            "Continue from the active skills, current phase memory, and current live page."
        )

    @staticmethod
    def _job_retrieval_serialization_error_message(*, current_url: str, page_label: str) -> str:
        page_line = f"Current page label: {page_label}\n" if page_label else ""
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        return (
            "The previous browser_evaluate call was rejected before page execution because the function was not serializable.\n"
            f"{url_line}"
            f"{page_line}"
            "Retry with a simpler serializable browser_evaluate function or choose another appropriate official browser tool from the current live page."
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
            "Runtime note: the current retrieval page fingerprint has already been recorded in this run.\n"
            f"{url_line}"
            f"{page_line}"
            "Treat this page as already persisted for the current run and continue from the active skills plus current live page."
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
        classify_history_matches = getattr(site_store, "classify_history_matches", None)
        list_jobs = getattr(site_store, "list_jobs", None)
        preview_new_flags = getattr(site_store, "preview_history_new_flags", None)
        before_rows = list_jobs(site_key) if callable(list_jobs) else []
        before_ids = {str(row.get("job_id") or "") for row in before_rows if isinstance(row, dict)}
        if callable(classify_history_matches):
            try:
                history_matches = list(classify_history_matches(site_key, jobs))
            except Exception:
                history_matches = []
        else:
            history_matches = []
        if not history_matches and callable(preview_new_flags):
            try:
                new_flags = list(preview_new_flags(site_key, jobs))
            except Exception:
                new_flags = []
        else:
            new_flags = [row.get("history_match_status") == "new" for row in history_matches]
        saved_rows = site_store.append_jobs(site_key, jobs, session_id or "", turn_id, batch_id)
        saved_ids: list[str] = []
        new_ids: list[str] = []
        history_match_results: list[dict[str, Any]] = []
        for idx, row in enumerate(saved_rows):
            if not isinstance(row, dict):
                continue
            record_id = str(row.get("observation_id") or row.get("job_id") or "").strip()
            if not record_id:
                continue
            run_job_id = str(row.get("job_id") or "").strip()
            saved_ids.append(record_id)
            is_new = idx < len(new_flags) and bool(new_flags[idx])
            if not new_flags and str(row.get("job_id") or "").strip() not in before_ids:
                is_new = True
            if is_new:
                new_ids.append(record_id)
            classification = history_matches[idx] if idx < len(history_matches) and isinstance(history_matches[idx], dict) else {}
            status = str(classification.get("history_match_status") or ("new" if is_new else "existing_complete"))
            reasons = classification.get("enrichment_reasons")
            if not isinstance(reasons, list):
                reasons = []
            history_match_results.append(
                {
                    "record_id": record_id,
                    "job_id": run_job_id,
                    "title": str(row.get("title") or ""),
                    "url": str(row.get("url") or ""),
                    "site_job_id": str(row.get("site_job_id") or ""),
                    "history_match_status": status,
                    "matched_job_id": str(classification.get("matched_job_id") or ""),
                    "decision_status": str(classification.get("decision_status") or ""),
                    "apply_state": str(classification.get("apply_state") or ""),
                    "application_status": str(classification.get("application_status") or ""),
                    "application_review_status": str(classification.get("application_review_status") or ""),
                    "application_review_status_raw": str(classification.get("application_review_status_raw") or ""),
                    "enrichment_reasons": [str(reason) for reason in reasons if str(reason).strip()],
                }
            )
            history_match_results[-1]["operation_success"] = self._is_history_operation_success(
                history_match_results[-1]
            )
        recorded_count = len(saved_ids)
        new_count = len(new_ids)
        existing_count = sum(
            1 for item in history_match_results if str(item.get("history_match_status") or "").startswith("existing_")
        )
        enrichment_needed = [
            item for item in history_match_results if item.get("history_match_status") == "existing_needs_enrichment"
        ]
        existing_complete_count = sum(
            1 for item in history_match_results if item.get("history_match_status") == "existing_complete"
        )
        enrichment_needed_count = len(enrichment_needed)
        operation_success_count = sum(1 for item in history_match_results if bool(item.get("operation_success")))
        operation_success_ratio = operation_success_count / recorded_count if recorded_count else 0.0
        stop_success_ratio_threshold = max(0.0, float(self.config.retrieval_history_stop_success_ratio or 0.0))
        stop_min_page_jobs = max(1, int(self.config.retrieval_history_stop_min_page_jobs or 1))
        stop_recommended = bool(
            recorded_count >= stop_min_page_jobs
            and operation_success_ratio >= stop_success_ratio_threshold
            and enrichment_needed_count == 0
        )
        stop_reason = (
            "current page reached the operation-success ratio threshold and has no enrichment targets"
            if stop_recommended
            else ""
        )
        summary = (
            f"Recorded {recorded_count} jobs from the current page "
            f"({new_count} new, {existing_count} existing, {enrichment_needed_count} need enrichment, "
            f"{operation_success_count} operation-success, success_ratio={operation_success_ratio:.2f})."
        )
        if stop_recommended:
            summary += (
                " Stop pagination is recommended by operation-success history policy "
                f"(threshold={stop_success_ratio_threshold:.2f}, min_page_jobs={stop_min_page_jobs})."
            )
        return {
            "isError": False,
            "current_url": current_url,
            "structuredContent": {
                "current_url": current_url,
                "recorded_count": recorded_count,
                "new_count": new_count,
                "existing_count": existing_count,
                "existing_complete_count": existing_complete_count,
                "enrichment_needed_count": enrichment_needed_count,
                "operation_success_count": operation_success_count,
                "operation_success_ratio": operation_success_ratio,
                "history_stop_success_ratio_threshold": stop_success_ratio_threshold,
                "history_stop_min_page_jobs": stop_min_page_jobs,
                "stop_recommended": stop_recommended,
                "stop_reason": stop_reason,
                "job_ids": saved_ids,
                "new_job_ids": new_ids,
                "history_matches": history_match_results,
                "enrichment_job_ids": [
                    str(item.get("job_id") or item.get("record_id") or "")
                    for item in enrichment_needed
                    if str(item.get("job_id") or item.get("record_id") or "")
                ],
            },
            "content": [{"type": "text", "text": summary}],
        }

    def _update_jobs_payload(
        self,
        *,
        site_store: Any,
        site_key: str,
        session_id: str,
        turn_id: str,
        batch_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        raw_jobs = arguments.get("jobs")
        jobs = [dict(job) for job in raw_jobs if isinstance(job, dict)] if isinstance(raw_jobs, list) else []
        saved_rows = site_store.update_run_jobs(site_key, jobs, session_id or "", turn_id, batch_id)
        updated_ids = [str(row.get("job_id") or "").strip() for row in saved_rows if str(row.get("job_id") or "").strip()]
        terminal_ids = [
            str(row.get("job_id") or "").strip()
            for row in saved_rows
            if self._is_apply_terminal_job_state(row) and str(row.get("job_id") or "").strip()
        ]
        summary = f"Updated {len(updated_ids)} jobs in the current batch run."
        return {
            "isError": False,
            "structuredContent": {
                "updated_count": len(updated_ids),
                "job_ids": updated_ids,
                "terminal_count": len(terminal_ids),
                "terminal_job_ids": terminal_ids,
            },
            "content": [{"type": "text", "text": summary}],
        }

    def _record_application_reviews_payload(
        self,
        *,
        site_store: Any,
        site_key: str,
        session_id: str,
        turn_id: str,
        batch_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        raw_reviews = arguments.get("reviews")
        reviews = [dict(row) for row in raw_reviews if isinstance(row, dict)] if isinstance(raw_reviews, list) else []
        append_reviews = getattr(site_store, "append_application_reviews", None)
        if not callable(append_reviews):
            raise RuntimeError("site store does not support application reviews")
        summary = append_reviews(site_key, reviews, session_id or "", turn_id, batch_id)
        recorded_count = int(summary.get("recorded_count") or 0) if isinstance(summary, dict) else 0
        matched_count = int(summary.get("matched_count") or 0) if isinstance(summary, dict) else 0
        unmatched_count = int(summary.get("unmatched_count") or 0) if isinstance(summary, dict) else 0
        created_history_count = int(summary.get("created_history_count") or 0) if isinstance(summary, dict) else 0
        matched_job_ids = summary.get("matched_job_ids") if isinstance(summary, dict) else []
        if not isinstance(matched_job_ids, list):
            matched_job_ids = []
        text = (
            f"Recorded {recorded_count} application reviews "
            f"({matched_count} matched history, {unmatched_count} unmatched)."
        )
        if created_history_count:
            text += f" Created {created_history_count} minimal history row(s)."
        return {
            "isError": False,
            "structuredContent": {
                "recorded_count": recorded_count,
                "matched_count": matched_count,
                "unmatched_count": unmatched_count,
                "created_history_count": created_history_count,
                "matched_job_ids": [str(job_id) for job_id in matched_job_ids if str(job_id).strip()],
            },
            "content": [{"type": "text", "text": text}],
        }

    @staticmethod
    def _request_context_payload(*, context_session: Any | None, arguments: dict[str, Any]) -> dict[str, Any]:
        if context_session is None:
            return {
                "isError": False,
                "structuredContent": {
                    "bundle": str(arguments.get("bundle") or ""),
                    "available": False,
                    "status": "context_session_unavailable",
                },
                "content": [{"type": "text", "text": "### Result\n- No browser context session is available for this phase."}],
            }
        request_bundle = getattr(context_session, "request_bundle", None)
        if not callable(request_bundle):
            return {
                "isError": False,
                "structuredContent": {
                    "bundle": str(arguments.get("bundle") or ""),
                    "available": False,
                    "status": "context_session_invalid",
                },
                "content": [{"type": "text", "text": "### Result\n- The current context session cannot serve bundles."}],
            }
        return dict(
            request_bundle(
                bundle=str(arguments.get("bundle") or ""),
                reason=str(arguments.get("reason") or ""),
            )
        )

    @staticmethod
    def _phase_memory_entries(raw_entries: Any) -> list[tuple[str, str]]:
        if not isinstance(raw_entries, list):
            return []
        entries: list[tuple[str, str]] = []
        for item in raw_entries:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            text = str(item.get("text") or "").strip()
            if key and text:
                entries.append((key, text))
        return entries

    @staticmethod
    def _positive_int(value: Any) -> int | None:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        if number <= 0:
            return None
        return number

    @classmethod
    def _update_phase_memory_payload(cls, *, phase_memory: BrowserPhaseMemory | None, arguments: dict[str, Any]) -> dict[str, Any]:
        if phase_memory is None:
            return {
                "isError": False,
                "structuredContent": {
                    "status": "phase_memory_unavailable",
                    "completed_count": 0,
                    "confirmed_count": 0,
                    "pending_count": 0,
                    "do_not_repeat_count": 0,
                    "metrics_count": 0,
                    "cleared_count": 0,
                },
                "content": [{"type": "text", "text": "### Result\n- No phase memory is available for this phase."}],
            }

        clear_keys = [
            str(item or "").strip()
            for item in (arguments.get("clear_keys") if isinstance(arguments.get("clear_keys"), list) else [])
            if str(item or "").strip()
        ]
        for key in clear_keys:
            phase_memory.drop(key)

        counts = {"completed": 0, "confirmed": 0, "pending": 0, "do_not_repeat": 0}
        for key, text in cls._phase_memory_entries(arguments.get("completed")):
            phase_memory.set_completed(key=key, text=text)
            counts["completed"] += 1
        for key, text in cls._phase_memory_entries(arguments.get("confirmed")):
            phase_memory.set_confirmed(key=key, text=text)
            counts["confirmed"] += 1
        for key, text in cls._phase_memory_entries(arguments.get("pending")):
            phase_memory.set_pending(key=key, text=text)
            counts["pending"] += 1
        for key, text in cls._phase_memory_entries(arguments.get("do_not_repeat")):
            phase_memory.set_do_not_repeat(key=key, text=text)
            counts["do_not_repeat"] += 1

        metrics_count = 0
        raw_metrics = arguments.get("metrics")
        if isinstance(raw_metrics, dict):
            for key in ("results_count", "total_pages", "page_size"):
                value = cls._positive_int(raw_metrics.get(key))
                if value is None:
                    continue
                phase_memory.set_metric(key=key, value=value)
                metrics_count += 1

        total_changes = sum(counts.values()) + metrics_count + len(clear_keys)
        if total_changes <= 0:
            return {
                "isError": False,
                "structuredContent": {
                    "status": "no_change",
                    "completed_count": 0,
                    "confirmed_count": 0,
                    "pending_count": 0,
                    "do_not_repeat_count": 0,
                    "metrics_count": 0,
                    "cleared_count": 0,
                    "clear_keys": [],
                },
                "content": [{"type": "text", "text": "### Result\n- Phase memory unchanged."}],
            }

        lines = ["### Result", "- Updated current phase memory."]
        if clear_keys:
            lines.append(f"- Cleared keys: {', '.join(clear_keys)}")
        if counts["completed"] > 0:
            lines.append(f"- Added completed items: {counts['completed']}")
        if counts["confirmed"] > 0:
            lines.append(f"- Added confirmed items: {counts['confirmed']}")
        if counts["pending"] > 0:
            lines.append(f"- Added pending items: {counts['pending']}")
        if counts["do_not_repeat"] > 0:
            lines.append(f"- Added do-not-repeat items: {counts['do_not_repeat']}")
        if metrics_count > 0:
            lines.append(f"- Added metrics: {metrics_count}")
        return {
            "isError": False,
            "structuredContent": {
                "status": "updated",
                "completed_count": counts["completed"],
                "confirmed_count": counts["confirmed"],
                "pending_count": counts["pending"],
                "do_not_repeat_count": counts["do_not_repeat"],
                "metrics_count": metrics_count,
                "cleared_count": len(clear_keys),
                "clear_keys": clear_keys,
            },
            "content": [{"type": "text", "text": "\n".join(lines)}],
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
        extra_context_items: list[dict[str, Any]] | None = None,
        context_session: Any | None = None,
        apply_staged_resume_pdf_path: str = "",
    ) -> BrowserPhaseResult:
        history_items: list[dict[str, Any]] = []
        tools = list(response_tools) + [self.phase_result_tool()]
        trace_ref = ""
        step_count = 0
        effective_phase_timeout = int(phase_timeout_seconds or self.config.phase_timeout_seconds or 180)
        effective_max_phase_steps = int(max_phase_steps or self.config.max_phase_steps or 24)
        ensure_timeout = getattr(self.responses, "ensure_timeout", None)
        if callable(ensure_timeout):
            ensure_timeout(max(effective_phase_timeout, int(self.config.step_timeout_seconds or 30)) + 30.0)
        deadline = time.monotonic() + max(5.0, float(effective_phase_timeout))
        current_url = str(entry_url or "")
        observation_streak = 0
        last_observation_url = ""
        same_page_evaluate_streak = 0
        last_evaluate_url = ""
        no_progress_internal_streak = 0
        last_no_progress_internal_url = ""
        recorded_job_ids: set[str] = set()
        new_job_ids: set[str] = set()
        recorded_page_fingerprints: set[str] = set()
        empty_extraction_page_key = ""
        empty_extraction_count = 0
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
        phase_memory = getattr(context_session, "phase_memory", None) if context_session is not None else None
        if not isinstance(phase_memory, BrowserPhaseMemory):
            phase_memory = BrowserPhaseMemory(recent_action_limit=self.RECENT_TRAJECTORY_LIMIT)
        staged_resume_pdf_path = str(apply_staged_resume_pdf_path or "").strip()
        apply_upload_requires_observation = False
        apply_upload_modal_unresolved = False
        apply_completed_upload_keys: set[tuple[str, str]] = set()
        apply_auth_recovery_page_key = ""
        failed_page_action_signature = ""
        failed_page_action_name = ""
        response_turn_timeout_count = 0
        max_response_turn_timeouts = max(1, int(self.config.recovery_max_attempts or 0))
        last_record_jobs_policy: dict[str, Any] = {}
        retrieval_policy_pagination_violation_count = 0
        same_url_no_progress_key = ""
        same_url_no_progress_tool_calls = 0
        same_url_no_progress_tokens = 0
        do_not_repeat_violation_count = 0

        def track_same_url_no_progress(
            *,
            made_progress: bool,
            tool_name: str,
            pre_progress_key: str,
            post_progress_key: str,
            response_tokens: int,
            output_text: str,
        ) -> BrowserPhaseResult | None:
            nonlocal same_url_no_progress_key
            nonlocal same_url_no_progress_tool_calls
            nonlocal same_url_no_progress_tokens
            if made_progress:
                same_url_no_progress_key = ""
                same_url_no_progress_tool_calls = 0
                same_url_no_progress_tokens = 0
                return None
            progress_key = post_progress_key or pre_progress_key or str(current_url or "").strip()
            if not progress_key:
                return None
            if progress_key == same_url_no_progress_key:
                same_url_no_progress_tool_calls += 1
            else:
                same_url_no_progress_key = progress_key
                same_url_no_progress_tool_calls = 1
                same_url_no_progress_tokens = 0
            same_url_no_progress_tokens += max(0, int(response_tokens or 0))
            trigger_values = {
                "tool_name": str(tool_name or ""),
                "progress_key": progress_key,
                "same_url_no_progress_tool_calls": same_url_no_progress_tool_calls,
                "same_url_no_progress_tokens": same_url_no_progress_tokens,
            }
            token_limit = self._same_url_no_progress_token_limit(phase)
            tool_call_limit = self._same_url_no_progress_tool_call_limit(phase)
            trigger_values["same_url_no_progress_token_limit"] = token_limit
            trigger_values["same_url_no_progress_tool_call_limit"] = tool_call_limit
            if token_limit > 0 and same_url_no_progress_tokens >= token_limit:
                summary = self._same_url_no_progress_message(
                    phase=phase,
                    current_url=current_url,
                    tool_calls=same_url_no_progress_tool_calls,
                    tokens=same_url_no_progress_tokens,
                )
                self._record_browser_control_event(
                    site_store=site_store,
                    event_type="same_url_no_progress_tokens",
                    batch_id=batch_id,
                    site_key=site_key,
                    phase=phase,
                    turn_id=turn_id,
                    current_url=current_url,
                    guard_name="same_url_no_progress_tokens",
                    trigger_values=trigger_values,
                    last_record_jobs_policy=last_record_jobs_policy,
                    trace_ref=trace_ref,
                    summary=summary,
                )
                return BrowserPhaseResult(
                    status="failed",
                    reason_tag="same_url_no_progress_tokens",
                    summary=summary,
                    current_url=current_url,
                    step_count=step_count,
                    trace_ref=trace_ref,
                    raw_text=output_text,
                    recorded_count=len(recorded_job_ids),
                    new_count=len(new_job_ids),
                )
            if tool_call_limit > 0 and same_url_no_progress_tool_calls >= tool_call_limit:
                summary = self._same_url_no_progress_message(
                    phase=phase,
                    current_url=current_url,
                    tool_calls=same_url_no_progress_tool_calls,
                    tokens=same_url_no_progress_tokens,
                )
                self._record_browser_control_event(
                    site_store=site_store,
                    event_type="same_url_no_progress",
                    batch_id=batch_id,
                    site_key=site_key,
                    phase=phase,
                    turn_id=turn_id,
                    current_url=current_url,
                    guard_name="same_url_no_progress",
                    trigger_values=trigger_values,
                    last_record_jobs_policy=last_record_jobs_policy,
                    trace_ref=trace_ref,
                    summary=summary,
                )
                return BrowserPhaseResult(
                    status="failed",
                    reason_tag="same_url_no_progress",
                    summary=summary,
                    current_url=current_url,
                    step_count=step_count,
                    trace_ref=trace_ref,
                    raw_text=output_text,
                    recorded_count=len(recorded_job_ids),
                    new_count=len(new_job_ids),
                )
            return None

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
            active_tools = list(tools)
            active_tool_names = set(tool_names)
            observation_guard_active = self._observation_guard_applies(
                phase=phase,
                observation_streak=observation_streak,
                current_url=current_url,
                last_observation_url=last_observation_url,
                apply_upload_requires_observation=apply_upload_requires_observation,
                apply_upload_modal_unresolved=apply_upload_modal_unresolved,
            )
            if observation_guard_active:
                active_tools, active_tool_names = self._filter_tools_for_observation_guard(active_tools, active_tool_names)
                loop_context_items.append(self._context_item(self._observation_guard_message(phase=phase, current_url=current_url)))
            same_page_evaluate_guard_active = self._same_page_evaluate_guard_applies(
                phase=phase,
                same_page_evaluate_streak=same_page_evaluate_streak,
                current_url=current_url,
                last_evaluate_url=last_evaluate_url,
                apply_upload_requires_observation=apply_upload_requires_observation,
                apply_upload_modal_unresolved=apply_upload_modal_unresolved,
            )
            if same_page_evaluate_guard_active:
                active_tools, active_tool_names = self._filter_tool_by_name(
                    active_tools,
                    active_tool_names,
                    "browser_evaluate",
                )
                loop_context_items.append(
                    self._context_item(self._same_page_evaluate_guard_message(phase=phase, current_url=current_url))
                )
            no_progress_internal_guard_active = self._no_progress_internal_guard_applies(
                phase=phase,
                no_progress_internal_streak=no_progress_internal_streak,
                current_url=current_url,
                last_no_progress_internal_url=last_no_progress_internal_url,
                apply_upload_requires_observation=apply_upload_requires_observation,
                apply_upload_modal_unresolved=apply_upload_modal_unresolved,
            )
            if no_progress_internal_guard_active:
                active_tools, active_tool_names = self._filter_tools_by_names(
                    active_tools,
                    active_tool_names,
                    set(self.NO_PROGRESS_INTERNAL_TOOLS),
                )
                loop_context_items.append(
                    self._context_item(self._no_progress_internal_guard_message(phase=phase, current_url=current_url))
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
                        recent_trajectory=phase_memory.recent_actions_text(),
                        phase_memory_summary=phase_memory.phase_memory_text(),
                    ),
                },
            ]
            if phase.slug == "session_preparation":
                base_items.extend(self._staged_resume_context_items(staged_resume_pdf_path))
            context_items = getattr(context_session, "items", None) if context_session is not None else None
            static_context_items = list(extra_context_items or [])
            if callable(context_items):
                static_context_items.extend(context_items())
            turn_timeout_seconds = self._response_turn_timeout_seconds(deadline=deadline)
            try:
                response_payload = self._payload(
                    input_items=base_items + static_context_items + loop_context_items,
                    tools=active_tools,
                    request_timeout_seconds=turn_timeout_seconds,
                )
                response_payload["_metrics_context"] = {
                    "site_id": site_key,
                    "site_key": site_key,
                    "batch_id": batch_id,
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "phase": phase.slug,
                    "current_url": current_url,
                }
                with anyio.fail_after(turn_timeout_seconds):
                    response = await self._create_response_with_retry(response_payload)
            except TimeoutError:
                response_turn_timeout_count += 1
                previous_url = current_url
                snapshot_payload = await self._capture_snapshot_payload(
                    bridge=bridge,
                    session=session,
                    tool_names=tool_names,
                )
                if (
                    isinstance(snapshot_payload, dict)
                    and snapshot_payload
                    and not bool(snapshot_payload.get("isError"))
                ):
                    latest_snapshot_payload = snapshot_payload
                    last_payload = snapshot_payload
                    snapshot_url = MCPToolBridge.extract_current_url(snapshot_payload)
                    if snapshot_url:
                        current_url = snapshot_url
                history_items = []
                if (
                    isinstance(latest_snapshot_payload, dict)
                    and latest_snapshot_payload
                    and not bool(latest_snapshot_payload.get("isError"))
                ):
                    history_items.append(self._context_item(self._live_snapshot_primary_message()))
                history_items.append(
                    self._context_item(
                        self._browser_recovery_message(
                            phase=phase,
                            reason="response_turn_timeout",
                            current_url=current_url,
                            previous_url=previous_url,
                            attempt=response_turn_timeout_count,
                            max_attempts=max_response_turn_timeouts,
                            timeout_seconds=turn_timeout_seconds,
                            detail="The previous model response turn timed out before producing any tool call.",
                        )
                    )
                )
                if response_turn_timeout_count >= max_response_turn_timeouts:
                    return BrowserPhaseResult(
                        status="failed",
                        reason_tag="recovery_exhausted",
                        summary=(
                            f"browser recovery exhausted after {response_turn_timeout_count} response timeout attempt(s) "
                            "without model tool progress"
                        ),
                        current_url=current_url,
                        step_count=step_count,
                        trace_ref=trace_ref,
                        recorded_count=len(recorded_job_ids),
                        new_count=len(new_job_ids),
                    )
                await self._sleep(min(1.0, max(0.1, turn_timeout_seconds / 2.0)))
                continue
            response_turn_timeout_count = 0
            output_items = self._extract_output_items(response)
            output_text = self._extract_output_text(response)
            response_total_tokens = self._response_total_tokens(response)
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
                current_signal_payload = (
                    latest_snapshot_payload
                    if isinstance(latest_snapshot_payload, dict)
                    else last_payload
                    if isinstance(last_payload, dict)
                    else None
                )
                if phase.slug == "job_retrieval":
                    current_page_key = self._job_retrieval_page_fingerprint(
                        current_url=current_url,
                        latest_snapshot_payload=latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else None,
                        last_payload=last_payload if isinstance(last_payload, dict) else None,
                    )
                    current_page_label = self._extract_page_label(latest_snapshot_payload) or self._extract_page_label(last_payload)
                pre_tool_progress_key = self._phase_progress_key(
                    phase=phase,
                    current_url=current_url,
                    latest_snapshot_payload=latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else None,
                    last_payload=last_payload if isinstance(last_payload, dict) else None,
                )

                if phase.slug == "apply" and apply_upload_requires_observation and name == "phase_result":
                    history_items = [
                        self._context_item(
                            self._apply_file_upload_observe_message(
                                current_url=current_url,
                                staged_path=staged_resume_pdf_path,
                            )
                        )
                    ]
                    retry_requested = True
                    break

                if phase.slug == "apply" and apply_upload_modal_unresolved and name == "phase_result":
                    if str(arguments.get("status") or "").strip().lower() != "blocked":
                        history_items = [
                            self._context_item(
                                self._apply_file_upload_modal_unresolved_message(
                                    current_url=current_url,
                                    staged_path=staged_resume_pdf_path,
                                    chooser_count=self._payload_file_chooser_count(current_signal_payload),
                                )
                            )
                        ]
                        retry_requested = True
                        break

                if name == "phase_result":
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

                if observation_guard_active and is_observation_tool and name not in active_tool_names:
                    return BrowserPhaseResult(
                        status="failed",
                        reason_tag="observation_limit",
                        summary="repeated observation-only tool calls on the same page exceeded limits",
                        current_url=current_url,
                        step_count=step_count,
                        trace_ref=trace_ref,
                        raw_text=output_text,
                        recorded_count=len(recorded_job_ids),
                        new_count=len(new_job_ids),
                    )

                if same_page_evaluate_guard_active and name == "browser_evaluate" and name not in active_tool_names:
                    return BrowserPhaseResult(
                        status="failed",
                        reason_tag="same_page_evaluate_limit",
                        summary="repeated browser_evaluate calls on the same page exceeded limits without progress",
                        current_url=current_url,
                        step_count=step_count,
                        trace_ref=trace_ref,
                        raw_text=output_text,
                        recorded_count=len(recorded_job_ids),
                        new_count=len(new_job_ids),
                    )

                if no_progress_internal_guard_active and self._is_no_progress_internal_tool(name) and name not in active_tool_names:
                    return BrowserPhaseResult(
                        status="failed",
                        reason_tag="no_progress_internal_limit",
                        summary="repeated non-browser context or memory tool calls exceeded limits without page action or terminal state update",
                        current_url=current_url,
                        step_count=step_count,
                        trace_ref=trace_ref,
                        raw_text=output_text,
                        recorded_count=len(recorded_job_ids),
                        new_count=len(new_job_ids),
                    )

                if name not in active_tool_names:
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

                if failed_page_action_signature:
                    current_action_signature = self._page_action_signature(
                        name,
                        arguments if isinstance(arguments, dict) else None,
                    )
                    if current_action_signature and current_action_signature == failed_page_action_signature:
                        history_items = [
                            self._context_item(
                                self._page_action_failure_recovery_message(
                                    current_url=current_url,
                                    tool_name=failed_page_action_name or name,
                                )
                            )
                        ]
                        retry_requested = True
                        break
                    if phase.slug == "apply" and name == "update_jobs":
                        history_items = [
                            self._context_item(
                                self._page_action_failure_recovery_message(
                                    current_url=current_url,
                                    tool_name=failed_page_action_name,
                                )
                            )
                        ]
                        retry_requested = True
                        break

                if phase.slug == "apply" and apply_upload_modal_unresolved:
                    history_items = [
                        self._context_item(
                            self._apply_file_upload_modal_unresolved_message(
                                current_url=current_url,
                                staged_path=staged_resume_pdf_path,
                                chooser_count=self._payload_file_chooser_count(current_signal_payload),
                            )
                        )
                    ]
                    retry_requested = True
                    break

                if phase.slug == "apply" and apply_auth_recovery_page_key and name == "update_jobs":
                    current_apply_recovery_page_key = self._apply_recovery_page_key(
                        current_url=current_url,
                        payload=latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else last_payload,
                    )
                    if not current_apply_recovery_page_key or current_apply_recovery_page_key == apply_auth_recovery_page_key:
                        history_items = [
                            self._context_item(self._live_snapshot_primary_message()),
                            self._context_item(
                                self._apply_auth_recovery_message(
                                    current_url=current_url,
                                )
                            ),
                        ]
                        retry_requested = True
                        break

                if phase.slug == "apply" and apply_upload_requires_observation and not is_observation_tool:
                    history_items = [
                        self._context_item(
                            self._apply_file_upload_observe_message(
                                current_url=current_url,
                                staged_path=staged_resume_pdf_path,
                            )
                        )
                    ]
                    retry_requested = True
                    break

                if phase.slug == "apply" and name == "browser_file_upload":
                    upload_paths = self._normalize_file_upload_paths(arguments if isinstance(arguments, dict) else None)
                    if not upload_paths:
                        history_items = [
                            self._context_item(
                                self._apply_file_upload_empty_paths_message(
                                    current_url=current_url,
                                    staged_path=staged_resume_pdf_path,
                                )
                            )
                        ]
                        retry_requested = True
                        break
                    if staged_resume_pdf_path and any(
                        not self._matches_staged_upload_path(path, staged_resume_pdf_path) for path in upload_paths
                    ):
                        history_items = [
                            self._context_item(
                                self._apply_file_upload_use_staged_path_message(
                                    current_url=current_url,
                                    staged_path=staged_resume_pdf_path,
                                    attempted_paths=upload_paths,
                                )
                            )
                        ]
                        retry_requested = True
                        break
                    upload_attempt_key = self._apply_upload_attempt_key(
                        current_url=current_url,
                        upload_paths=upload_paths,
                    )
                    if upload_attempt_key and upload_attempt_key in apply_completed_upload_keys:
                        history_items = [
                            self._context_item(
                                self._apply_file_upload_repeat_message(
                                    current_url=current_url,
                                    staged_path=staged_resume_pdf_path or upload_paths[0],
                                )
                            )
                        ]
                        retry_requested = True
                        break
                    if not self._payload_is_upload_ready(current_signal_payload):
                        history_items = [
                            self._context_item(
                                self._apply_file_upload_requires_ready_message(
                                    current_url=current_url,
                                    staged_path=staged_resume_pdf_path or upload_paths[0],
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
                    and (is_observation_tool or name in {"browser_evaluate", "record_jobs"})
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
                        history_items = [
                            self._context_item(
                                self._job_retrieval_missing_urls_message(
                                    current_url=current_url,
                                    page_label=page_label,
                                    missing_count=missing_url_count,
                                )
                            )
                        ]
                        retry_requested = True
                        break
                if (
                    phase.slug == "job_retrieval"
                    and self._looks_like_pagination_action(name, arguments if isinstance(arguments, dict) else None)
                    and self._record_jobs_policy_applies(
                        last_record_jobs_policy,
                        current_url=current_url,
                        current_page_key=current_page_key,
                    )
                ):
                    if bool(last_record_jobs_policy.get("stop_recommended")):
                        summary = (
                            "Job retrieval history policy recommended stopping, but pagination is allowed to continue; "
                            "history ratio is advisory and not a hard stop."
                        )
                        self._record_browser_control_event(
                            site_store=site_store,
                            event_type="advisory_stop_recommended",
                            batch_id=batch_id,
                            site_key=site_key,
                            phase=phase,
                            turn_id=turn_id,
                            current_url=current_url,
                            guard_name="retrieval_stop_recommended",
                            trigger_values={
                                "attempted_tool": name,
                                "attempted_arguments": arguments,
                                "stop_recommended": True,
                            },
                            last_record_jobs_policy=last_record_jobs_policy,
                            trace_ref=trace_ref,
                            summary=summary,
                        )
                        history_items.append(
                            self._context_item(
                                "Runtime note: history ratio is advisory only. Do not use it as a hard pagination stop. "
                                "Continue retrieval unless the page reaches a strong stop condition such as date window, "
                                "no next page, no more results, or a site-specific stop rule."
                            )
                        )
                    enrichment_needed_count = int(last_record_jobs_policy.get("enrichment_needed_count") or 0)
                    if enrichment_needed_count > 0:
                        retrieval_policy_pagination_violation_count += 1
                        enrichment_job_ids = [
                            str(job_id).strip()
                            for job_id in last_record_jobs_policy.get("enrichment_job_ids") or []
                            if str(job_id).strip()
                        ]
                        summary = "Job retrieval blocked pagination because current-page history matches still need enrichment."
                        self._record_browser_control_event(
                            site_store=site_store,
                            event_type="ignored_enrichment_required",
                            batch_id=batch_id,
                            site_key=site_key,
                            phase=phase,
                            turn_id=turn_id,
                            current_url=current_url,
                            guard_name="retrieval_enrichment_required",
                            trigger_values={
                                "attempted_tool": name,
                                "attempted_arguments": arguments,
                                "enrichment_needed_count": enrichment_needed_count,
                                "violation_count": retrieval_policy_pagination_violation_count,
                            },
                            last_record_jobs_policy=last_record_jobs_policy,
                            trace_ref=trace_ref,
                            summary=summary,
                        )
                        if retrieval_policy_pagination_violation_count >= self.RETRIEVAL_POLICY_PAGINATION_VIOLATION_LIMIT:
                            return BrowserPhaseResult(
                                status="failed",
                                reason_tag="retrieval_enrichment_required",
                                summary=summary,
                                current_url=current_url,
                                step_count=step_count,
                                trace_ref=trace_ref,
                                raw_text=output_text,
                                recorded_count=len(recorded_job_ids),
                                new_count=len(new_job_ids),
                            )
                        history_items = [
                            self._context_item(
                                self._job_retrieval_enrichment_required_message(
                                    current_url=current_url,
                                    enrichment_needed_count=enrichment_needed_count,
                                    enrichment_job_ids=enrichment_job_ids,
                                )
                            )
                        ]
                        retry_requested = True
                        break
                do_not_repeat_reason = self._phase_memory_do_not_repeat_violation(
                    phase_memory=phase_memory,
                    tool_name=name,
                    arguments=arguments if isinstance(arguments, dict) else None,
                    current_url=current_url,
                )
                if do_not_repeat_reason:
                    do_not_repeat_violation_count += 1
                    summary = "Runtime blocked a repeated action from phase memory do-not-repeat guidance."
                    self._record_browser_control_event(
                        site_store=site_store,
                        event_type="do_not_repeat_violation",
                        batch_id=batch_id,
                        site_key=site_key,
                        phase=phase,
                        turn_id=turn_id,
                        current_url=current_url,
                        guard_name="do_not_repeat_violation",
                        trigger_values={
                            "attempted_tool": name,
                            "attempted_arguments": arguments,
                            "violation_count": do_not_repeat_violation_count,
                            "rule": do_not_repeat_reason,
                        },
                        last_record_jobs_policy=last_record_jobs_policy,
                        trace_ref=trace_ref,
                        summary=summary,
                    )
                    if do_not_repeat_violation_count >= 2:
                        return BrowserPhaseResult(
                            status="failed",
                            reason_tag="do_not_repeat_violation",
                            summary=summary,
                            current_url=current_url,
                            step_count=step_count,
                            trace_ref=trace_ref,
                            raw_text=output_text,
                            recorded_count=len(recorded_job_ids),
                            new_count=len(new_job_ids),
                        )
                    history_items = [
                        self._context_item(
                            f"{summary}\nRule: {do_not_repeat_reason}\nChoose a different action or finish with phase_result."
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
                        elif name == "update_jobs":
                            payload = self._update_jobs_payload(
                                site_store=site_store,
                                site_key=site_key,
                                session_id=session_id,
                                turn_id=turn_id,
                                batch_id=batch_id,
                                arguments=arguments if isinstance(arguments, dict) else {},
                            )
                        elif name == "record_application_reviews":
                            payload = self._record_application_reviews_payload(
                                site_store=site_store,
                                site_key=site_key,
                                session_id=session_id,
                                turn_id=turn_id,
                                batch_id=batch_id,
                                arguments=arguments if isinstance(arguments, dict) else {},
                            )
                        elif name == "request_context":
                            payload = self._request_context_payload(
                                context_session=context_session,
                                arguments=arguments if isinstance(arguments, dict) else {},
                            )
                        elif name == "update_phase_memory":
                            payload = self._update_phase_memory_payload(
                                phase_memory=phase_memory,
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
                        last_record_jobs_policy = self._record_jobs_policy_from_structured(
                            structured,
                            current_url=current_url,
                            page_key=recorded_page_key,
                        )
                        retrieval_policy_pagination_violation_count = 0
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
                    terminal_job_ids: list[str] = []
                    if phase.slug == "apply" and name == "update_jobs" and not error_text:
                        terminal_job_ids = self._terminal_job_ids_from_update_payload(payload)
                        if terminal_job_ids:
                            terminal_text = ", ".join(terminal_job_ids[:3])
                            if len(terminal_job_ids) > 3:
                                terminal_text = f"{terminal_text}, ..."
                            return BrowserPhaseResult(
                                status="done",
                                reason_tag="apply_terminal_update",
                                summary=f"Current apply target reached terminal state via update_jobs: {terminal_text}",
                                current_url=current_url,
                                step_count=step_count,
                                trace_ref=trace_ref,
                                raw_text=output_text,
                                recorded_count=len(recorded_job_ids),
                                new_count=len(new_job_ids),
                            )
                    tool_feedback = self._context_item(
                        MCPToolBridge.build_tool_feedback(
                            name,
                            payload,
                            ignore_phrases=phase.ignore_phrases,
                        )
                    )
                    if name in {"request_context", "update_phase_memory"} and not error_text:
                        self._update_phase_memory(
                            phase_memory=phase_memory,
                            phase=phase,
                            tool_name=name,
                            arguments=arguments,
                            error_text=error_text,
                            current_url=current_url,
                            payload=payload,
                            latest_snapshot_payload=latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else None,
                            staged_resume_pdf_path=staged_resume_pdf_path,
                        )
                        if no_progress_internal_streak > 0 and (
                            not last_no_progress_internal_url or current_url == last_no_progress_internal_url
                        ):
                            no_progress_internal_streak += 1
                        else:
                            no_progress_internal_streak = 1
                        last_no_progress_internal_url = current_url
                        post_tool_progress_key = self._phase_progress_key(
                            phase=phase,
                            current_url=current_url,
                            latest_snapshot_payload=latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else None,
                            last_payload=last_payload if isinstance(last_payload, dict) else None,
                        )
                        no_progress_result = track_same_url_no_progress(
                            made_progress=self._tool_call_made_runtime_progress(
                                phase=phase,
                                tool_name=name,
                                error_text=error_text,
                                pre_progress_key=pre_tool_progress_key,
                                post_progress_key=post_tool_progress_key,
                                payload=payload,
                            ),
                            tool_name=name,
                            pre_progress_key=pre_tool_progress_key,
                            post_progress_key=post_tool_progress_key,
                            response_tokens=response_total_tokens,
                            output_text=output_text,
                        )
                        if no_progress_result is not None:
                            return no_progress_result
                        history_items = [tool_feedback]
                        retry_requested = True
                        break
                    if phase.slug == "apply" and name == "update_jobs" and not error_text and not terminal_job_ids:
                        auth_recovery_payload = (
                            latest_snapshot_payload
                            if isinstance(latest_snapshot_payload, dict)
                            else current_signal_payload
                            if isinstance(current_signal_payload, dict)
                            else payload
                            if isinstance(payload, dict)
                            else None
                        )
                        if isinstance(auth_recovery_payload, dict) and self._payload_has_visible_auth_action(
                            auth_recovery_payload,
                            phase=phase,
                        ):
                            apply_auth_recovery_page_key = self._apply_recovery_page_key(
                                current_url=current_url,
                                payload=auth_recovery_payload,
                            )
                            history_items = [tool_feedback]
                            history_items.append(self._context_item(self._live_snapshot_primary_message()))
                            history_items.append(
                                self._context_item(
                                    self._apply_auth_recovery_message(
                                        current_url=current_url,
                                    )
                                )
                            )
                            last_payload = latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else payload
                            retry_requested = True
                            break
                    post_tool_context_items: list[dict[str, Any]] = []
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
                    if fresh_snapshot_captured and not error_text:
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
                    if phase.slug == "apply" and not error_text:
                        auth_signal_payload = (
                            latest_snapshot_payload
                            if isinstance(latest_snapshot_payload, dict)
                            else payload
                            if isinstance(payload, dict)
                            else None
                        )
                        latest_apply_recovery_page_key = self._apply_recovery_page_key(
                            current_url=current_url,
                            payload=auth_signal_payload,
                        )
                        if (
                            apply_auth_recovery_page_key
                            and latest_apply_recovery_page_key
                            and latest_apply_recovery_page_key != apply_auth_recovery_page_key
                        ):
                            apply_auth_recovery_page_key = ""
                        elif apply_auth_recovery_page_key and (
                            not isinstance(auth_signal_payload, dict)
                            or not self._payload_has_visible_auth_action(auth_signal_payload, phase=phase)
                        ):
                            apply_auth_recovery_page_key = ""
                    if phase.slug == "apply":
                        if name == "browser_file_upload" and not error_text:
                            upload_paths = self._normalize_file_upload_paths(arguments if isinstance(arguments, dict) else None)
                            upload_attempt_key = self._apply_upload_attempt_key(
                                current_url=current_url,
                                upload_paths=upload_paths,
                            )
                            if upload_attempt_key:
                                apply_completed_upload_keys.add(upload_attempt_key)
                            upload_signal_payload = (
                                latest_snapshot_payload
                                if isinstance(latest_snapshot_payload, dict)
                                else payload
                            )
                            upload_chooser_count = self._payload_file_chooser_count(upload_signal_payload)
                            if self._payload_confirms_staged_file_upload(
                                upload_signal_payload,
                                staged_path=staged_resume_pdf_path,
                            ):
                                apply_upload_requires_observation = False
                                apply_upload_modal_unresolved = False
                                history_items = [
                                    tool_feedback,
                                    self._context_item(
                                        self._apply_file_upload_confirmed_message(
                                            current_url=current_url,
                                            staged_path=staged_resume_pdf_path,
                                        )
                                    ),
                                ]
                            elif upload_chooser_count > 0:
                                apply_upload_requires_observation = False
                                apply_upload_modal_unresolved = True
                                history_items = [
                                    tool_feedback,
                                    self._context_item(
                                        self._apply_file_upload_modal_unresolved_message(
                                            current_url=current_url,
                                            staged_path=staged_resume_pdf_path or (upload_paths[0] if upload_paths else ""),
                                            chooser_count=upload_chooser_count,
                                        )
                                    ),
                                ]
                            else:
                                apply_upload_requires_observation = True
                                apply_upload_modal_unresolved = False
                                history_items = [
                                    tool_feedback,
                                    self._context_item(
                                        self._apply_file_upload_observe_message(
                                            current_url=current_url,
                                            staged_path=staged_resume_pdf_path,
                                        )
                                    ),
                                ]
                            last_payload = latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else payload
                            retry_requested = True
                            break
                        if apply_upload_requires_observation and is_observation_tool and not error_text:
                            if self._payload_confirms_staged_file_upload(
                                payload,
                                staged_path=staged_resume_pdf_path,
                            ):
                                apply_upload_requires_observation = False
                                apply_upload_modal_unresolved = False
                                post_tool_context_items.append(
                                    self._context_item(
                                        self._apply_file_upload_confirmed_message(
                                            current_url=current_url,
                                            staged_path=staged_resume_pdf_path,
                                        )
                                    )
                                )
                            elif self._payload_has_file_chooser(payload):
                                apply_upload_requires_observation = False
                                apply_upload_modal_unresolved = True
                                history_items = [
                                    tool_feedback,
                                    self._context_item(
                                        self._apply_file_upload_modal_unresolved_message(
                                            current_url=current_url,
                                            staged_path=staged_resume_pdf_path,
                                            chooser_count=self._payload_file_chooser_count(payload),
                                        )
                                    ),
                                ]
                                last_payload = latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else payload
                                retry_requested = True
                                break
                            else:
                                apply_upload_requires_observation = False
                                apply_upload_modal_unresolved = False
                    self._update_phase_memory(
                        phase_memory=phase_memory,
                        phase=phase,
                        tool_name=name,
                        arguments=arguments,
                        error_text=error_text,
                        current_url=current_url,
                        payload=payload,
                        latest_snapshot_payload=latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else None,
                        staged_resume_pdf_path=staged_resume_pdf_path,
                    )
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
                    if name == "browser_evaluate":
                        if same_page_evaluate_streak > 0 and (
                            not last_evaluate_url or current_url == last_evaluate_url
                        ):
                            same_page_evaluate_streak += 1
                        else:
                            same_page_evaluate_streak = 1
                        last_evaluate_url = current_url
                    elif not is_observation_tool:
                        same_page_evaluate_streak = 0
                        last_evaluate_url = ""
                    if self._is_no_progress_internal_tool(name):
                        if no_progress_internal_streak > 0 and (
                            not last_no_progress_internal_url or current_url == last_no_progress_internal_url
                        ):
                            no_progress_internal_streak += 1
                        else:
                            no_progress_internal_streak = 1
                        last_no_progress_internal_url = current_url
                    else:
                        no_progress_internal_streak = 0
                        last_no_progress_internal_url = ""
                    latest_page_key = ""
                    if phase.slug == "job_retrieval":
                        latest_page_key = self._job_retrieval_page_fingerprint(
                            current_url=current_url,
                            latest_snapshot_payload=latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else None,
                            last_payload=last_payload if isinstance(last_payload, dict) else None,
                        )
                        page_label = self._extract_page_label(latest_snapshot_payload) or self._extract_page_label(last_payload)
                        signal_payload = (
                            latest_snapshot_payload
                            if isinstance(latest_snapshot_payload, dict)
                            else last_payload
                            if isinstance(last_payload, dict)
                            else payload
                        )
                    empty_extraction_signal = (
                        phase.slug == "job_retrieval"
                        and name == "browser_evaluate"
                        and not error_text
                        and self._payload_has_empty_extracted_jobs(payload)
                    )
                    if empty_extraction_signal:
                        latest_empty_page_key = latest_page_key or current_page_key or current_url or "__job_retrieval__"
                        if latest_empty_page_key == empty_extraction_page_key:
                            empty_extraction_count += 1
                        else:
                            empty_extraction_page_key = latest_empty_page_key
                            empty_extraction_count = 1
                    elif (
                        phase.slug == "job_retrieval"
                        and not error_text
                        and (
                            name == "record_jobs"
                            or self._is_job_retrieval_page_action(name)
                            or (name == "browser_evaluate" and self._payload_has_extracted_jobs(payload))
                        )
                    ):
                        empty_extraction_page_key = ""
                        empty_extraction_count = 0
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
                    if empty_extraction_signal:
                        empty_extraction_limit = self._job_retrieval_empty_evaluate_limit(phase_memory)
                        if empty_extraction_count >= empty_extraction_limit:
                            summary = (
                                "same-page browser_evaluate returned empty jobs repeatedly "
                                "without recording retrieval progress"
                            )
                            self._record_browser_control_event(
                                site_store=site_store,
                                event_type="empty_extraction_loop",
                                batch_id=batch_id,
                                site_key=site_key,
                                phase=phase,
                                turn_id=turn_id,
                                current_url=current_url,
                                guard_name="empty_extraction_loop",
                                trigger_values={
                                    "empty_extraction_count": empty_extraction_count,
                                    "empty_extraction_limit": empty_extraction_limit,
                                    "page_key": empty_extraction_page_key,
                                },
                                last_record_jobs_policy=last_record_jobs_policy,
                                trace_ref=trace_ref,
                                summary=summary,
                            )
                            return BrowserPhaseResult(
                                status="failed",
                                reason_tag="empty_extraction_loop",
                                summary=summary,
                                current_url=current_url,
                                step_count=step_count,
                                trace_ref=trace_ref,
                                raw_text=output_text,
                                recorded_count=len(recorded_job_ids),
                                new_count=len(new_job_ids),
                            )
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
                    post_tool_progress_key = self._phase_progress_key(
                        phase=phase,
                        current_url=current_url,
                        latest_snapshot_payload=latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else None,
                        last_payload=last_payload if isinstance(last_payload, dict) else None,
                    )
                    no_progress_result = track_same_url_no_progress(
                        made_progress=self._tool_call_made_runtime_progress(
                            phase=phase,
                            tool_name=name,
                            error_text=error_text,
                            pre_progress_key=pre_tool_progress_key,
                            post_progress_key=post_tool_progress_key,
                            payload=payload,
                        ),
                        tool_name=name,
                        pre_progress_key=pre_tool_progress_key,
                        post_progress_key=post_tool_progress_key,
                        response_tokens=response_total_tokens,
                        output_text=output_text,
                    )
                    if no_progress_result is not None:
                        return no_progress_result
                    use_fresh_snapshot_primary_context = self._should_use_fresh_snapshot_primary_context(
                        tool_name=name,
                        fresh_snapshot_captured=fresh_snapshot_captured,
                        error_text=error_text,
                    )
                    if (
                        phase.slug == "job_retrieval"
                        and not error_text
                        and self._is_job_retrieval_page_action(name)
                    ):
                        phase_memory.clear_recent_actions()
                        history_items = []
                        if use_fresh_snapshot_primary_context:
                            history_items.append(self._context_item(self._live_snapshot_primary_message()))
                        else:
                            history_items.append(tool_feedback)
                            if fresh_snapshot_captured and name != "browser_snapshot":
                                history_items.append(self._context_item(self._live_snapshot_primary_message()))
                        history_items.extend(post_tool_context_items)
                        last_payload = latest_snapshot_payload if isinstance(latest_snapshot_payload, dict) else payload
                    else:
                        history_items = []
                        if use_fresh_snapshot_primary_context:
                            history_items.append(self._context_item(self._live_snapshot_primary_message()))
                        else:
                            history_items.append(tool_feedback)
                            if fresh_snapshot_captured and name != "browser_snapshot":
                                history_items.append(self._context_item(self._live_snapshot_primary_message()))
                        history_items.extend(post_tool_context_items)
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
                    page_action_failed = bool(error_text) and self._is_page_settle_action(name)
                    if page_action_failed:
                        failed_page_action_signature = self._page_action_signature(
                            name,
                            arguments if isinstance(arguments, dict) else None,
                        )
                        failed_page_action_name = str(name or "").strip()
                        if phase_memory.has_recent_actions():
                            phase_memory.keep_last_recent_action()
                    previous_url = current_url
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
                    stale_context_error = self._is_objective_stale_context_error(error_text)
                    if stale_context_error:
                        history_items = []
                        if (
                            isinstance(snapshot_payload, dict)
                            and snapshot_payload
                            and not bool(snapshot_payload.get("isError"))
                        ):
                            history_items.append(self._context_item(self._live_snapshot_primary_message()))
                        history_items.append(
                            self._context_item(
                                self._browser_recovery_message(
                                    phase=phase,
                                    reason="stale_context",
                                    current_url=current_url,
                                    previous_url=previous_url,
                                    detail=(
                                        "The previous tool call used stale page context for the current live page. "
                                        f"Failed tool: {name}. Observed stale-context error: {error_text}"
                                    ),
                                )
                            )
                        )
                    else:
                        history_items = [tool_feedback]
                        if (
                            isinstance(snapshot_payload, dict)
                            and snapshot_payload
                            and not bool(snapshot_payload.get("isError"))
                        ):
                            history_items.append(self._context_item(self._live_snapshot_primary_message()))
                        history_items.append(
                            self._context_item(
                                self._browser_recovery_message(
                                    phase=phase,
                                    reason="page_action_failed" if page_action_failed else "tool_call_failed",
                                    current_url=current_url,
                                    previous_url=previous_url,
                                    detail=(
                                        self._page_action_failure_recovery_message(
                                            current_url=current_url,
                                            tool_name=failed_page_action_name or name,
                                        )
                                        if page_action_failed
                                        else f"The previous tool call {name} failed with: {error_text}."
                                    ),
                                )
                            )
                        )
                    retry_requested = True
                    break
                if not error_text and self._is_page_settle_action(name):
                    failed_page_action_signature = ""
                    failed_page_action_name = ""
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
