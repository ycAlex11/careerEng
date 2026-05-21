"""Local rule-based processor adapter for assistant bridge v1."""

from __future__ import annotations

import re
from typing import Any

from careereng.integrations.assistant_bridge.schema import (
    AssistantBridgeDecision,
    DATA_CATEGORY_APPLICATION_FEEDBACK,
    DATA_CATEGORY_CAREER_INTENT_STRATEGY,
    DATA_CATEGORY_CAREERENG_COMMAND,
    DATA_CATEGORY_CHAT,
    DATA_CATEGORY_CORRECTION,
    DATA_CATEGORY_INTERVIEW_RECORD,
    DATA_CATEGORY_PROFILE_RESUME_SIGNAL,
)
from careereng.utils import safe_file_stem


SITE_ALIASES = {
    "amd": "amd",
    "advanced micro devices": "amd",
    "高通": "qualcomm",
    "qualcomm": "qualcomm",
    "英伟达": "nvidia",
    "nvidia": "nvidia",
    "微软": "microsoft",
    "microsoft": "microsoft",
}


class LocalProcessorAdapter:
    backend = "local"
    version = "v1"

    def classify(self, *, message: str, context: dict[str, Any]) -> AssistantBridgeDecision:
        raw = str(message or "").strip()
        lowered = raw.lower()
        if not raw:
            return self._decision(DATA_CATEGORY_CHAT, 0.0, reason="empty_message")

        if self._looks_like_correction(raw):
            return self._decision(
                DATA_CATEGORY_CORRECTION,
                0.86,
                route="correction",
                labels=["routing_correction"],
                action="record_correction",
                reason="message looks like user correction or route/action correction",
            )

        command = self._command_decision(raw)
        if command is not None:
            return command

        if self._looks_like_interview(raw):
            return self._decision(
                DATA_CATEGORY_INTERVIEW_RECORD,
                0.82,
                route="interview_prep",
                labels=["interview_prep"],
                entities=self._entities(raw),
                reason="message discusses interview preparation or interview records",
            )

        if self._looks_like_profile_resume(raw):
            return self._decision(
                DATA_CATEGORY_PROFILE_RESUME_SIGNAL,
                0.78,
                route="career_memory",
                labels=["profile_resume"],
                entities=self._entities(raw),
                reason="message contains resume/profile/capability signal",
            )

        if self._looks_like_application_feedback(raw):
            return self._decision(
                DATA_CATEGORY_APPLICATION_FEEDBACK,
                0.78,
                route="application_feedback",
                labels=["application_feedback"],
                entities=self._entities(raw),
                reason="message discusses application outcomes or feedback",
            )

        if self._looks_like_career_intent(raw):
            return self._decision(
                DATA_CATEGORY_CAREER_INTENT_STRATEGY,
                0.76,
                route="career_memory",
                labels=["career_strategy"],
                entities=self._entities(raw),
                reason="message discusses career direction, target role, or preparation strategy",
            )

        inherited_scope = bool(context.get("inherited_scope"))
        if inherited_scope:
            active_category = str(context.get("active_category") or DATA_CATEGORY_CAREER_INTENT_STRATEGY)
            category = active_category if active_category != DATA_CATEGORY_CHAT else DATA_CATEGORY_CAREER_INTENT_STRATEGY
            return self._decision(
                category,
                0.62,
                route="career_memory",
                labels=["scope_followup"],
                entities=self._entities(raw),
                reason="message inherited an active assistant bridge career scope",
            )

        return self._decision(DATA_CATEGORY_CHAT, 0.2, reason="no career-related signal detected")

    @classmethod
    def _decision(
        cls,
        category: str,
        confidence: float,
        *,
        route: str = "",
        labels: list[str] | None = None,
        entities: dict[str, Any] | None = None,
        action: str = "",
        command: str = "",
        reason: str = "",
    ) -> AssistantBridgeDecision:
        return AssistantBridgeDecision(
            data_category=category,
            route=route or ("chat" if category == DATA_CATEGORY_CHAT else category),
            confidence=confidence,
            semantic_labels=list(labels or []),
            detected_entities=dict(entities or {}),
            suggested_action=action,
            suggested_command=command,
            should_save=category != DATA_CATEGORY_CHAT,
            should_execute=False,
            reason=reason,
            processor_backend=cls.backend,
            processor_version=cls.version,
            processor_trace={"adapter": cls.backend, "version": cls.version},
        )

    @staticmethod
    def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
        lowered = text.lower()
        return any(term in text or term in lowered for term in terms)

    @classmethod
    def _looks_like_correction(cls, text: str) -> bool:
        return cls._contains_any(
            text,
            (
                "不是",
                "不对",
                "错了",
                "纠正",
                "correction",
                "wrong",
                "route wrong",
                "调错",
                "不是要",
                "不是看dashboard",
                "不是看 dashboard",
            ),
        )

    @classmethod
    def _looks_like_interview(cls, text: str) -> bool:
        return cls._contains_any(
            text,
            (
                "面试",
                "interview",
                "teams",
                "面试官",
                "面经",
                "mock interview",
                "准备面试",
            ),
        )

    @classmethod
    def _looks_like_profile_resume(cls, text: str) -> bool:
        return cls._contains_any(
            text,
            (
                "简历",
                "resume",
                "cv",
                "persona",
                "用户画像",
                "项目经历",
                "项目进展",
                "证书",
                "开源",
                "技能",
                "掌握了",
                "做了一个项目",
            ),
        )

    @classmethod
    def _looks_like_application_feedback(cls, text: str) -> bool:
        return cls._contains_any(
            text,
            (
                "被拒",
                "拒了",
                "没过",
                "rejected",
                "declined",
                "in process",
                "application in review",
                "application received",
                "过筛",
                "通过率",
                "投递反馈",
                "投递结果",
                "状态变化",
            ),
        )

    @classmethod
    def _looks_like_career_intent(cls, text: str) -> bool:
        return cls._contains_any(
            text,
            (
                "求职意向",
                "目标岗位",
                "目标公司",
                "ai infra",
                "infra",
                "平台",
                "架构",
                "学习计划",
                "还需要做什么",
                "需要补什么",
                "职业规划",
                "想投",
                "想找",
                "找一些",
                "岗位建议",
                "career strategy",
                "target role",
            ),
        )

    @classmethod
    def _command_decision(cls, text: str) -> AssistantBridgeDecision | None:
        lowered = text.lower()
        entities = cls._entities(text)
        site_key = str(entities.get("site_key") or "")

        if cls._contains_any(text, ("结束", "end scope", "结束上下文", "@career end", "career end")):
            return cls._decision(
                DATA_CATEGORY_CAREERENG_COMMAND,
                0.95,
                route="assistant_scope",
                labels=["assistant_scope"],
                entities=entities,
                action="assistant_scope_end",
                reason="message requests closing assistant bridge scope",
            )

        if cls._contains_any(text, ("检查投递状态", "查看投递状态", "投递状态", "申请状态", "review-status", "review status")) or (
            cls._contains_any(text, ("查看", "检查", "看看", "看一下")) and ("投递情况" in text or "投递" in text)
        ):
            return cls._decision(
                DATA_CATEGORY_CAREERENG_COMMAND,
                0.92,
                route="jobs_batch",
                labels=["application_status"],
                entities=entities,
                action="jobs_review_status",
                command="python -m careereng jobs review-status",
                reason="message requests application status review",
            )

        if cls._contains_any(text, ("检索投递", "开始投递", "投递已注册", "jobs apply", "retrieve and apply")) and not cls._contains_any(
            text, ("建议", "有什么建议", "需要做什么", "怎么准备")
        ):
            return cls._decision(
                DATA_CATEGORY_CAREERENG_COMMAND,
                0.9,
                route="jobs_batch",
                labels=["job_search_apply"],
                entities=entities,
                action="jobs_apply",
                command="python -m careereng jobs apply",
                reason="message requests registered-site retrieval/apply",
            )

        if cls._contains_any(text, ("总结投递", "投递总结", "application summary", "总结一下投递")) or (
            "总结" in text and "投递情况" in text
        ):
            return cls._decision(
                DATA_CATEGORY_CAREERENG_COMMAND,
                0.88,
                route="application_summary",
                labels=["application_summary"],
                entities=entities,
                action="application_summary_build",
                command="python -m careereng application-summary build",
                reason="message requests application summary",
            )

        if cls._contains_any(text, ("metrics", "性能", "token", "耗时", "消耗")):
            return cls._decision(
                DATA_CATEGORY_CAREERENG_COMMAND,
                0.84,
                route="metrics",
                labels=["metrics"],
                entities=entities,
                action="metrics_summary",
                command="python -m careereng metrics summary",
                reason="message requests runtime metrics",
            )

        if cls._contains_any(text, ("停止批次", "停止后台", "清理残留", "batch-stop", "batch stop")):
            return cls._decision(
                DATA_CATEGORY_CAREERENG_COMMAND,
                0.9,
                route="batch",
                labels=["batch_control"],
                entities=entities,
                action="batch_stop",
                command="python -m careereng batch-stop",
                reason="message requests stopping active batch/browser processes",
            )

        if cls._contains_any(text, ("修复 unmatched", "repair-history", "修复脏数据", "清理 history")):
            return cls._decision(
                DATA_CATEGORY_CAREERENG_COMMAND,
                0.86,
                route="application_summary",
                labels=["history_repair"],
                entities=entities,
                action="application_summary_repair_history",
                command="python -m careereng application-summary repair-history",
                reason="message requests safe history repair planning",
            )

        if cls._contains_any(text, ("激活", "activate")) and site_key:
            site_keys = [str(item).strip() for item in entities.get("site_keys") or [site_key] if str(item).strip()]
            command = " && ".join(f"python -m careereng site activate {key}" for key in site_keys)
            return cls._decision(
                DATA_CATEGORY_CAREERENG_COMMAND,
                0.88,
                route="site",
                labels=["site_activate"],
                entities=entities,
                action="site_activate",
                command=command,
                reason="message requests site activation",
            )

        if cls._contains_any(text, ("停用", "deactivate", "不要投")) and site_key:
            site_keys = [str(item).strip() for item in entities.get("site_keys") or [site_key] if str(item).strip()]
            command = " && ".join(f"python -m careereng site deactivate {key}" for key in site_keys)
            return cls._decision(
                DATA_CATEGORY_CAREERENG_COMMAND,
                0.88,
                route="site",
                labels=["site_deactivate"],
                entities=entities,
                action="site_deactivate",
                command=command,
                reason="message requests site deactivation",
            )

        if cls._contains_any(text, ("查看网站", "注册网站", "site list", "网站列表", "已注册网站")):
            return cls._decision(
                DATA_CATEGORY_CAREERENG_COMMAND,
                0.82,
                route="site",
                labels=["site_list"],
                entities=entities,
                action="site_list",
                command="python -m careereng site list",
                reason="message requests registered site listing",
            )

        if "http://" in lowered or "https://" in lowered:
            match = re.search(r"https?://[^\s]+", text)
            url = match.group(0) if match else ""
            if cls._contains_any(text, ("注册", "添加", "add site", "site add")):
                company = str(entities.get("company") or "target-site")
                return cls._decision(
                    DATA_CATEGORY_CAREERENG_COMMAND,
                    0.84,
                    route="site",
                    labels=["site_register"],
                    entities={**entities, "url": url},
                    action="site_add",
                    command=f'python -m careereng site add "{company}" --url {url}',
                    reason="message requests adding a career site",
                )
        return None

    @classmethod
    def _entities(cls, text: str) -> dict[str, Any]:
        lowered = text.lower()
        site_key = ""
        company = ""
        site_keys: list[str] = []
        for alias, key in SITE_ALIASES.items():
            if alias in text or alias in lowered:
                if key not in site_keys:
                    site_keys.append(key)
                if not site_key:
                    site_key = key
                    company = alias
        if not site_key:
            company_match = re.search(r"(?:公司|company)\s*[:：]?\s*([\w\u4e00-\u9fff&.\- ]{2,40})", text, flags=re.I)
            if company_match:
                company = company_match.group(1).strip()
                site_key = safe_file_stem(company)
        roles: list[str] = []
        for role in ("ai infra", "software engineer", "sdet", "ai架构", "架构师", "后端", "平台"):
            if role in lowered or role in text:
                roles.append(role)
        skills: list[str] = []
        for skill in ("cuda", "kubernetes", "distributed systems", "llm", "agent", "codex", "claude code"):
            if skill in lowered:
                skills.append(skill)
        entities: dict[str, Any] = {}
        if company:
            entities["company"] = company
        if site_key:
            entities["site_key"] = site_key
        if site_keys:
            entities["site_keys"] = site_keys
        if roles:
            entities["roles"] = roles
        if skills:
            entities["skills"] = skills
        return entities
