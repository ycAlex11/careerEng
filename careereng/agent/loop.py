"""Main agent loop for `careereng run`."""

from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path
from typing import Any

from careereng.agent.channel_locator import ChannelLocator
from careereng.agent.context import ContextBuilder
from careereng.agent.extractor import CandidateExtractor
from careereng.agent.fallbacks import default_search_spec, minimal_intent_candidate_from_persona
from careereng.agent.profile_pipeline import ProfilePipeline
from careereng.agent.relatedness import RelatednessEvaluator
from careereng.agent.route_decider import RouteDecider
from careereng.agent.response_templates import (
    format_company_index_pick_prompt,
    format_company_pick_prompt,
    format_site_result_text,
)
from careereng.agent.search_flow import SearchFlow
from careereng.agent.strategies import SearchStrategyEngine
from careereng.agent.router import detect_search_request, is_no, is_yes, parse_yes_no_reason
from careereng.providers.base import LLMProvider, ProviderError
from careereng.session.manager import SessionManager
from careereng.storage.application_store import ApplicationStore
from careereng.storage.chat_store import ChatStore
from careereng.storage.intent_store import IntentStore
from careereng.storage.profile_store import ProfileStore
from careereng.storage.router_store import RouterStore
from careereng.storage.run_store import RunStore
from careereng.storage.search_store import SearchStore
from careereng.tools.site_tools import SiteTools
from careereng.utils import make_id, safe_file_stem


class AgentLoop:
    def __init__(
        self,
        *,
        project_root: Path,
        workspace: Path,
        provider: LLMProvider,
        model: str,
        max_history_messages: int,
        related_history_k: int,
        relatedness_threshold: float,
        router_confidence_threshold: float = 0.75,
        router_log_enabled: bool = True,
        search_company_top_k: int = 10,
        site_parallelism: int = 2,
        site_tools: SiteTools,
    ):
        self.workspace = workspace
        self.project_root = project_root
        self.provider = provider
        self.model = model
        self.max_history_messages = max_history_messages
        self.related_history_k = related_history_k
        self.router_log_enabled = bool(router_log_enabled)
        self.search_company_top_k = max(1, int(search_company_top_k or 1))
        self.site_parallelism = max(1, int(site_parallelism or 1))

        self.session_manager = SessionManager(workspace)
        self.chat_store = ChatStore(workspace)
        self.profile_store = ProfileStore(workspace)
        self.intent_store = IntentStore(workspace)
        self.run_store = RunStore(workspace)
        self.router_store = RouterStore(workspace)
        self.search_store = SearchStore(workspace)
        self.application_store = ApplicationStore(workspace)
        self.site_tools = site_tools
        self.channel_locator = ChannelLocator(
            site_tools=self.site_tools,
            search_store=self.search_store,
        )
        self.search_flow = SearchFlow(
            site_tools=self.site_tools,
            save_state_fn=self._save_session_state,
            channel_locator=self.channel_locator,
        )

        evals_dir = project_root / "evals"
        self.context = ContextBuilder(workspace)
        self.relatedness = RelatednessEvaluator(evals_dir=evals_dir, threshold=relatedness_threshold)
        self.route_decider = RouteDecider(
            provider=provider,
            model=model,
            confidence_threshold=router_confidence_threshold,
        )
        self.search_strategy = SearchStrategyEngine(
            provider=provider,
            model=model,
            debug_dir=workspace / "debug" / "search_candidates",
        )
        self.extractor = CandidateExtractor(
            evals_dir=evals_dir,
            debug_dir=workspace / "debug" / "resume_extract",
        )
        self.pipeline = ProfilePipeline(
            provider=provider,
            model=model,
            extractor=self.extractor,
            profile_store=self.profile_store,
            intent_store=self.intent_store,
        )

    def _provider_chat(self, messages: list[dict[str, Any]]) -> str:
        try:
            return self.provider.chat(messages, model=self.model)
        except ProviderError as exc:
            return f"Provider error: {exc}"

    def _load_resume_skill_text(self) -> str:
        path = self.project_root / "skills" / "resume-sync" / "SKILL.md"
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return ""

    def _load_search_skill_text(self, *, search_kind: str = "jobs") -> str:
        root = self.project_root / "skills" / "search"
        if not root.exists():
            return ""
        selected = [
            root / "SKILL.md",
            root / search_kind / "SKILL.md",
        ]
        parts: list[str] = []
        for path in selected:
            if not path.exists():
                continue
            try:
                txt = path.read_text(encoding="utf-8").strip()
            except Exception:
                txt = ""
            if txt:
                parts.append(f"## {path.relative_to(root)}\n{txt}")
        return "\n\n".join(parts)

    def _load_user_job_skill_text(self) -> str:
        candidates = [
            self.workspace / "skills" / "jobs" / "SKILL.md",
            self.workspace / "jobs" / "SKILL.md",
        ]
        for path in candidates:
            if not path.exists():
                continue
            try:
                txt = path.read_text(encoding="utf-8").strip()
            except Exception:
                txt = ""
            if not txt:
                continue
            try:
                rel = path.relative_to(self.workspace)
                label = f"workspace/{rel.as_posix()}"
            except Exception:
                label = path.name
            return f"## {label}\n{txt}"
        return ""

    def _save_session_state(self, session_id: str, state: dict[str, Any]) -> None:
        if state:
            self.session_manager.update_state(session_id, state)
        else:
            self.session_manager.clear_state(session_id)

    def _default_search_spec(self, user_message: str, persona: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
        return default_search_spec(user_message, persona, intent)

    def _extract_search_spec(
        self,
        *,
        user_message: str,
        persona: dict[str, Any],
        intent: dict[str, Any],
        search_skill_text: str,
    ) -> dict[str, Any]:
        return self.search_strategy.extract_search_spec(
            user_message=user_message,
            persona=persona,
            intent=intent,
            search_skill_text=search_skill_text,
            default_builder=default_search_spec,
        )

    def _summarize_google_company_candidates(
        self,
        *,
        query_id: str,
        search_spec: dict[str, Any],
        all_web_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return self.search_strategy.summarize_google_company_candidates(
            query_id=query_id,
            search_spec=search_spec,
            all_web_items=all_web_items,
        )

    def _format_company_pick_prompt(self, pending: dict[str, Any]) -> str:
        return format_company_pick_prompt(pending)

    def _run_site_searches_parallel(
        self,
        *,
        session_id: str,
        turn_id: str,
        selected_companies: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not selected_companies:
            return []

        def _job(row: dict[str, Any]) -> dict[str, Any]:
            site_name = str(row.get("company") or row.get("site_name") or "")
            base_url = str(row.get("base_url") or "")
            result = self.site_tools.handle_site_request(
                site_name=site_name or "target-site",
                base_url=base_url,
                apply_requested=False,
                session_id=session_id,
                turn_id=turn_id,
                source_type="search_selection",
            )
            return {**row, "site_result": result}

        workers = min(self.site_parallelism, max(1, len(selected_companies)))
        results: list[dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_job, row) for row in selected_companies]
            for fu in concurrent.futures.as_completed(futures):
                try:
                    results.append(fu.result())
                except Exception:
                    continue
        results.sort(key=lambda r: str(r.get("company") or ""))
        return results

    def _minimal_intent_candidate_from_persona(self, persona: dict[str, Any]) -> dict[str, Any]:
        return minimal_intent_candidate_from_persona(persona)

    def _evaluate_jobs_for_apply(
        self,
        *,
        site_name: str,
        jobs: list[dict[str, Any]],
        persona: dict[str, Any],
        intent: dict[str, Any],
    ) -> list[dict[str, Any]]:
        chosen = self.search_strategy.evaluate_jobs_for_apply(
            site_name=site_name,
            jobs=jobs,
            persona=persona,
            intent=intent,
        )
        chosen_ids = {str(j.get("job_id") or "") for j in chosen if isinstance(j, dict)}
        for job in jobs:
            if not isinstance(job, dict):
                continue
            job_id = str(job.get("job_id") or "")
            self.application_store.append_event(
                "fit.evaluated",
                {
                    "site_name": site_name,
                    "job_id": job_id,
                    "title": job.get("title"),
                    "confidence": 0.0,
                    "apply": job_id in chosen_ids,
                    "reason": "strategy_engine",
                },
            )
        return chosen

    def _handle_pending_company_selection(self, session_id: str, message: str, turn_id: str) -> str | None:
        state = self.session_manager.get_state(session_id)
        pending = state.get("pending_company_selection") if isinstance(state, dict) else None
        if not isinstance(pending, dict):
            return None
        candidates = pending.get("candidates") if isinstance(pending.get("candidates"), list) else []
        mode = str(pending.get("mode") or "sequential")
        if mode == "indices":
            query_id = str(pending.get("query_id") or "")
            picked = self.search_flow.parse_company_indices(message, len(candidates))
            if not picked:
                return "请回复要注册的公司序号，例如 `1 3 5`。"
            selected_companies: list[dict[str, Any]] = []
            selected_set = set(picked)
            for idx, row in enumerate(candidates, 1):
                company = str(row.get("company") or "")
                site_id = str(row.get("site_id") or safe_file_stem(company))
                if idx in selected_set:
                    selected_companies.append(row)
                    self.search_store.append_company_decision(
                        query_id=query_id,
                        session_id=session_id,
                        company=company,
                        site_id=site_id,
                        decision="yes",
                        reason_tag="index_selected",
                        metadata={"candidate_index": idx - 1},
                    )
                else:
                    self.search_store.append_company_decision(
                        query_id=query_id,
                        session_id=session_id,
                        company=company,
                        site_id=site_id,
                        decision="no",
                        reason_tag="index_not_selected",
                        metadata={"candidate_index": idx - 1},
                    )
            state.pop("pending_company_selection", None)
            return self.search_flow.finalize_company_selection(
                session_id=session_id,
                turn_id=turn_id,
                query_id=query_id,
                selected_companies=selected_companies,
                state=state,
                run_site_searches_parallel=self._run_site_searches_parallel,
            )

        idx = int(pending.get("index") or 0)
        if idx >= len(candidates):
            state.pop("pending_company_selection", None)
            self._save_session_state(session_id, state)
            return None

        decision, reason_text = parse_yes_no_reason(message)
        if decision == "unknown":
            return "请回复 y 或 n（可附原因，如 `n 不符合公司偏好`）。"

        row = candidates[idx]
        query_id = str(pending.get("query_id") or "")
        company = str(row.get("company") or "")
        site_id = str(row.get("site_id") or safe_file_stem(company))
        self.search_store.append_company_decision(
            query_id=query_id,
            session_id=session_id,
            company=company,
            site_id=site_id,
            decision=decision,
            reason_tag="user_feedback" if decision == "no" and reason_text else "",
            reason_text=reason_text,
            metadata={"candidate_index": idx},
        )

        selected = pending.get("selected") if isinstance(pending.get("selected"), list) else []
        if decision == "yes":
            selected.append(row)
        pending["selected"] = selected
        pending["index"] = idx + 1

        if int(pending["index"]) < len(candidates):
            state["pending_company_selection"] = pending
            self._save_session_state(session_id, state)
            return self._format_company_pick_prompt(pending)

        # Finalize selection and run site searches.
        state.pop("pending_company_selection", None)
        selected_companies = selected
        return self.search_flow.finalize_company_selection(
            session_id=session_id,
            turn_id=turn_id,
            query_id=str(pending.get("query_id") or ""),
            selected_companies=selected_companies,
            state=state,
            run_site_searches_parallel=self._run_site_searches_parallel,
        )

    def _handle_pending_company_apply(self, session_id: str, message: str, turn_id: str) -> str | None:
        state = self.session_manager.get_state(session_id)
        pending = state.get("pending_company_apply") if isinstance(state, dict) else None
        if not isinstance(pending, dict):
            return None
        sites = pending.get("sites") if isinstance(pending.get("sites"), list) else []
        idx = int(pending.get("index") or 0)
        if idx >= len(sites):
            state.pop("pending_company_apply", None)
            self._save_session_state(session_id, state)
            return None

        decision, reason_text = parse_yes_no_reason(message)
        if decision == "unknown":
            return "请回复 y 或 n（公司级确认）。"

        current = sites[idx]
        company = str(current.get("company") or "")
        site_id = str(current.get("site_id") or "")
        jobs = current.get("jobs") if isinstance(current.get("jobs"), list) else []

        applied_count = 0
        skipped_count = 0
        if decision == "yes":
            if not bool(current.get("has_skill")):
                reason = "missing_site_skill"
                skipped_count = len(jobs)
                self.application_store.append_event(
                    "apply.batch_skipped",
                    {"company": company, "site_id": site_id, "reason": reason},
                )
            else:
                persona = self.profile_store.load_doc()
                intent = self.intent_store.load_doc()
                chosen = self._evaluate_jobs_for_apply(
                    site_name=company or site_id,
                    jobs=jobs,
                    persona=persona,
                    intent=intent,
                )
                chosen_ids = {str(j.get("job_id") or "") for j in chosen}
                skipped_count = max(0, len(jobs) - len(chosen_ids))
                result = self.site_tools.apply_now(site_id, chosen, session_id=session_id, turn_id=turn_id)
                applied_rows = result.get("applied") if isinstance(result.get("applied"), list) else []
                for row in applied_rows:
                    submitted = bool(row.get("submitted"))
                    if submitted:
                        applied_count += 1
                    payload = {
                        "job_id": row.get("job_id") or "",
                        "canonical_job_id": row.get("canonical_job_id") or "",
                        "title": row.get("title") or "",
                        "employer": row.get("employer") or company,
                        "site_id": site_id,
                        "discovery_site": row.get("discovery_site") or site_id,
                        "submission_site": row.get("submission_site") or site_id,
                        "decision_scope": "company",
                        "auto_decision": "apply",
                        "submitted": submitted,
                        "submitted_at": row.get("ts") or "",
                        "stage": "applied" if submitted else "apply_failed",
                        "stage_updated_at": row.get("ts") or "",
                        "error": row.get("detail", {}).get("error") if isinstance(row.get("detail"), dict) else "",
                    }
                    self.application_store.append_application(payload)
                self.application_store.append_event(
                    "apply.batch_finished",
                    {
                        "company": company,
                        "site_id": site_id,
                        "attempted_jobs": len(chosen),
                        "submitted_jobs": applied_count,
                        "skipped_jobs": skipped_count,
                    },
                )
        else:
            self.application_store.append_event(
                "apply.batch_user_skipped",
                {
                    "company": company,
                    "site_id": site_id,
                    "reason": reason_text,
                },
            )
            skipped_count = len(jobs)

        pending["index"] = idx + 1
        if int(pending["index"]) < len(sites):
            state["pending_company_apply"] = pending
            self._save_session_state(session_id, state)
            nxt = sites[int(pending["index"])]
            return (
                f"{company} 处理完成：submitted={applied_count}, skipped={skipped_count}。\n"
                f"是否对 {nxt.get('company')} 执行自动投递？（公司级）请回复 y/n。"
            )

        state.pop("pending_company_apply", None)
        self._save_session_state(session_id, state)
        return f"{company} 处理完成：submitted={applied_count}, skipped={skipped_count}。全部公司处理完毕。"

    def _handle_pending_apply(self, session_id: str, message: str) -> str | None:
        state = self.session_manager.get_state(session_id)
        pending = state.get("pending_apply") if isinstance(state, dict) else None
        if not isinstance(pending, dict):
            return None

        if is_yes(message):
            site_id = str(pending.get("site_id") or "")
            jobs = pending.get("jobs") or []
            persona = self.profile_store.load_doc()
            intent = self.intent_store.load_doc()
            chosen = self._evaluate_jobs_for_apply(
                site_name=site_id,
                jobs=[j for j in jobs if isinstance(j, dict)],
                persona=persona,
                intent=intent,
            )
            result = self.site_tools.apply_now(site_id, chosen, session_id=session_id, turn_id=make_id("turn"))
            applied_rows = result.get("applied") if isinstance(result.get("applied"), list) else []
            submitted = 0
            for row in applied_rows:
                ok = bool(row.get("submitted"))
                if ok:
                    submitted += 1
                self.application_store.append_application(
                    {
                        "job_id": row.get("job_id") or "",
                        "canonical_job_id": row.get("canonical_job_id") or "",
                        "title": row.get("title") or "",
                        "employer": row.get("employer") or "",
                        "site_id": site_id,
                        "discovery_site": row.get("discovery_site") or site_id,
                        "submission_site": row.get("submission_site") or site_id,
                        "decision_scope": "company",
                        "auto_decision": "apply",
                        "submitted": ok,
                        "stage": "applied" if ok else "apply_failed",
                        "stage_updated_at": row.get("ts") or "",
                        "error": row.get("detail", {}).get("error") if isinstance(row.get("detail"), dict) else "",
                    }
                )
            state.pop("pending_apply", None)
            self._save_session_state(session_id, state)
            if result.get("ok"):
                return f"已执行投递流程，尝试 {len(applied_rows)} 条，成功提交 {submitted} 条。"
            return f"投递失败：{result.get('error') or 'unknown'}"

        if is_no(message):
            state.pop("pending_apply", None)
            self._save_session_state(session_id, state)
            return "已取消本次投递。"

        return None

    def _handle_pending_intent_confirmation(self, session_id: str, message: str) -> str | None:
        state = self.session_manager.get_state(session_id)
        pending = state.get("pending_intent_patch") if isinstance(state, dict) else None
        if not isinstance(pending, dict):
            return None

        patch = pending.get("patch")
        if not isinstance(patch, dict) or not patch:
            state.pop("pending_intent_patch", None)
            self._save_session_state(session_id, state)
            return None

        event_id = str(pending.get("event_id") or "")
        if is_yes(message):
            self.intent_store.apply_patch(patch, reason=f"resume_intent_confirm:{event_id or 'manual'}")
            if event_id:
                self.intent_store.update_event(event_id, status="applied", metadata={"decision": "yes"})
            state.pop("pending_intent_patch", None)
            self._save_session_state(session_id, state)
            return "已确认并更新 intent.md。"

        if is_no(message):
            if event_id:
                self.intent_store.update_event(event_id, status="user_rejected", metadata={"decision": "no"})
            state.pop("pending_intent_patch", None)
            self._save_session_state(session_id, state)
            return "已保留 intent 候选，不写入文档。"

        return None

    def _record_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        user_message: str,
        assistant_message: str,
        relatedness: dict[str, Any] | None = None,
        extra_run: dict[str, Any] | None = None,
    ) -> None:
        self.session_manager.append_message(session_id, "user", user_message, turn_id=turn_id)
        self.session_manager.append_message(session_id, "assistant", assistant_message, turn_id=turn_id)
        is_profile = bool((relatedness or {}).get("is_profile_related"))
        is_intent = bool((relatedness or {}).get("is_intent_related"))
        self.chat_store.append_message(
            session_id,
            "user",
            user_message,
            is_profile_related=is_profile,
            is_intent_related=is_intent,
            metadata={"relatedness": relatedness or {}, "turn_id": turn_id},
        )
        self.chat_store.append_message(
            session_id,
            "assistant",
            assistant_message,
            is_profile_related=is_profile,
            is_intent_related=is_intent,
            metadata={"turn_id": turn_id},
        )
        payload = {"session_id": session_id, "turn_id": turn_id, "relatedness": relatedness or {}}
        if extra_run:
            payload.update(extra_run)
        self.run_store.append(payload)

    def _handle_search_request(self, session_id: str, message: str, turn_id: str) -> str:
        persona = self.profile_store.load_doc()
        intent = self.intent_store.load_doc()
        search_skill_text = self._load_search_skill_text()
        user_job_skill_text = self._load_user_job_skill_text()
        spec = self._extract_search_spec(
            user_message=message,
            persona=persona,
            intent=intent,
            search_skill_text=(search_skill_text + "\n\n" + user_job_skill_text).strip(),
        )
        query = self.search_store.start_query(
            session_id=session_id,
            turn_id=turn_id,
            user_message=message,
            query_spec=spec,
        )
        query_id = str(query.get("query_id") or "")
        candidates = self.search_strategy.generate_company_candidates(
            user_message=message,
            intent=intent,
            job_skill_text=(search_skill_text + "\n\n" + user_job_skill_text).strip(),
            top_k=self.search_company_top_k,
            query_id=query_id,
        )
        self.search_store.append_company_candidates(query_id=query_id, candidates=candidates)
        if not candidates:
            return "未生成具体公司候选。请补充岗位方向或调整 job skill 后重试。"

        state = self.session_manager.get_state(session_id)
        state["pending_company_selection"] = {
            "query_id": query_id,
            "candidates": candidates,
            "mode": "indices",
        }
        self._save_session_state(session_id, state)
        lines = [
            f"已生成 {len(candidates)} 个公司候选（基于 intent + search skill + job skill）。",
            format_company_index_pick_prompt(candidates),
        ]
        return "\n".join(lines)

    def _interrupt_search_pending_if_needed(self, session_id: str, message: str) -> None:
        state = self.session_manager.get_state(session_id)
        if not isinstance(state, dict) or not state:
            return
        has_search_pending = any(key in state for key in ("pending_company_selection", "pending_company_apply", "pending_apply"))
        if not has_search_pending:
            return
        if not bool(detect_search_request(message).get("is_search_flow")):
            return
        changed = False
        for key in ("pending_company_selection", "pending_company_apply", "pending_apply"):
            if key in state:
                state.pop(key, None)
                changed = True
        if changed:
            self._save_session_state(session_id, state)

    def process_message(self, session_id: str, message: str) -> str:
        turn_id = make_id("turn")
        self._interrupt_search_pending_if_needed(session_id, message)
        pending_reply = self._handle_pending_apply(session_id, message)
        if pending_reply is None:
            pending_reply = self._handle_pending_intent_confirmation(session_id, message)
        if pending_reply is None:
            pending_reply = self._handle_pending_company_selection(session_id, message, turn_id)
        if pending_reply is None:
            pending_reply = self._handle_pending_company_apply(session_id, message, turn_id)
        if pending_reply is not None:
            self._record_turn(
                session_id=session_id,
                turn_id=turn_id,
                user_message=message,
                assistant_message=pending_reply,
                relatedness=None,
                extra_run={"pending_flow": True},
            )
            return pending_reply

        persona = self.profile_store.load_doc()
        intent = self.intent_store.load_doc()
        route_decision = self.route_decider.decide(message=message, persona=persona, intent=intent)
        route_event_id = ""
        if self.router_log_enabled:
            event = self.router_store.append_event(
                {
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "message": message,
                    "llm_route": route_decision.get("llm", {}).get("route", ""),
                    "llm_confidence": route_decision.get("llm", {}).get("confidence", 0.0),
                    "llm_reason_tag": route_decision.get("llm", {}).get("reason_tag", ""),
                    "llm_params": route_decision.get("llm", {}).get("params", {}),
                    "fallback_route": route_decision.get("fallback", {}).get("route", ""),
                    "fallback_reason_tag": route_decision.get("fallback", {}).get("reason_tag", ""),
                    "fallback_params": route_decision.get("fallback", {}).get("params", {}),
                    "fallback_used": bool(route_decision.get("fallback_used")),
                    "threshold": route_decision.get("threshold", 0.0),
                    "decision_source": route_decision.get("decision_source", ""),
                    "final_route": route_decision.get("final_route", ""),
                    "final_params": route_decision.get("final_params", {}),
                }
            )
            route_event_id = str(event.get("route_event_id") or "")

        final_route = str(route_decision.get("final_route") or "chat")
        if final_route == "search":
            reply = self._handle_search_request(session_id, message, turn_id)
            self._record_turn(
                session_id=session_id,
                turn_id=turn_id,
                user_message=message,
                assistant_message=reply,
                relatedness=None,
                extra_run={
                    "search_flow": True,
                    "route_event_id": route_event_id,
                    "route_source": route_decision.get("decision_source", ""),
                    "route_confidence": route_decision.get("llm", {}).get("confidence", 0.0),
                    "route_fallback_used": bool(route_decision.get("fallback_used")),
                },
            )
            return reply

        related = self.relatedness.evaluate(
            provider=self.provider,
            model=self.model,
            message=message,
            persona=persona,
            intent=intent,
        )

        session_history = self.session_manager.get_recent_messages(session_id, limit=self.max_history_messages)
        profile_hist = self.chat_store.recent_related(session_id, "profile", self.related_history_k)
        intent_hist = self.chat_store.recent_related(session_id, "intent", self.related_history_k)
        llm_messages = self.context.build_messages(
            session_history=session_history,
            user_message=message,
            persona=persona,
            intent=intent,
            relatedness=related,
            profile_related_history=profile_hist,
            intent_related_history=intent_hist,
        )
        assistant = self._provider_chat(llm_messages)

        site_hint = ""
        req = {
            "is_site_flow": final_route == "site",
            "company": "",
            "base_url": "",
            "apply_requested": False,
        }
        if final_route == "site":
            params = route_decision.get("final_params") if isinstance(route_decision.get("final_params"), dict) else {}
            req = {
                "is_site_flow": True,
                "company": str(params.get("company") or "target-site"),
                "base_url": str(params.get("base_url") or ""),
                "apply_requested": bool(params.get("apply_requested")),
            }
            site_result = self.site_tools.handle_site_request(
                site_name=str(req.get("company") or "target-site"),
                base_url=str(req.get("base_url") or ""),
                apply_requested=bool(req.get("apply_requested")),
                session_id=session_id,
                turn_id=turn_id,
                source_type="site_request",
            )
            site_hint = self._format_site_result(site_result, session_id)

        message_id = make_id("msg")
        pipeline_info = self.pipeline.process_message(
            message_id=message_id,
            session_id=session_id,
            message=message,
            relatedness=related,
        )

        final_reply = assistant
        if site_hint:
            final_reply = (assistant + "\n\n" + site_hint).strip()

        reports = pipeline_info.get("new_reports") or []
        if reports:
            ids = ", ".join(str(r.get("id")) for r in reports)
            final_reply += f"\n\n检测到新报告已生成：{ids}。请运行 `careereng report review --id <report_id>` 进行审核。"
        self._record_turn(
            session_id=session_id,
            turn_id=turn_id,
            user_message=message,
            assistant_message=final_reply,
            relatedness=related,
            extra_run={
                "site_flow": bool(req.get("is_site_flow")),
                "route_event_id": route_event_id,
                "route_source": route_decision.get("decision_source", ""),
                "route_confidence": route_decision.get("llm", {}).get("confidence", 0.0),
                "route_fallback_used": bool(route_decision.get("fallback_used")),
                "final_route": final_route,
            },
        )
        return final_reply

    def _format_site_result(self, site_result: dict[str, Any], session_id: str) -> str:
        return format_site_result_text(site_result)

    def process_resume_upload(self, session_id: str, text: str, source_name: str) -> str:
        skill_text = self._load_resume_skill_text()
        profile_patch = self.extractor.extract_profile_patch(
            self.provider,
            self.model,
            text,
            skill_text=skill_text,
            use_few_shot=False,
            debug_label=f"resume_profile:{source_name}",
        )
        if profile_patch:
            self.profile_store.apply_patch(profile_patch, reason=f"resume_upload:{source_name}")
            self.profile_store.append_event(
                {
                    "name": "profile.resume_upload",
                    "message": f"resume source: {source_name}",
                    "session_id": session_id,
                    "related": True,
                    "confidence": 1.0,
                    "reason": "resume upload",
                    "patch": profile_patch,
                    "status": "applied",
                    "few_shot_version": "disabled",
                    "evaluator_version": "resume_sync_skill.v1",
                    "metadata": {"skill": "resume-sync"},
                }
            )

        persona = self.profile_store.load_doc()
        intent_patch = self.extractor.extract_resume_intent_patch(
            self.provider,
            self.model,
            resume_text=text,
            persona=persona,
            skill_text=skill_text,
            use_few_shot=False,
            debug_label=f"resume_intent:{source_name}",
        )
        intent_event = None
        intent_mode = "primary"
        intent_reason = "resume sync intent candidate"
        intent_confidence = 0.9
        if not intent_patch and profile_patch:
            intent_mode = "persona_fallback"
            intent_reason = "resume sync persona fallback"
            intent_confidence = 0.8
            intent_patch = self.extractor.extract_resume_intent_patch(
                self.provider,
                self.model,
                resume_text=json.dumps({"persona": persona, "source": "persona_fallback"}, ensure_ascii=False),
                persona=persona,
                skill_text=skill_text,
                use_few_shot=False,
                debug_label=f"resume_intent_fallback:{source_name}",
            )
        if not intent_patch and profile_patch:
            intent_mode = "deterministic_fallback"
            intent_reason = "resume sync deterministic fallback"
            intent_confidence = 0.6
            intent_patch = self._minimal_intent_candidate_from_persona(persona)
        if intent_patch:
            intent_event = self.intent_store.append_event(
                {
                    "name": "intent.resume_candidate",
                    "message": f"resume source: {source_name}",
                    "session_id": session_id,
                    "related": True,
                    "confidence": intent_confidence,
                    "reason": intent_reason,
                    "patch": intent_patch,
                    "status": "pending_user_confirm",
                    "few_shot_version": "disabled",
                    "evaluator_version": "resume_sync_skill.v1",
                    "metadata": {"skill": "resume-sync", "mode": intent_mode},
                }
            )
            state = self.session_manager.get_state(session_id)
            state["pending_intent_patch"] = {
                "event_id": intent_event.get("id", ""),
                "patch": intent_patch,
                "source": source_name,
            }
            self._save_session_state(session_id, state)

        if not profile_patch and not intent_patch:
            return "未从简历中提取到可更新字段。"

        lines: list[str] = []
        if skill_text.strip():
            lines.append("已加载 skills/resume-sync/SKILL.md 进行简历解析。")
        if profile_patch:
            lines.append(f"已根据简历更新 persona.md。patch={json.dumps(profile_patch, ensure_ascii=False)}")
        if intent_patch:
            if intent_mode != "primary":
                lines.append("未直接提取到 intent，已基于更新后的 persona 生成保守 intent 候选。")
            lines.append(f"生成 intent 候选更新：patch={json.dumps(intent_patch, ensure_ascii=False)}")
            lines.append("是否写入 intent.md？请回复 y/n。")
        return "\n".join(lines)
