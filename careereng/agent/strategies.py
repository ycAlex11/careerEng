"""LLM-driven strategies for search and apply decisions."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from careereng.providers.base import LLMProvider, ProviderError, StructuredOutputResult
from careereng.utils import ensure_dir, now_iso, safe_file_stem, write_json


SEARCH_SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query_text": {"type": "string"},
        "target_roles": {"type": "array", "items": {"type": "string"}},
        "target_locations": {"type": "array", "items": {"type": "string"}},
        "company_preferences": {"type": "array", "items": {"type": "string"}},
        "industry_preferences": {"type": "array", "items": {"type": "string"}},
        "date_posted_after": {"type": "string"},
        "must_have": {"type": "array", "items": {"type": "string"}},
        "nice_to_have": {"type": "array", "items": {"type": "string"}},
        "google_queries": {"type": "array", "items": {"type": "string"}},
        "resolved_by": {"type": "object"},
        "source_note": {"type": "string"},
    },
    "additionalProperties": False,
}

COMPANY_CANDIDATES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "companies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "company": {"type": "string"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number"},
                    "base_url": {"type": "string"},
                },
                "required": ["company"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["companies"],
    "additionalProperties": False,
}

GOOGLE_COMPANY_CANDIDATES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "company": {"type": "string"},
                    "site_name": {"type": "string"},
                    "base_url": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                    "evidence_urls": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["company"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}

APPLY_DECISIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "apply": {"type": "boolean"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["job_id"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["decisions"],
    "additionalProperties": False,
}


class SearchStrategyEngine:
    def __init__(self, *, provider: LLMProvider, model: str, debug_dir: Path | None = None):
        self.provider = provider
        self.model = model
        self.debug_dir = debug_dir

    def _llm_json(
        self,
        messages: list[dict[str, Any]],
        *,
        schema: dict[str, Any],
        schema_name: str,
    ) -> StructuredOutputResult:
        chat_json = getattr(self.provider, "chat_json", None)
        if callable(chat_json):
            try:
                return chat_json(
                    messages,
                    model=self.model,
                    schema=schema,
                    schema_name=schema_name,
                )
            except ProviderError as exc:
                return StructuredOutputResult(
                    data={},
                    raw=f"Provider error: {exc}",
                    mode="error",
                    used_fallback=True,
                )

        try:
            raw = self.provider.chat(messages, model=self.model)
        except Exception as exc:
            return StructuredOutputResult(
                data={},
                raw=f"Provider error: {exc}",
                mode="error",
                used_fallback=True,
            )
        parsed = LLMProvider.parse_json_object(raw)
        if isinstance(parsed, dict):
            return StructuredOutputResult(data=parsed, raw=raw, mode="plain_text")

        repair_prompt = "Convert the previous output into a strict JSON object only. Do not add markdown or commentary."
        repair_input: dict[str, Any] = {
            "schema_name": schema_name,
            "previous_output": raw,
        }
        if schema:
            repair_prompt += " Match the requested schema as closely as possible. Omit unknown fields."
            repair_input["schema"] = schema
        repair_messages = [
            {"role": "system", "content": repair_prompt},
            {"role": "user", "content": json.dumps(repair_input, ensure_ascii=False)},
        ]
        try:
            repaired_raw = self.provider.chat(repair_messages, model=self.model)
        except Exception as exc:
            return StructuredOutputResult(
                data={},
                raw=raw,
                repaired_raw=f"Provider error: {exc}",
                mode="text_repair",
                used_fallback=True,
            )
        parsed = LLMProvider.parse_json_object(repaired_raw)
        return StructuredOutputResult(
            data=parsed if isinstance(parsed, dict) else {},
            raw=raw,
            repaired_raw=repaired_raw,
            mode="text_repair",
            used_fallback=True,
        )

    def _is_abstract_company_name(self, value: str) -> bool:
        name = str(value or "").strip()
        if not name:
            return True
        lowered = name.lower()
        abstract_terms = (
            "product companies",
            "internal tools",
            "software vendors",
            "digital twin",
            "companies",
            "vendors",
            "外企",
            "大厂",
            "百人公司",
            "欧美企业",
            "big tech",
        )
        if any(term in lowered for term in abstract_terms):
            return True
        if "/" in lowered and any(term in lowered for term in ("ai", "llm", "saas", "enterprise", "industrial")):
            return True
        return False

    def _write_candidate_debug(self, query_id: str, payload: dict[str, Any]) -> None:
        if self.debug_dir is None:
            return
        file_name = safe_file_stem(query_id or now_iso()) or "search-candidates"
        write_json(ensure_dir(self.debug_dir) / f"{file_name}.json", payload)

    def _normalize_company_candidates(self, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        out: list[dict[str, Any]] = []
        invalid_names: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            company = str(row.get("company") or "").strip()
            if not company:
                continue
            if self._is_abstract_company_name(company):
                invalid_names.append(company)
                continue
            confidence = row.get("confidence", 0.0)
            try:
                conf = float(confidence)
            except Exception:
                conf = 0.0
            out.append(
                {
                    "company": company,
                    "site_id": safe_file_stem(company),
                    "site_name": company,
                    "base_url": str(row.get("base_url") or "").strip(),
                    "reason": str(row.get("reason") or ""),
                    "confidence": max(0.0, min(1.0, conf)),
                    "evidence_urls": [],
                }
            )
        return out, invalid_names

    def extract_search_spec(
        self,
        *,
        user_message: str,
        persona: dict[str, Any],
        intent: dict[str, Any],
        search_skill_text: str,
        default_builder: Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        prompt = (
            "Build a job-search query spec JSON.\n"
            "Priority order for conflicts: current user message > workspace user job skill > project search skills > intent > defaults.\n"
            "Return JSON only with keys: query_text,target_roles,target_locations,company_preferences,"
            "industry_preferences,date_posted_after,must_have,nice_to_have,google_queries,resolved_by,source_note."
        )
        messages = [{"role": "system", "content": prompt}]
        if search_skill_text.strip():
            messages.append({"role": "system", "content": "Search skills policy:\n" + search_skill_text})
        messages.append(
            {
                "role": "user",
                "content": json.dumps(
                    {"message": user_message, "persona": persona, "intent": intent},
                    ensure_ascii=False,
                ),
            }
        )
        parsed = self._llm_json(messages, schema=SEARCH_SPEC_SCHEMA, schema_name="search_spec").data
        spec = default_builder(user_message, persona, intent)
        if parsed:
            for key in (
                "query_text",
                "target_roles",
                "target_locations",
                "company_preferences",
                "industry_preferences",
                "date_posted_after",
                "must_have",
                "nice_to_have",
                "google_queries",
                "resolved_by",
                "source_note",
            ):
                if key not in parsed:
                    continue
                val = parsed.get(key)
                if isinstance(spec.get(key), list) and isinstance(val, list):
                    spec[key] = [str(x) for x in val if str(x).strip()]
                elif isinstance(spec.get(key), dict) and isinstance(val, dict):
                    spec[key] = val
                elif isinstance(spec.get(key), str) and isinstance(val, str) and val.strip():
                    spec[key] = val.strip()
        if not isinstance(spec.get("google_queries"), list) or not spec["google_queries"]:
            spec["google_queries"] = [spec.get("query_text", "job search")]
        return spec

    def _filter_company_candidates(
        self,
        *,
        candidates: list[dict[str, Any]],
        user_message: str,
        intent: dict[str, Any],
        job_skill_text: str,
        top_k: int,
    ) -> tuple[list[dict[str, Any]], StructuredOutputResult, bool]:
        if not candidates:
            return [], StructuredOutputResult(data={}, mode="skip"), False
        messages = [
            {
                "role": "system",
                "content": (
                    "Filter company candidates by search constraints. "
                    "Return strict JSON only: {\"companies\":[{\"company\",\"reason\",\"confidence\",\"base_url\"}]}. "
                    "Keep only companies that satisfy the resolved search constraints. "
                    "Use priority order: current user message > workspace user job skill > project search skills > intent. "
                    "If a company clearly violates the higher-priority constraints, remove it."
                ),
            }
        ]
        if job_skill_text.strip():
            messages.append({"role": "system", "content": "Job search skill policy:\n" + job_skill_text})
        messages.append(
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "message": user_message,
                        "intent": intent,
                        "top_k": top_k,
                        "candidates": candidates,
                    },
                    ensure_ascii=False,
                ),
            }
        )
        result = self._llm_json(messages, schema=COMPANY_CANDIDATES_SCHEMA, schema_name="company_candidate_filter")
        parsed = result.data
        if not isinstance(parsed, dict) or not isinstance(parsed.get("companies"), list):
            return [], result, False
        rows = parsed.get("companies") if isinstance(parsed.get("companies"), list) else []
        filtered, _ = self._normalize_company_candidates(rows if isinstance(rows, list) else [])
        return filtered, result, True

    def generate_company_candidates(
        self,
        *,
        user_message: str,
        intent: dict[str, Any],
        job_skill_text: str,
        top_k: int = 10,
        query_id: str = "",
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(top_k or 10), 30))
        prompt = (
            "Recommend target companies for job applications based on the current user request, workspace user job skill, project search skills, and intent.\n"
            "Priority order for conflicts: current user message > workspace user job skill > project search skills > intent.\n"
            "Return JSON only: {\"companies\":[{\"company\",\"reason\",\"confidence\",\"base_url\"}]}.\n"
            "company must be a real employer name.\n"
            "Do not return categories, industries, company types, or abstract labels.\n"
            "Obey higher-priority employer, company-size, location, and industry preferences when they are present.\n"
            "If you cannot satisfy the constraints, return fewer companies instead of violating them.\n"
            "Do not use markdown."
        )
        messages = [{"role": "system", "content": prompt}]
        if job_skill_text.strip():
            messages.append({"role": "system", "content": "Job search skill policy:\n" + job_skill_text})
        messages.append(
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "message": user_message,
                        "intent": intent,
                        "top_k": limit,
                    },
                    ensure_ascii=False,
                ),
            }
        )
        result = self._llm_json(messages, schema=COMPANY_CANDIDATES_SCHEMA, schema_name="company_candidates")
        rows = result.data.get("companies") if isinstance(result.data.get("companies"), list) else []
        out, invalid_names = self._normalize_company_candidates(rows if isinstance(rows, list) else [])
        all_invalid_names = list(invalid_names)
        filtered_out, filter_result, filter_applied = self._filter_company_candidates(
            candidates=out,
            user_message=user_message,
            intent=intent,
            job_skill_text=job_skill_text,
            top_k=limit,
        )
        if filter_applied:
            out = filtered_out

        retry_result = StructuredOutputResult(data={}, mode="skip")
        retry_filter_result = StructuredOutputResult(data={}, mode="skip")
        if not out:
            retry_messages = messages + [
                {
                    "role": "system",
                    "content": (
                        "Retry company recommendation with a stricter reading of the current request and skill constraints. "
                        "Use priority order: current user message > workspace user job skill > project search skills > intent. "
                        "If uncertain, return fewer companies rather than broad matches."
                    ),
                }
            ]
            retry_result = self._llm_json(
                retry_messages,
                schema=COMPANY_CANDIDATES_SCHEMA,
                schema_name="company_candidates_retry",
            )
            rows = retry_result.data.get("companies") if isinstance(retry_result.data.get("companies"), list) else []
            out, retry_invalid_names = self._normalize_company_candidates(rows if isinstance(rows, list) else [])
            all_invalid_names.extend(retry_invalid_names)
            filtered_out, retry_filter_result, retry_filter_applied = self._filter_company_candidates(
                candidates=out,
                user_message=user_message,
                intent=intent,
                job_skill_text=job_skill_text,
                top_k=limit,
            )
            if retry_filter_applied:
                out = filtered_out

        self._write_candidate_debug(
            query_id,
            {
                "ts": now_iso(),
                "query_id": query_id,
                "input": {
                    "message": user_message,
                    "intent": intent,
                    "job_skill_text": job_skill_text,
                    "top_k": limit,
                },
                "raw": result.raw,
                "structured_mode": result.mode,
                "repaired_raw": result.repaired_raw,
                "filter_raw": filter_result.raw,
                "filter_mode": filter_result.mode,
                "filter_repaired_raw": filter_result.repaired_raw,
                "retry_raw": retry_result.raw,
                "retry_mode": retry_result.mode,
                "retry_repaired_raw": retry_result.repaired_raw,
                "retry_filter_raw": retry_filter_result.raw,
                "retry_filter_mode": retry_filter_result.mode,
                "retry_filter_repaired_raw": retry_filter_result.repaired_raw,
                "invalid_names": sorted({name for name in all_invalid_names if name}),
                "candidates": out,
            },
        )
        dedup: dict[str, dict[str, Any]] = {}
        for row in out:
            key = str(row.get("site_id") or "")
            if key and key not in dedup:
                dedup[key] = row
        merged = list(dedup.values())
        merged.sort(key=lambda x: float(x.get("confidence", 0.0)), reverse=True)
        return merged[:limit]

    def summarize_google_company_candidates(
        self,
        *,
        query_id: str,
        search_spec: dict[str, Any],
        all_web_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        compact_items = []
        for item in all_web_items[:60]:
            if not isinstance(item, dict):
                continue
            compact_items.append(
                {
                    "title": str(item.get("title") or ""),
                    "url": str(item.get("url") or ""),
                    "snippet": str(item.get("snippet") or ""),
                }
            )
        if not compact_items:
            return []
        prompt = (
            "From search results, recommend up to 6 job-target companies.\n"
            "Return JSON object only: {\"candidates\":[{\"company\",\"site_name\",\"base_url\",\"confidence\",\"reason\",\"evidence_urls\"}]}.\n"
            "Do not include companies without evidence URLs."
        )
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": json.dumps({"search_spec": search_spec, "results": compact_items}, ensure_ascii=False),
            },
        ]
        parsed = self._llm_json(
            messages,
            schema=GOOGLE_COMPANY_CANDIDATES_SCHEMA,
            schema_name="google_company_candidates",
        ).data
        out: list[dict[str, Any]] = []
        raw_candidates = parsed.get("candidates") if isinstance(parsed, dict) else []
        if isinstance(raw_candidates, list):
            for row in raw_candidates:
                if not isinstance(row, dict):
                    continue
                company = str(row.get("company") or row.get("site_name") or "").strip()
                base_url = str(row.get("base_url") or "").strip()
                confidence = row.get("confidence")
                try:
                    conf = float(confidence)
                except Exception:
                    conf = 0.0
                evidence_urls = row.get("evidence_urls") if isinstance(row.get("evidence_urls"), list) else []
                evidence_urls = [str(x) for x in evidence_urls if str(x).startswith("http")]
                if not company:
                    continue
                if not base_url and evidence_urls:
                    base_url = evidence_urls[0]
                out.append(
                    {
                        "query_id": query_id,
                        "company": company,
                        "site_id": safe_file_stem(company),
                        "site_name": str(row.get("site_name") or company),
                        "base_url": base_url,
                        "confidence": conf,
                        "reason": str(row.get("reason") or ""),
                        "evidence_urls": evidence_urls[:5],
                    }
                )
        if out:
            return out[:6]

        by_domain: dict[str, dict[str, Any]] = {}
        for item in compact_items:
            url = str(item.get("url") or "")
            title = str(item.get("title") or "")
            m = re.match(r"https?://([^/]+)", url)
            if not m:
                continue
            domain = m.group(1).lower()
            if domain.startswith("www."):
                domain = domain[4:]
            if "google." in domain:
                continue
            entry = by_domain.get(domain)
            if entry is None:
                company = title.split("-")[0].strip() or domain.split(".")[0]
                by_domain[domain] = {
                    "query_id": query_id,
                    "company": company,
                    "site_id": safe_file_stem(company),
                    "site_name": company,
                    "base_url": "https://" + domain,
                    "confidence": 0.45,
                    "reason": "domain frequency fallback",
                    "evidence_urls": [url],
                    "_count": 1,
                }
            else:
                entry["_count"] = int(entry.get("_count", 1)) + 1
                if len(entry["evidence_urls"]) < 5:
                    entry["evidence_urls"].append(url)
        out = list(by_domain.values())
        out.sort(key=lambda x: int(x.get("_count", 0)), reverse=True)
        for row in out:
            row.pop("_count", None)
        return out[:6]

    def evaluate_jobs_for_apply(
        self,
        *,
        site_name: str,
        jobs: list[dict[str, Any]],
        persona: dict[str, Any],
        intent: dict[str, Any],
        cv_text: str = "",
        project_job_skill_text: str = "",
        site_job_skill_text: str = "",
    ) -> list[dict[str, Any]]:
        if not jobs:
            return []
        prompt = (
            "Evaluate job fit for automatic application.\n"
            "Priority order for conflicts: site job skill > project jobs skill > intent.\n"
            "Use persona and current CV as factual background.\n"
            "Return JSON only: {\"decisions\":[{\"job_id\",\"apply\",\"confidence\",\"reason\"}]}.\n"
            "If confidence < 0.65, set apply=false."
        )
        messages = [{"role": "system", "content": prompt}]
        if project_job_skill_text.strip():
            messages.append({"role": "system", "content": "Project jobs skill:\n" + project_job_skill_text})
        if site_job_skill_text.strip():
            messages.append({"role": "system", "content": "Site job skill (highest priority):\n" + site_job_skill_text})
        messages.append(
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "site_name": site_name,
                        "persona": persona,
                        "intent": intent,
                        "current_cv": cv_text[:24000],
                        "jobs": jobs[:20],
                    },
                    ensure_ascii=False,
                ),
            },
        )
        parsed = self._llm_json(
            messages,
            schema=APPLY_DECISIONS_SCHEMA,
            schema_name="apply_decisions",
        ).data
        decisions = parsed.get("decisions") if isinstance(parsed.get("decisions"), list) else []
        by_id: dict[str, dict[str, Any]] = {}
        for row in decisions:
            if not isinstance(row, dict):
                continue
            job_id = str(row.get("job_id") or "")
            if not job_id:
                continue
            by_id[job_id] = row

        chosen: list[dict[str, Any]] = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            job_id = str(job.get("job_id") or "")
            dec = by_id.get(job_id, {})
            confidence = dec.get("confidence", 0.0)
            try:
                conf = float(confidence)
            except Exception:
                conf = 0.0
            apply_state = str(job.get("apply_state") or "").lower()
            if "view application" in apply_state:
                apply = False
                conf = 1.0
                reason = "site already marks this role as applied"
                source = "site_signal"
                decision_status = "already_applied"
            else:
                apply = bool(dec.get("apply")) and conf >= 0.65
                reason = str(dec.get("reason") or "")
                source = "llm"
                decision_status = "recommended_apply" if apply else "filtered_out"
            job["fit_apply"] = apply
            job["fit_confidence"] = conf
            job["fit_reason"] = reason
            job["fit_source"] = source
            job["decision_status"] = decision_status
            if apply:
                chosen.append(job)

        if chosen:
            return chosen[:10]

        role_terms = [str(x).lower() for x in intent.get("target_roles", []) if str(x).strip()]
        if not role_terms:
            role_terms = ["engineer"]
        fallback = []
        for job in jobs:
            title = str(job.get("title") or "").lower()
            if any(term in title for term in role_terms):
                job["fit_apply"] = True
                job["fit_confidence"] = 0.65
                job["fit_reason"] = "fallback title match"
                job["fit_source"] = "fallback"
                job["decision_status"] = "recommended_apply"
                fallback.append(job)
            elif isinstance(job, dict):
                job["fit_apply"] = False
                job["fit_confidence"] = 0.0
                job["fit_reason"] = "fallback filtered out"
                job["fit_source"] = "fallback"
                job["decision_status"] = "filtered_out"
        if fallback:
            return fallback[:2]
        if jobs:
            first = jobs[0]
            if isinstance(first, dict):
                first["fit_apply"] = True
                first["fit_confidence"] = 0.55
                first["fit_reason"] = "fallback single candidate"
                first["fit_source"] = "fallback"
                first["decision_status"] = "recommended_apply"
            for job in jobs[1:]:
                if isinstance(job, dict):
                    job["fit_apply"] = False
                    job["fit_confidence"] = 0.0
                    job["fit_reason"] = "fallback filtered out"
                    job["fit_source"] = "fallback"
                    job["decision_status"] = "filtered_out"
        return jobs[:1]
