"""Stateless Responses loop that executes local Playwright MCP function tools."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import json
import re
import time
from typing import Any
from urllib.parse import urlparse

import anyio
import httpx

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

    async def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.api_base}/responses",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        if response.status_code >= 400:
            raise RuntimeError(response.text[:2000] or f"responses api error {response.status_code}")
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("invalid responses payload")
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
        while True:
            try:
                return await self.responses.create(payload)
            except httpx.ConnectError:
                attempts += 1
                if attempts > 1:
                    raise
                await self._sleep(1.0)

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
                " During Job Retrieval, record only the current visible job cards from the live results surface. "
                "Do not scan the broader page for every /job/ link. "
                "After an extraction step yields the current page jobs, call record_jobs immediately before any more observation or pagination."
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
    ) -> None:
        recent_steps.append(
            {
                "tool": str(tool_name or "").strip(),
                "action": self._summarize_arguments(arguments),
                "result": "error" if str(error_text or "").strip() else "ok",
                "url": str(current_url or "").strip(),
                "title": MCPToolBridge.extract_page_title(payload),
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
    def _extract_job_records(cls, payload: dict[str, Any]) -> list[dict[str, Any]] | None:
        if not isinstance(payload, dict):
            return None
        structured = payload.get("structuredContent")
        for candidate in [structured, *cls._parse_result_json_blocks(payload)]:
            jobs = cls._coerce_job_records(candidate)
            if jobs is not None:
                return jobs
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
    def _job_retrieval_record_jobs_message() -> str:
        return (
            "You already extracted the current visible jobs from the live results surface. "
            "Call record_jobs now with those current-page jobs before any more observation, pagination, or another extraction. "
            "Record only the current visible results cards, not every /job/ link on the broader page."
        )

    @staticmethod
    def _job_retrieval_extracted_page_message(*, current_url: str, page_label: str) -> str:
        page_line = f"Current page label: {page_label}\n" if page_label else ""
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        return (
            "The current retrieval page already yielded a non-empty jobs extraction.\n"
            f"{url_line}"
            f"{page_line}"
            "Do not run browser_evaluate again on this same page right now. "
            "Use the already extracted current-page jobs to call record_jobs now. "
            "After that, either take a real page-changing action or finish with phase_result if the site stop condition has been met."
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
    def _job_retrieval_empty_extraction_message(*, current_url: str, page_label: str) -> str:
        page_line = f"Current page label: {page_label}\n" if page_label else ""
        url_line = f"Current page URL: {current_url}\n" if current_url else ""
        return (
            "The current retrieval page still shows live results signals, but the latest extraction returned zero jobs.\n"
            f"{url_line}"
            f"{page_line}"
            "Do not run browser_evaluate again on this same page until you first capture a fresh browser_snapshot or take a real page-changing action. "
            "After a fresh snapshot, re-identify the actual live results container and extract only the current visible results cards."
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
        list_run_jobs = getattr(site_store, "list_run_jobs", None)
        before_rows = list_jobs(site_key) if callable(list_jobs) else []
        before_ids = {str(row.get("job_id") or "") for row in before_rows if isinstance(row, dict)}
        if callable(list_run_jobs):
            for row in list_run_jobs(site_key, batch_id):
                if isinstance(row, dict):
                    job_id = str(row.get("job_id") or "").strip()
                    if job_id:
                        before_ids.add(job_id)
        saved_rows = site_store.append_jobs(site_key, jobs, session_id or "", turn_id, batch_id)
        saved_ids = []
        for row in saved_rows:
            if not isinstance(row, dict):
                continue
            job_id = str(row.get("job_id") or "").strip()
            if job_id:
                saved_ids.append(job_id)
        unique_saved_ids = sorted(set(saved_ids))
        new_ids = sorted({job_id for job_id in unique_saved_ids if job_id not in before_ids})
        recorded_count = len(unique_saved_ids)
        new_count = len(new_ids)
        summary = f"Recorded {recorded_count} jobs from the current page ({new_count} new)."
        return {
            "isError": False,
            "current_url": current_url,
            "structuredContent": {
                "current_url": current_url,
                "recorded_count": recorded_count,
                "new_count": new_count,
                "job_ids": unique_saved_ids,
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
        empty_extraction_page_key = ""
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
                return BrowserPhaseResult(
                    status="failed",
                    reason_tag="missing_tool_call",
                    summary="model returned no output items",
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
                if (
                    phase.slug == "job_retrieval"
                    and name == "browser_evaluate"
                    and empty_extraction_page_key
                    and current_page_key
                    and current_page_key == empty_extraction_page_key
                ):
                    history_items = [
                        self._context_item(
                            self._job_retrieval_empty_extraction_message(
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
                            empty_extraction_page_key = ""
                        elif (
                            name == "browser_snapshot"
                            and not error_text
                            and empty_extraction_page_key
                            and latest_page_key
                            and latest_page_key == empty_extraction_page_key
                        ):
                            empty_extraction_page_key = ""
                        elif (
                            name == "browser_evaluate"
                            and not error_text
                            and self._payload_has_extracted_jobs(payload)
                        ):
                            extracted_page_key = latest_page_key or current_page_key
                            empty_extraction_page_key = ""
                        elif (
                            name == "browser_evaluate"
                            and not error_text
                            and self._payload_has_empty_extracted_jobs(payload)
                            and (
                                self._payload_has_job_results_signal(payload)
                                or self._payload_has_job_results_signal(latest_snapshot_payload)
                                or self._payload_has_job_results_signal(last_payload)
                            )
                        ):
                            empty_extraction_page_key = latest_page_key or current_page_key
                        elif (
                            extracted_page_key
                            and latest_page_key
                            and latest_page_key != extracted_page_key
                            and not error_text
                        ):
                            extracted_page_key = ""
                        if (
                            empty_extraction_page_key
                            and latest_page_key
                            and latest_page_key != empty_extraction_page_key
                            and not error_text
                        ):
                            empty_extraction_page_key = ""
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
                    if (
                        phase.slug == "job_retrieval"
                        and name == "browser_evaluate"
                        and not error_text
                        and self._payload_has_empty_extracted_jobs(payload)
                        and empty_extraction_page_key
                    ):
                        history_items.append(
                            self._context_item(
                                self._job_retrieval_empty_extraction_message(
                                    current_url=current_url,
                                    page_label=self._extract_page_label(latest_snapshot_payload) or self._extract_page_label(last_payload),
                                )
                            )
                        )
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
