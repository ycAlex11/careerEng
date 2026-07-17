"""OpenAI-compatible provider client."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from careereng.platform.observability import LLMUsageRecorder, extract_usage
from careereng.orchestration.agent_protocol.llm import LLMProvider, ProviderError, StructuredOutputResult


DEFAULT_PROVIDER_TIMEOUT_SECONDS = 180.0


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str = ""


@dataclass
class ToolChatResult:
    tool_calls: list[ToolCall]
    raw: dict[str, Any]
    mode: str


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        *,
        api_key: str,
        api_base: str,
        structured_output_mode: str = "auto",
        provider_name: str = "openai",
        metrics_recorder: LLMUsageRecorder | None = None,
    ):
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.structured_output_mode = str(structured_output_mode or "auto").strip().lower() or "auto"
        self._unsupported_structured_modes: set[str] = set()
        self.provider_name = str(provider_name or "openai")
        self.metrics_recorder = metrics_recorder

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        model = str(payload.get("model") or "")
        if not self.api_key:
            self._record_chat_metric(
                model=model,
                started=started,
                status="error",
                error_type="missing_api_key",
            )
            raise ProviderError("API key is missing")
        try:
            resp = httpx.post(
                f"{self.api_base}/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=DEFAULT_PROVIDER_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            self._record_chat_metric(
                model=model,
                started=started,
                status="error",
                error_type=exc.__class__.__name__,
            )
            raise ProviderError(f"provider request failed: {exc}") from exc

        if resp.status_code >= 400:
            self._record_chat_metric(
                model=model,
                started=started,
                status="error",
                error_type=f"http_{resp.status_code}",
            )
            raise ProviderError(f"provider error {resp.status_code}: {resp.text[:300]}")

        try:
            data = resp.json()
        except Exception as exc:
            self._record_chat_metric(
                model=model,
                started=started,
                status="error",
                error_type="invalid_json",
            )
            raise ProviderError("invalid provider response") from exc
        if not isinstance(data, dict):
            self._record_chat_metric(
                model=model,
                started=started,
                status="error",
                error_type="invalid_response",
            )
            raise ProviderError("invalid provider response")
        self._record_chat_metric(model=model, started=started, status="ok", usage=data.get("usage"))
        return data

    def _record_chat_metric(
        self,
        *,
        model: str,
        started: float,
        status: str,
        usage: Any = None,
        error_type: str = "",
    ) -> None:
        recorder = self.metrics_recorder
        if recorder is None:
            return
        recorder.record(
            provider=self.provider_name,
            model=model,
            api_type="chat_completions",
            operation="provider_chat",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            status=status,
            error_type=error_type,
            **extract_usage(usage),
        )

    def _extract_text_content(self, data: dict[str, Any]) -> str:
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception as exc:
            raise ProviderError("invalid provider response") from exc
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    if isinstance(item.get("text"), str):
                        parts.append(item["text"])
                    elif isinstance(item.get("content"), str):
                        parts.append(item["content"])
                elif item is not None:
                    parts.append(str(item))
            return "".join(parts)
        if isinstance(content, (dict, list)):
            return json.dumps(content, ensure_ascii=False)
        return str(content or "")

    @staticmethod
    def _safe_schema_name(value: str) -> str:
        raw = "".join(ch if ch.isalnum() else "_" for ch in str(value or "response"))
        raw = raw.strip("_") or "response"
        if raw[0].isdigit():
            raw = "schema_" + raw
        return raw[:64]

    def _structured_mode_candidates(self, requested: str) -> list[str]:
        mode = str(requested or self.structured_output_mode or "auto").strip().lower() or "auto"
        if mode == "json_schema_strict":
            return ["json_schema", "json_object"]
        if mode == "json_object_strict":
            return ["json_object"]
        if mode in {"text_repair", "text_repair_only", "plain_text"}:
            return ["text_repair"]
        if mode == "json_object":
            return ["json_object", "text_repair"]
        if mode == "json_schema":
            return ["json_schema", "json_object", "text_repair"]
        return ["json_schema", "json_object", "text_repair"]

    @staticmethod
    def _should_cache_structured_failure(error_text: str) -> bool:
        lowered = str(error_text or "").lower()
        if "provider error 400" not in lowered and "provider error 422" not in lowered:
            return False
        markers = (
            "response_format",
            "json_schema",
            "json_object",
            "unsupported",
            "not supported",
            "unknown parameter",
            "extra inputs are not permitted",
            "invalid type",
        )
        return any(marker in lowered for marker in markers)

    def _structured_payload(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        schema: dict[str, Any],
        schema_name: str,
        mode: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if mode == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": self._safe_schema_name(schema_name),
                    "schema": schema,
                    "strict": True,
                },
            }
        elif mode == "json_object":
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _responses_tool_payload(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        payload_tools: list[dict[str, Any]] = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            function = tool.get("function") if isinstance(tool.get("function"), dict) else None
            if function is not None:
                name = str(function.get("name") or "").strip()
                if not name:
                    continue
                row = {
                    "type": "function",
                    "name": name,
                    "description": str(function.get("description") or ""),
                    "parameters": function.get("parameters") if isinstance(function.get("parameters"), dict) else {},
                }
                payload_tools.append(row)
                continue
            name = str(tool.get("name") or "").strip()
            if name:
                row = {
                    "type": "function",
                    "name": name,
                    "description": str(tool.get("description") or ""),
                    "parameters": tool.get("parameters") if isinstance(tool.get("parameters"), dict) else {},
                }
                payload_tools.append(row)
        return payload_tools

    @staticmethod
    def _parse_arguments(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        try:
            data = json.loads(str(value or "{}"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _extract_responses_tool_calls(self, data: dict[str, Any]) -> list[ToolCall]:
        rows: list[ToolCall] = []
        output = data.get("output") if isinstance(data.get("output"), list) else []
        for item in output:
            if not isinstance(item, dict) or str(item.get("type") or "") != "function_call":
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            rows.append(
                ToolCall(
                    name=name,
                    arguments=self._parse_arguments(item.get("arguments")),
                    call_id=str(item.get("call_id") or item.get("id") or ""),
                )
            )
        return rows

    def _extract_chat_tool_calls(self, data: dict[str, Any]) -> list[ToolCall]:
        try:
            tool_calls = data["choices"][0]["message"]["tool_calls"]
        except Exception:
            tool_calls = []
        rows: list[ToolCall] = []
        for item in tool_calls if isinstance(tool_calls, list) else []:
            if not isinstance(item, dict):
                continue
            function = item.get("function") if isinstance(item.get("function"), dict) else {}
            name = str(function.get("name") or "").strip()
            if not name:
                continue
            rows.append(
                ToolCall(
                    name=name,
                    arguments=self._parse_arguments(function.get("arguments")),
                    call_id=str(item.get("id") or ""),
                )
            )
        return rows

    def _post_responses(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise ProviderError("API key is missing")
        try:
            resp = httpx.post(
                f"{self.api_base}/responses",
                json=payload,
                headers=self._headers(),
                timeout=DEFAULT_PROVIDER_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            raise ProviderError(f"provider request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise ProviderError(f"provider error {resp.status_code}: {resp.text[:300]}")
        try:
            data = resp.json()
        except Exception as exc:
            raise ProviderError("invalid provider response") from exc
        if not isinstance(data, dict):
            raise ProviderError("invalid provider response")
        return data

    def chat(self, messages: list[dict[str, Any]], *, model: str) -> str:
        payload = {
            "model": model,
            "messages": messages,
        }
        return self._extract_text_content(self._post_chat(payload))

    def chat_tools(self, messages: list[dict[str, Any]], *, model: str, tools: list[dict[str, Any]]) -> ToolChatResult:
        responses_payload = {
            "model": model,
            "input": messages,
            "tools": self._responses_tool_payload(tools),
            "tool_choice": "required",
            "store": False,
        }
        try:
            data = self._post_responses(responses_payload)
        except ProviderError:
            chat_payload = {
                "model": model,
                "messages": messages,
                "tools": tools,
                "tool_choice": "required",
            }
            data = self._post_chat(chat_payload)
            return ToolChatResult(tool_calls=self._extract_chat_tool_calls(data), raw=data, mode="tool_calls")
        return ToolChatResult(tool_calls=self._extract_responses_tool_calls(data), raw=data, mode="responses")

    def chat_json(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        schema: dict[str, Any] | None = None,
        schema_name: str = "response",
        json_mode: str = "auto",
    ) -> StructuredOutputResult:
        requested_mode = str(json_mode or self.structured_output_mode or "auto").strip().lower() or "auto"
        if not isinstance(schema, dict) or not schema:
            return super().chat_json(
                messages,
                model=model,
                schema=schema,
                schema_name=schema_name,
                json_mode=requested_mode,
            )

        for mode in self._structured_mode_candidates(requested_mode):
            if mode == "text_repair":
                break
            if requested_mode == "auto" and mode in self._unsupported_structured_modes:
                continue
            try:
                payload = self._structured_payload(
                    messages=messages,
                    model=model,
                    schema=schema,
                    schema_name=schema_name,
                    mode=mode,
                )
                raw = self._extract_text_content(self._post_chat(payload))
            except ProviderError as exc:
                if requested_mode == "auto" and self._should_cache_structured_failure(str(exc)):
                    self._unsupported_structured_modes.add(mode)
                continue
            parsed = self.parse_json_object(raw)
            if isinstance(parsed, dict):
                return StructuredOutputResult(
                    data=parsed,
                    raw=raw,
                    mode=mode,
                    used_fallback=(requested_mode == "auto" and mode != "json_schema"),
                )

        if requested_mode in {"json_schema_strict", "json_object_strict"}:
            return StructuredOutputResult(data={}, mode=requested_mode, used_fallback=True)

        result = super().chat_json(
            messages,
            model=model,
            schema=schema,
            schema_name=schema_name,
            json_mode=requested_mode,
        )
        result.used_fallback = True
        return result
