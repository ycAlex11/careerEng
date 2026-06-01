"""Local rule-based processor adapter for assistant bridge v1."""

from __future__ import annotations

import re
import shlex
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

        interview_command = cls._interview_candidates_command(text, entities=entities)
        if interview_command:
            command, interview_entities = interview_command
            return cls._decision(
                DATA_CATEGORY_CAREERENG_COMMAND,
                0.88,
                route="interview_prep",
                labels=["interview_candidates", "interview_binding"],
                entities=interview_entities,
                action="interview_candidates",
                command=command,
                reason="message requests interview preparation or recording; local candidate confirmation is required first",
            )

        ad_hoc_interview = cls._ad_hoc_interview_command(text)
        if ad_hoc_interview:
            return cls._decision(
                DATA_CATEGORY_CAREERENG_COMMAND,
                0.86,
                route="interview_record",
                labels=["interview_ad_hoc", "interview_assist"],
                entities=entities,
                action="interview_create_ad_hoc",
                command=ad_hoc_interview,
                reason="message requests starting interview assist without enough company/job context",
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

        if (
            cls._contains_any(text, ("检索投递", "开始投递", "投递已注册", "jobs apply", "retrieve and apply"))
            or (cls._contains_any(text, ("投递", "apply")) and cls._contains_any(text, ("已注册", "已激活", "激活的网站", "active sites", "registered sites")))
        ) and not cls._contains_any(text, ("建议", "有什么建议", "需要做什么", "怎么准备")):
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

        bootstrap = cls._site_bootstrap_command(text, entities=entities)
        if bootstrap:
            command, bootstrap_entities = bootstrap
            return cls._decision(
                DATA_CATEGORY_CAREERENG_COMMAND,
                0.84,
                route="site",
                labels=["site_bootstrap", "new_site"],
                entities=bootstrap_entities,
                action="site_bootstrap",
                command=command,
                reason="message requests preparing a target company site through bootstrap",
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
    def _interview_candidates_command(cls, text: str, *, entities: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        if not cls._contains_any(text, ("面试", "interview")):
            return None
        if not cls._contains_any(text, ("准备", "记录", "开始", "prep", "prepare", "record")):
            return None
        company = str(entities.get("company") or "").strip()
        title = cls._extract_interview_title(text, company=company)
        roles = entities.get("roles") if isinstance(entities.get("roles"), list) else []
        if not title and roles:
            title = " ".join(str(role) for role in roles if str(role).strip())
        if not company and not title:
            return None
        command = "python -m careereng interview candidates"
        if company:
            command += f" --company {shlex.quote(company)}"
        if title:
            command += f" --title {shlex.quote(title)}"
        next_entities = dict(entities)
        if company:
            next_entities["company"] = company
        if title:
            next_entities["interview_title_query"] = title
        return command, next_entities

    @classmethod
    def _ad_hoc_interview_command(cls, text: str) -> str:
        if not cls._contains_any(text, ("面试", "interview")):
            return ""
        if not cls._contains_any(text, ("辅助", "开启", "开始", "记录", "assist", "start", "record")):
            return ""
        return "python -m careereng interview create --company unknown --title unknown --created-reason ad_hoc_assist"

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
        if not company:
            company = cls._extract_bootstrap_company(text)
            if company:
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

    @classmethod
    def _site_bootstrap_command(cls, text: str, *, entities: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        if not cls._looks_like_site_bootstrap(text):
            return None
        company = str(entities.get("company") or "").strip() or cls._extract_bootstrap_company(text)
        if not company:
            return None
        url = cls._extract_url(text)
        command = f"python -m careereng site bootstrap {shlex.quote(company)}"
        if url:
            command += f" --url {shlex.quote(url)}"
        next_entities = dict(entities)
        next_entities["company"] = company
        next_entities["site_key"] = str(next_entities.get("site_key") or safe_file_stem(company))
        if url:
            next_entities["url"] = url
        return command, next_entities

    @classmethod
    def _looks_like_site_bootstrap(cls, text: str) -> bool:
        lowered = text.lower()
        if cls._contains_any(text, ("建议", "有什么建议", "需要做什么", "怎么准备", "需要补什么", "还需要做什么", "怎么办")):
            return False
        if cls._contains_any(
            text,
            (
                "投递已注册",
                "检索投递",
                "开始投递",
                "jobs apply",
                "retrieve and apply",
                "检查投递",
                "投递状态",
                "申请状态",
                "review-status",
                "review status",
                "总结投递",
                "投递总结",
                "已注册",
                "已激活",
                "激活的网站",
                "active sites",
                "registered sites",
                "registered companies",
            ),
        ):
            return False
        if cls._contains_any(text, ("添加", "新增", "注册", "add", "register", "site bootstrap", "bootstrap")) and cls._extract_bootstrap_company(text):
            return True
        if cls._contains_any(text, ("想投", "帮我投", "投 ", "投", "apply to", "apply for")):
            company = cls._extract_bootstrap_company(text)
            return bool(company and company.lower() not in {"已注册的公司", "registered sites", "registered companies"})
        return bool(("http://" in lowered or "https://" in lowered) and cls._contains_any(text, ("添加", "新增", "注册", "add", "register")))

    @staticmethod
    def _extract_url(text: str) -> str:
        match = re.search(r"https?://[^\s]+", text)
        return match.group(0).rstrip("，,。.;；)") if match else ""

    @classmethod
    def _extract_interview_title(cls, text: str, *, company: str) -> str:
        cleaned = str(text or "").strip()
        cleaned = re.sub(r"^@career\s*", "", cleaned, flags=re.I).strip()
        cleaned = re.sub(r"(请|帮我|一下|这个岗位|的面试|面试|interview|准备|记录|开始记录|开始|prep|prepare|record)", " ", cleaned, flags=re.I)
        if company:
            cleaned = re.sub(re.escape(company), " ", cleaned, flags=re.I)
        for alias in SITE_ALIASES:
            cleaned = re.sub(re.escape(alias), " ", cleaned, flags=re.I)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_，,。.;；:：")
        if len(cleaned) > 80:
            cleaned = cleaned[:80].strip()
        return cleaned

    @classmethod
    def _extract_bootstrap_company(cls, text: str) -> str:
        cleaned = str(text or "").strip()
        cleaned = re.sub(r"^@career\s*", "", cleaned, flags=re.I).strip()
        url = cls._extract_url(cleaned)
        if url:
            cleaned = cleaned.replace(url, " ").strip()

        patterns = (
            r"(?:帮我)?(?:投|投递|想投)\s*([\w\u4e00-\u9fff&.\- ]{2,50})",
            r"(?:添加|新增|注册)\s*([\w\u4e00-\u9fff&.\- ]{2,50}?)(?:\s*(?:网站|站点|site|career site))?$",
            r"(?:apply\s+(?:to|for)\s+)([A-Za-z0-9&.\- ]{2,50})",
            r"(?:(?:add|register)\s+)([A-Za-z0-9&.\- ]{2,50}?)(?:\s+(?:site|career site|careers site))?$",
            r"(?:site\s+bootstrap\s+)([A-Za-z0-9&.\- ]{2,50})",
        )
        for pattern in patterns:
            match = re.search(pattern, cleaned, flags=re.I)
            if not match:
                continue
            company = cls._clean_company_candidate(match.group(1))
            if company:
                return company
        return ""

    @staticmethod
    def _clean_company_candidate(value: str) -> str:
        text = str(value or "").strip()
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"(网站|站点|官网|career site|careers site|site)$", "", text, flags=re.I).strip()
        text = text.strip(" -_，,。.;；:：")
        blocked = {
            "已注册的公司",
            "已注册公司",
            "registered sites",
            "registered companies",
            "all registered sites",
            "公司",
            "网站",
        }
        if not text or text.lower() in blocked:
            return ""
        return text
