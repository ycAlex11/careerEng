"""Main agent loop for `careereng run`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from careereng.agent.context import ContextBuilder
from careereng.agent.extractor import CandidateExtractor
from careereng.agent.profile_pipeline import ProfilePipeline
from careereng.agent.relatedness import RelatednessEvaluator
from careereng.agent.router import detect_site_request, is_no, is_yes
from careereng.providers.base import LLMProvider, ProviderError
from careereng.session.manager import SessionManager
from careereng.storage.chat_store import ChatStore
from careereng.storage.intent_store import IntentStore
from careereng.storage.profile_store import ProfileStore
from careereng.storage.run_store import RunStore
from careereng.tools.site_tools import SiteTools
from careereng.utils import make_id


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
        site_tools: SiteTools,
    ):
        self.workspace = workspace
        self.project_root = project_root
        self.provider = provider
        self.model = model
        self.max_history_messages = max_history_messages
        self.related_history_k = related_history_k

        self.session_manager = SessionManager(workspace)
        self.chat_store = ChatStore(workspace)
        self.profile_store = ProfileStore(workspace)
        self.intent_store = IntentStore(workspace)
        self.run_store = RunStore(workspace)
        self.site_tools = site_tools

        evals_dir = project_root / "evals"
        self.context = ContextBuilder(workspace)
        self.relatedness = RelatednessEvaluator(evals_dir=evals_dir, threshold=relatedness_threshold)
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

    def _save_session_state(self, session_id: str, state: dict[str, Any]) -> None:
        if state:
            self.session_manager.update_state(session_id, state)
        else:
            self.session_manager.clear_state(session_id)

    def _minimal_intent_candidate_from_persona(self, persona: dict[str, Any]) -> dict[str, Any]:
        roles: list[str] = []
        skills = persona.get("skills") if isinstance(persona.get("skills"), dict) else {}
        ai_skills = skills.get("ai") if isinstance(skills.get("ai"), list) else []
        programming = skills.get("programming") if isinstance(skills.get("programming"), list) else []
        experience = persona.get("experience") if isinstance(persona.get("experience"), list) else []
        projects = persona.get("projects") if isinstance(persona.get("projects"), list) else []

        if ai_skills:
            roles.append("AI Engineer")
        if programming:
            roles.append("Software Engineer")
        if experience:
            roles.append("Backend Engineer")
        if projects and not roles:
            roles.append("Software Engineer")
        if not roles:
            roles.append("Software Engineer")

        dedup_roles: list[str] = []
        for role in roles:
            role = str(role).strip()
            if role and role not in dedup_roles:
                dedup_roles.append(role)

        locations: list[str] = []
        basic = persona.get("basic") if isinstance(persona.get("basic"), dict) else {}
        current_city = str(basic.get("current_city") or "").strip()
        if current_city:
            locations.append(current_city)

        constraints = persona.get("constraints") if isinstance(persona.get("constraints"), dict) else {}
        work_auth = str(constraints.get("work_auth") or "").strip().lower()
        if work_auth == "china":
            locations.append("China")

        dedup_locations: list[str] = []
        for loc in locations:
            loc = str(loc).strip()
            if loc and loc not in dedup_locations:
                dedup_locations.append(loc)

        patch: dict[str, Any] = {"target_roles": dedup_roles[:3]}
        if dedup_locations:
            patch["target_locations"] = dedup_locations[:6]
        return patch

    def _handle_pending_apply(self, session_id: str, message: str) -> str | None:
        state = self.session_manager.get_state(session_id)
        pending = state.get("pending_apply") if isinstance(state, dict) else None
        if not isinstance(pending, dict):
            return None

        if is_yes(message):
            site_id = str(pending.get("site_id") or "")
            jobs = pending.get("jobs") or []
            result = self.site_tools.apply_now(site_id, jobs, session_id=session_id, turn_id=make_id("turn"))
            state.pop("pending_apply", None)
            self._save_session_state(session_id, state)
            if result.get("ok"):
                return f"已执行投递流程，处理 {len(result.get('applied') or [])} 条职位。"
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

    def process_message(self, session_id: str, message: str) -> str:
        turn_id = make_id("turn")
        pending_reply = self._handle_pending_apply(session_id, message)
        if pending_reply is None:
            pending_reply = self._handle_pending_intent_confirmation(session_id, message)
        if pending_reply is not None:
            self.session_manager.append_message(session_id, "user", message, turn_id=turn_id)
            self.session_manager.append_message(session_id, "assistant", pending_reply, turn_id=turn_id)
            self.chat_store.append_message(session_id, "user", message)
            self.chat_store.append_message(session_id, "assistant", pending_reply)
            return pending_reply

        persona = self.profile_store.load_doc()
        intent = self.intent_store.load_doc()

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
        req = detect_site_request(message)
        if req.get("is_site_flow"):
            site_result = self.site_tools.handle_site_request(
                site_name=str(req.get("company") or "target-site"),
                base_url=str(req.get("base_url") or ""),
                apply_requested=bool(req.get("apply_requested")),
                session_id=session_id,
                turn_id=turn_id,
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

        self.session_manager.append_message(session_id, "user", message, turn_id=turn_id)
        self.session_manager.append_message(session_id, "assistant", final_reply, turn_id=turn_id)
        self.chat_store.append_message(
            session_id,
            "user",
            message,
            is_profile_related=bool(related.get("is_profile_related")),
            is_intent_related=bool(related.get("is_intent_related")),
            metadata={"relatedness": related, "turn_id": turn_id},
        )
        self.chat_store.append_message(
            session_id,
            "assistant",
            final_reply,
            is_profile_related=bool(related.get("is_profile_related")),
            is_intent_related=bool(related.get("is_intent_related")),
            metadata={"turn_id": turn_id},
        )
        self.run_store.append(
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "relatedness": related,
                "site_flow": bool(req.get("is_site_flow")),
            }
        )
        return final_reply

    def _format_site_result(self, site_result: dict[str, Any], session_id: str) -> str:
        lines = [
            f"[站点] {site_result.get('site_name')} ({site_result.get('site_id')})",
            f"检索职位数: {site_result.get('jobs_count')}",
        ]
        if site_result.get("search_error"):
            lines.append(f"检索告警: {site_result.get('search_error')}")

        if site_result.get("search_only_no_skill"):
            lines.append("未检测到该公司 skill，仅完成检索，不执行投递。")
        elif site_result.get("await_apply_confirmation"):
            state = self.session_manager.get_state(session_id)
            state["pending_apply"] = {
                "site_id": site_result.get("site_id"),
                "jobs": site_result.get("jobs", [])[:3],
            }
            self.session_manager.update_state(session_id, state)
            lines.append("检测到可投递 skill。是否立即投递？请回复 y/n。")

        return "\n".join(lines)

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
