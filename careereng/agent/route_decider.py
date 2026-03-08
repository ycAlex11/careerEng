"""LLM-first route decision with deterministic fallback."""

from __future__ import annotations

import json
from typing import Any

from careereng.agent.router import detect_search_request, detect_site_request
from careereng.providers.base import LLMProvider


class RouteDecider:
    def __init__(self, *, provider: LLMProvider, model: str, confidence_threshold: float = 0.75):
        self.provider = provider
        self.model = model
        self.confidence_threshold = float(confidence_threshold or 0.0)

    def _parse_json_object(self, text: str) -> dict[str, Any] | None:
        raw = (text or "").strip()
        if not raw:
            return None
        candidates = [raw]
        if raw.startswith("```"):
            start = raw.find("\n")
            end = raw.rfind("```")
            if start != -1 and end > start:
                candidates.append(raw[start + 1 : end].strip())
        first, last = raw.find("{"), raw.rfind("}")
        if first != -1 and last > first:
            candidates.append(raw[first : last + 1].strip())
        for candidate in candidates:
            try:
                data = json.loads(candidate)
            except Exception:
                continue
            if isinstance(data, dict):
                return data
        return None

    def _parse_llm_decision(self, text: str) -> dict[str, Any] | None:
        parsed = self._parse_json_object(text)
        if not isinstance(parsed, dict):
            return None
        return self._normalize_llm_decision(parsed)

    def _repair_llm_decision(self, raw: str) -> dict[str, Any] | None:
        if not str(raw or "").strip():
            return None
        repair_messages = [
            {
                "role": "system",
                "content": (
                    "Convert the previous route classification into strict JSON only. "
                    "Return keys: route, confidence, reason_tag, params. "
                    "route must be one of chat/search/site. "
                    "For search params return {query}. "
                    "For site params return {company, base_url, apply_requested}."
                ),
            },
            {"role": "user", "content": raw},
        ]
        try:
            repaired_raw = self.provider.chat(repair_messages, model=self.model)
        except Exception:
            return None
        return self._parse_llm_decision(repaired_raw)

    def _normalize_llm_decision(self, parsed: dict[str, Any]) -> dict[str, Any] | None:
        route = str(parsed.get("route") or "").strip().lower()
        if route not in {"chat", "search", "site"}:
            return None

        confidence = parsed.get("confidence", 0.0)
        try:
            conf = float(confidence)
        except Exception:
            conf = 0.0
        conf = max(0.0, min(1.0, conf))

        params = parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
        normalized: dict[str, Any] = {
            "route": route,
            "confidence": conf,
            "reason_tag": str(parsed.get("reason_tag") or ""),
            "params": {},
        }
        if route == "search":
            query = str(params.get("query") or "").strip()
            if query:
                normalized["params"]["query"] = query
        if route == "site":
            normalized["params"] = {
                "company": str(params.get("company") or "").strip(),
                "base_url": str(params.get("base_url") or "").strip(),
                "apply_requested": bool(params.get("apply_requested")),
            }
        return normalized

    def _fallback_decision(self, message: str) -> dict[str, Any]:
        search = detect_search_request(message)
        if search.get("is_search_flow"):
            return {
                "route": "search",
                "confidence": 0.55,
                "reason_tag": "fallback.search_keyword",
                "params": {"query": str(search.get("query") or message)},
            }

        site = detect_site_request(message)
        if site.get("is_site_flow"):
            return {
                "route": "site",
                "confidence": 0.6,
                "reason_tag": "fallback.site_keyword",
                "params": {
                    "company": str(site.get("company") or ""),
                    "base_url": str(site.get("base_url") or ""),
                    "apply_requested": bool(site.get("apply_requested")),
                },
            }

        return {
            "route": "chat",
            "confidence": 0.4,
            "reason_tag": "fallback.default_chat",
            "params": {},
        }

    def decide(self, *, message: str, persona: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
        llm_raw = ""
        llm_decision: dict[str, Any] | None = None
        try:
            prompt = (
                "You are a route classifier for a CLI job-search assistant that already has persona and intent context.\n"
                "Return strict JSON only with keys: route, confidence, reason_tag, params.\n"
                "route must be one of: chat, search, site.\n"
                "Use route=search when the user wants help finding suitable jobs, target companies, job directions, or company recommendations.\n"
                "Examples of route=search include messages like: 我现在在找工作, 帮我看看哪些适合我的岗位, 请推荐一些公司.\n"
                "Use route=site when the user clearly targets a specific company, website, careers URL, or asks to apply/submit/register on that site.\n"
                "Use route=chat only for normal conversation or questions that are not asking the agent to search jobs or target companies.\n"
                "params for search: {query}.\n"
                "params for site: {company, base_url, apply_requested}."
            )
            messages = [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"message": message, "persona": persona, "intent": intent},
                        ensure_ascii=False,
                    ),
                },
            ]
            llm_raw = self.provider.chat(messages, model=self.model)
            llm_decision = self._parse_llm_decision(llm_raw)
            if llm_decision is None:
                llm_decision = self._repair_llm_decision(llm_raw)
        except Exception:
            llm_decision = None

        fallback = self._fallback_decision(message)
        llm_ok = bool(llm_decision and float(llm_decision.get("confidence", 0.0)) >= self.confidence_threshold)
        selected = llm_decision if llm_ok and llm_decision else fallback
        final_route = str(selected.get("route") or "chat")
        final_params = dict(selected.get("params") or {})

        if final_route == "search":
            if not str(final_params.get("query") or "").strip():
                final_params["query"] = message
        elif final_route == "site":
            fb_params = fallback.get("params") if fallback.get("route") == "site" else {}
            if not isinstance(fb_params, dict):
                fb_params = {}
            for key in ("company", "base_url"):
                if key not in final_params or final_params.get(key) in ("", None):
                    final_params[key] = fb_params.get(key, "")
            if not bool(final_params.get("apply_requested")) and bool(fb_params.get("apply_requested")):
                final_params["apply_requested"] = True
            final_params["apply_requested"] = bool(final_params.get("apply_requested"))

        return {
            "final_route": final_route,
            "final_params": final_params,
            "decision_source": "llm" if llm_ok and llm_decision else "fallback",
            "fallback_used": not (llm_ok and llm_decision),
            "threshold": self.confidence_threshold,
            "llm": {
                "route": str(llm_decision.get("route") or "") if llm_decision else "",
                "confidence": float(llm_decision.get("confidence", 0.0)) if llm_decision else 0.0,
                "reason_tag": str(llm_decision.get("reason_tag") or "") if llm_decision else "",
                "params": dict(llm_decision.get("params") or {}) if llm_decision else {},
                "raw": llm_raw,
                "valid": bool(llm_decision),
            },
            "fallback": fallback,
        }
