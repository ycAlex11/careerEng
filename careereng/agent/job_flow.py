"""Batch retrieve/apply orchestration for registered sites."""

from __future__ import annotations

import concurrent.futures
import re
import threading
import time
from pathlib import Path
from typing import Any

from careereng.config.schema import BrowserBudgetsConfig
from careereng.evolution.apply_probe import apply_probe_counters
from careereng.evolution.capabilities import EvolutionCapabilityStore
from careereng.evolution.reports import create_apply_probe_report
from careereng.reporting.job_report import generate_job_batch_report
from careereng.storage.application_store import ApplicationStore
from careereng.storage.job_store import JobStore
from careereng.storage.job_planning import JobPlanningStore
from careereng.tools.site_tools import SiteTools


class JobFlow:
    ENABLE_BROWSER_APPLY_PHASE = True
    DISCOVERY_PHASES = (
        "session_preparation",
        "application_status_review",
        "channel_discovery",
        "job_filtering",
        "job_retrieval",
    )
    OPERATION_APPLICATION_STATUS_REVIEW = "application_status_review"
    OPERATION_JOB_SEARCH = "job_search"
    PHASE_PLANS = {
        OPERATION_APPLICATION_STATUS_REVIEW: ("session_preparation", "application_status_review"),
        OPERATION_JOB_SEARCH: DISCOVERY_PHASES,
    }
    AUTH_RECOVERY_PHASE = "session_preparation"
    AUTH_RECOVERY_MARKERS = (
        "auth_required",
        "not authenticated",
        "unauthenticated",
        "not signed in",
        "session expired",
        "sign in required",
        "signin required",
        "login required",
        "log in required",
        "requires sign-in",
        "requires signin",
        "requires login",
        "requires human credential",
        "credential entry",
        "login page",
        "sign-in page",
        "signin page",
        "returning user login",
        "需要登录",
        "未登录",
        "登录页",
    )

    def __init__(
        self,
        *,
        project_root: Path,
        job_store: JobStore,
        application_store: ApplicationStore,
        site_tools: SiteTools,
        browser_runner: Any | None,
        search_strategy: Any,
        profile_store: Any,
        cv_store: Any,
        intent_store: Any,
        site_parallelism: int = 2,
        browser_budgets: BrowserBudgetsConfig | None = None,
    ):
        self.project_root = project_root
        self.job_store = job_store
        self.application_store = application_store
        self.site_tools = site_tools
        self.browser_runner = browser_runner
        self.search_strategy = search_strategy
        self.profile_store = profile_store
        self.cv_store = cv_store
        self.intent_store = intent_store
        self.site_parallelism = max(1, int(site_parallelism or 1))
        self.browser_budgets = browser_budgets or BrowserBudgetsConfig()
        self.job_planning_store = JobPlanningStore(job_store.workspace)
        self.capability_store = EvolutionCapabilityStore(job_store.workspace)

    @property
    def APPLY_JOB_PHASE_TIMEOUT_SECONDS(self) -> int:
        return int(self.browser_budgets.apply_job_phase_timeout_seconds)

    @property
    def APPLY_JOB_TIMEOUT_MS(self) -> int:
        return int(self.browser_budgets.apply_job_timeout_ms)

    @property
    def APPLY_SITE_PHASE_BUDGET_FACTOR(self) -> float:
        return float(self.browser_budgets.apply_site_phase_budget_factor)

    @property
    def APPLY_PROBE_MAX_ATTEMPTED(self) -> int:
        return int(self.browser_budgets.apply_probe_max_attempted)

    @property
    def APPLY_PROBE_UNSUCCESSFUL_THRESHOLD(self) -> int:
        return int(self.browser_budgets.apply_probe_unsuccessful_threshold)

    def close(self) -> None:
        closer = getattr(self.browser_runner, "close", None)
        if callable(closer):
            closer()
        return None

    @classmethod
    def _normalize_operation(cls, operation: str) -> str:
        normalized = str(operation or "").strip().lower()
        if normalized in cls.PHASE_PLANS:
            return normalized
        return cls.OPERATION_JOB_SEARCH

    def _phase_plan_for_operation(self, operation: str) -> tuple[str, ...]:
        normalized = self._normalize_operation(operation)
        if normalized == self.OPERATION_JOB_SEARCH:
            return tuple(self.DISCOVERY_PHASES)
        return tuple(self.PHASE_PLANS[normalized])

    def _terminal_phase_for_operation(self, operation: str) -> str:
        phases = self._phase_plan_for_operation(operation)
        return phases[-1] if phases else ""

    def _compute_batch_status(self, batch: dict[str, Any]) -> str:
        if str(batch.get("status") or "") == "cancelled":
            return "cancelled"
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        rows = [row for row in sites.values() if isinstance(row, dict)]
        operation = self._normalize_operation(str(batch.get("operation") or ""))
        apply_requested = bool(batch.get("apply_requested"))
        if any(str(row.get("status") or "") in {"queued", "running"} for row in rows):
            return "running"
        if operation == self.OPERATION_JOB_SEARCH and apply_requested:
            for row in rows:
                row_status = str(row.get("status") or "")
                retrieve_status = str((row.get("retrieve") or {}).get("status") or "")
                apply_status = str((row.get("apply") or {}).get("status") or "")
                if row_status in {"blocked_login", "blocked", "failed", "skipped"}:
                    continue
                if apply_status == "running" or (apply_status == "pending" and retrieve_status == "done"):
                    return "running"
        if any(str(row.get("status") or "") in {"blocked_login", "blocked"} for row in rows):
            return "waiting_user"
        if any(
            str((row.get("apply") or {}).get("status") or "") in {"failed", "blocked"}
            or str((row.get("retrieve") or {}).get("status") or "") == "failed"
            or str(row.get("status") or "") in {"failed", "skipped"}
            for row in rows
        ):
            return "partial_completed"
        if rows and all(str(row.get("status") or "") in {"ready", "completed"} for row in rows):
            return "completed"
        if rows:
            return "completed"
        return "failed"

    def _is_batch_cancelled(self, batch_id: str) -> bool:
        if not str(batch_id or "").strip():
            return False
        try:
            batch = self.job_store.load_batch(batch_id)
        except Exception:
            return False
        return str(batch.get("status") or "") == "cancelled"

    @staticmethod
    def _cancelled_site_row(existing: dict[str, Any], *, current_phase: str = "") -> dict[str, Any]:
        retrieve = dict(existing.get("retrieve") or {})
        apply = dict(existing.get("apply") or {})
        if str(retrieve.get("status") or "") == "running":
            retrieve["status"] = "cancelled"
            retrieve.setdefault("reason_tag", "batch_cancelled")
        if current_phase == "apply" or str(apply.get("status") or "") in {"pending", "running"}:
            apply["status"] = "cancelled"
            apply.setdefault("reason_tag", "batch_cancelled")
        return {
            **existing,
            "status": "cancelled",
            "reason_tag": "batch_cancelled",
            "message": "Cancelled by batch-stop.",
            "current_phase": current_phase or str(existing.get("current_phase") or ""),
            "retrieve": retrieve,
            "apply": apply,
        }

    def _format_site_line(self, row: dict[str, Any]) -> str:
        site_key = str(row.get("site_key") or "site")
        site_name = str(row.get("site_name") or site_key)
        status = str(row.get("status") or "unknown")
        retrieve = row.get("retrieve") if isinstance(row.get("retrieve"), dict) else {}
        apply = row.get("apply") if isinstance(row.get("apply"), dict) else {}
        reason = str(row.get("reason_tag") or apply.get("reason_tag") or retrieve.get("reason_tag") or "")
        if status == "skipped":
            skill_path = str(row.get("skill_path") or "")
            suffix = f" 请补充 {skill_path}。" if skill_path else ""
            return f"- {site_name} [{site_key}]: 已跳过（{reason or 'preflight_skip'}）。{suffix}".rstrip()
        if status in {"blocked_login", "blocked"}:
            message = str(row.get("message") or "")
            if message:
                return f"- {site_name} [{site_key}]: {message}"
            return f"- {site_name} [{site_key}]: 浏览器自动化已禁用。"
        if status == "ready":
            message = str(
                row.get("message")
                or self._ready_message_for_phase(
                    str(row.get("current_phase") or ""),
                    authenticated_ready=bool(row.get("authenticated_ready") or row.get("session_ready")),
                    jobs_surface_ready=bool(row.get("jobs_surface_ready")),
                )
            )
            return f"- {site_name} [{site_key}]: {message}"
        if str(row.get("operation") or "") == self.OPERATION_APPLICATION_STATUS_REVIEW and status == "completed":
            message = str(row.get("message") or "申请状态检查已完成。")
            return f"- {site_name} [{site_key}]: {message}"
        if str(retrieve.get("status") or "") == "failed":
            return f"- {site_name} [{site_key}]: 岗位检索失败（{reason or 'retrieve_failed'}）。"
        retrieve_count = int(retrieve.get("count") or 0)
        if str(apply.get("status") or "") == "done":
            submitted = int(apply.get("submitted") or 0)
            attempted = int(apply.get("form_sampled") or apply.get("attempted") or 0)
            already_applied = int(apply.get("already_applied") or 0)
            filtered_out = int(apply.get("filtered_out") or 0)
            failed = int(apply.get("failed") or 0)
            blocked = int(apply.get("blocked") or 0)
            suffix_parts: list[str] = []
            if already_applied:
                suffix_parts.append(f"已存在申请 {already_applied} 个")
            if filtered_out:
                suffix_parts.append(f"不匹配 {filtered_out} 个")
            if failed:
                suffix_parts.append(f"失败 {failed} 个")
            if blocked:
                suffix_parts.append(f"阻塞 {blocked} 个")
            suffix = f"，{'，'.join(suffix_parts)}" if suffix_parts else ""
            return f"- {site_name} [{site_key}]: 已检索 {retrieve_count} 个岗位，表单样本 {attempted} 个，成功 {submitted} 个{suffix}。"
        if str(apply.get("status") or "") in {"probe_completed", "probe_failed"}:
            attempted = int(apply.get("form_sampled") or apply.get("attempted") or 0)
            submitted = int(apply.get("submitted") or 0)
            failed = int(apply.get("form_unsuccessful") or apply.get("failed") or 0)
            blocked = int(apply.get("blocked") or 0)
            probe = apply.get("probe") if isinstance(apply.get("probe"), dict) else {}
            auto_accept = probe.get("auto_accept") if isinstance(probe.get("auto_accept"), dict) else {}
            report_md = str(probe.get("report_md") or "")
            status_label = "探测完成" if str(apply.get("status") or "") == "probe_completed" else "探测停止"
            report_suffix = f"，report={report_md}" if report_md else ""
            accept_suffix = "，已自动接受" if str(auto_accept.get("status") or "") == "accepted" else ""
            return (
                f"- {site_name} [{site_key}]: apply {status_label}，"
                f"表单样本 {attempted} 个，成功 {submitted} 个，失败样本 {failed} 个，阻塞 {blocked} 个"
                f"{accept_suffix}{report_suffix}。"
            )
        if str(apply.get("status") or "") == "failed":
            return f"- {site_name} [{site_key}]: 已检索 {retrieve_count} 个岗位，投递阶段失败（{reason or 'apply_failed'}）。"
        if str(apply.get("status") or "") == "blocked":
            return f"- {site_name} [{site_key}]: 已检索 {retrieve_count} 个岗位，投递阶段阻塞（{reason or 'apply_blocked'}）。"
        return f"- {site_name} [{site_key}]: 已检索 {retrieve_count} 个岗位，未执行投递。"

    @classmethod
    def _ready_message_for_phase(cls, phase_slug: str, *, authenticated_ready: bool, jobs_surface_ready: bool) -> str:
        normalized = str(phase_slug or "").strip()
        if authenticated_ready:
            if normalized == "application_status_review":
                return "登录已就绪，申请状态检查已完成。"
            if normalized == "job_retrieval":
                return "登录已就绪，岗位检索已完成，等待后续投递。"
            if normalized == "job_filtering":
                return "登录已就绪，岗位筛选已完成，等待后续岗位检索。"
            if normalized == "channel_discovery":
                return "登录已就绪，岗位入口已定位，等待后续岗位检索。"
            return "登录已就绪，等待后续岗位检索。"
        if jobs_surface_ready:
            if normalized == "application_status_review":
                return "申请状态检查已完成。"
            if normalized == "job_retrieval":
                return "岗位检索已完成，当前 jobs 页面可继续，等待后续投递。"
            if normalized == "job_filtering":
                return "岗位筛选已完成，当前 jobs 页面可继续，等待后续岗位检索。"
            if normalized == "channel_discovery":
                return "岗位入口已定位，当前 jobs 页面可继续，等待后续岗位检索。"
        return "当前 jobs 页面可继续，等待后续岗位检索。"

    def _format_batch_summary(self, batch: dict[str, Any]) -> str:
        status = str(batch.get("status") or "unknown")
        lines = [f"batch={batch.get('batch_id')} status={status}"]
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        for site_key in sorted(sites.keys()):
            row = sites.get(site_key)
            if isinstance(row, dict):
                lines.append(self._format_site_line(row))
        return "\n".join(lines)

    def _generate_batch_report_if_possible(self, batch: dict[str, Any]) -> None:
        batch_id = str(batch.get("batch_id") or "")
        if not batch_id:
            return
        try:
            report = generate_job_batch_report(
                workspace=self.job_store.workspace,
                project_root=self.project_root,
                batch_id=batch_id,
            )
        except Exception as exc:
            self.job_store.append_event(
                "report.failed",
                {
                    "batch_id": batch_id,
                    "error": str(exc),
                },
            )
            return
        self.job_store.append_event(
            "report.generated",
            {
                "batch_id": batch_id,
                "json_path": str(report.get("json_path") or ""),
                "markdown_path": str(report.get("markdown_path") or ""),
                "final_json_path": str(report.get("final_json_path") or ""),
                "final_markdown_path": str(report.get("final_markdown_path") or ""),
            },
        )

    def _disabled_site_row(
        self,
        *,
        site_key: str,
        site_name: str,
        entry_url: str,
        skill_path: str,
        allow_apply: bool,
        operation: str = OPERATION_JOB_SEARCH,
    ) -> dict[str, Any]:
        return {
            "site_key": site_key,
            "site_name": site_name,
            "operation": self._normalize_operation(operation),
            "status": "failed",
            "reason_tag": "browser_automation_disabled",
            "entry_url": entry_url,
            "skill_path": skill_path,
            "retrieve": {"status": "failed", "count": 0, "error": "browser_automation_disabled"},
            "apply": {
                "status": "skipped" if not allow_apply else "failed",
                "attempted": 0,
                "submitted": 0,
                "reason_tag": "browser_automation_disabled" if allow_apply else "",
            },
        }

    def _ready_site_row(
        self,
        *,
        site_key: str,
        site_name: str,
        entry_url: str,
        skill_path: str,
        allow_apply: bool,
        operation: str = OPERATION_JOB_SEARCH,
    ) -> dict[str, Any]:
        normalized_operation = self._normalize_operation(operation)
        return {
            "site_key": site_key,
            "site_name": site_name,
            "operation": normalized_operation,
            "status": "running",
            "reason_tag": "",
            "entry_url": entry_url,
            "skill_path": skill_path,
            "retrieve": {
                "status": "skipped" if normalized_operation == self.OPERATION_APPLICATION_STATUS_REVIEW else "running",
                "count": 0,
            },
            "apply": {
                "status": "pending"
                if allow_apply and normalized_operation == self.OPERATION_JOB_SEARCH
                else "skipped",
                "attempted": 0,
                "submitted": 0,
            },
        }

    def _browser_result_to_site_row(
        self,
        *,
        result: Any,
        existing: dict[str, Any],
        allow_apply: bool,
        operation: str = OPERATION_JOB_SEARCH,
    ) -> dict[str, Any]:
        normalized_operation = self._normalize_operation(operation)
        terminal_phase = self._terminal_phase_for_operation(normalized_operation)
        site_key = str(existing.get("site_key") or getattr(result, "site_key", ""))
        site_name = str(existing.get("site_name") or getattr(result, "site_name", site_key))
        entry_url = str(existing.get("entry_url") or existing.get("current_url") or "")
        skill_path = str(existing.get("skill_path") or "")
        status = str(getattr(result, "status", "") or "failed")
        reason_tag = str(getattr(result, "reason_tag", "") or "")
        message = str(getattr(result, "message", "") or "")
        current_phase = str(getattr(result, "current_phase", "") or "")
        current_url = str(getattr(result, "current_url", "") or entry_url)
        trace_ref = str(getattr(result, "trace_ref", "") or "")
        step_count = int(getattr(result, "step_count", 0) or 0)
        retrieved_count = int(getattr(result, "retrieved_count", 0) or 0)
        authenticated_ready = bool(getattr(result, "authenticated_ready", False))
        jobs_surface_ready = bool(getattr(result, "jobs_surface_ready", False))

        if status == "ready":
            if current_phase == terminal_phase and normalized_operation == self.OPERATION_APPLICATION_STATUS_REVIEW:
                return {
                    "site_key": site_key,
                    "site_name": site_name,
                    "operation": normalized_operation,
                    "status": "completed",
                    "reason_tag": reason_tag,
                    "message": message
                    or self._ready_message_for_phase(
                        current_phase,
                        authenticated_ready=authenticated_ready,
                        jobs_surface_ready=jobs_surface_ready,
                    ),
                    "entry_url": entry_url,
                    "current_phase": current_phase,
                    "current_url": current_url,
                    "trace_ref": trace_ref,
                    "step_count": step_count,
                    "skill_path": skill_path,
                    "authenticated_ready": authenticated_ready,
                    "jobs_surface_ready": jobs_surface_ready,
                    "session_ready": authenticated_ready,
                    "retrieve": {"status": "skipped", "count": 0},
                    "apply": {
                        "status": "skipped",
                        "attempted": 0,
                        "submitted": 0,
                    },
                }
            if current_phase == terminal_phase and normalized_operation == self.OPERATION_JOB_SEARCH:
                return {
                    "site_key": site_key,
                    "site_name": site_name,
                    "operation": normalized_operation,
                    "status": "completed",
                    "reason_tag": reason_tag,
                    "message": message
                    or self._ready_message_for_phase(
                        current_phase,
                        authenticated_ready=authenticated_ready,
                        jobs_surface_ready=jobs_surface_ready,
                    ),
                    "entry_url": entry_url,
                    "current_phase": current_phase,
                    "current_url": current_url,
                    "trace_ref": trace_ref,
                    "step_count": step_count,
                    "skill_path": skill_path,
                    "authenticated_ready": authenticated_ready,
                    "jobs_surface_ready": jobs_surface_ready,
                    "session_ready": authenticated_ready,
                    "retrieve": {"status": "done", "count": retrieved_count},
                    "apply": {
                        "status": "pending" if allow_apply else "skipped",
                        "attempted": 0,
                        "submitted": 0,
                    },
                }
            return {
                "site_key": site_key,
                "site_name": site_name,
                "operation": normalized_operation,
                "status": "ready",
                "reason_tag": reason_tag,
                "message": message
                or self._ready_message_for_phase(
                    current_phase,
                    authenticated_ready=authenticated_ready,
                    jobs_surface_ready=jobs_surface_ready,
                ),
                "entry_url": entry_url,
                "current_phase": current_phase,
                "current_url": current_url,
                "trace_ref": trace_ref,
                "step_count": step_count,
                "skill_path": skill_path,
                "authenticated_ready": authenticated_ready,
                "jobs_surface_ready": jobs_surface_ready,
                "session_ready": authenticated_ready,
                "retrieve": {"status": "pending", "count": retrieved_count},
                "apply": {
                    "status": "pending" if allow_apply and normalized_operation == self.OPERATION_JOB_SEARCH else "skipped",
                    "attempted": 0,
                    "submitted": 0,
                },
            }
        if status == "blocked":
            return {
                "site_key": site_key,
                "site_name": site_name,
                "operation": normalized_operation,
                "status": "blocked",
                "reason_tag": reason_tag,
                "message": message or f"{site_key} 需要先完成登录，关闭窗口后再回复 `{site_key} done`。",
                "entry_url": entry_url,
                "current_phase": current_phase,
                "current_url": current_url,
                "trace_ref": trace_ref,
                "step_count": step_count,
                "skill_path": skill_path,
                "retrieve": {
                    "status": "skipped" if normalized_operation == self.OPERATION_APPLICATION_STATUS_REVIEW else "blocked",
                    "count": retrieved_count,
                },
                "apply": {
                    "status": "pending" if allow_apply and normalized_operation == self.OPERATION_JOB_SEARCH else "skipped",
                    "attempted": 0,
                    "submitted": 0,
                },
            }
        return {
            "site_key": site_key,
            "site_name": site_name,
            "operation": normalized_operation,
            "status": "failed",
            "reason_tag": reason_tag or "browser_runtime_failed",
            "message": message,
            "entry_url": entry_url,
            "current_phase": current_phase,
            "current_url": current_url,
            "trace_ref": trace_ref,
            "step_count": step_count,
            "skill_path": skill_path,
            "retrieve": {
                "status": "skipped" if normalized_operation == self.OPERATION_APPLICATION_STATUS_REVIEW else "failed",
                "count": retrieved_count,
                "error": message,
            },
            "apply": {
                "status": "failed" if allow_apply and normalized_operation == self.OPERATION_JOB_SEARCH else "skipped",
                "attempted": 0,
                "submitted": 0,
                "reason_tag": reason_tag or "browser_runtime_failed",
            },
        }

    @classmethod
    def _phase_requires_auth_recovery(cls, result: Any) -> bool:
        status = str(getattr(result, "status", "") or "").strip().lower()
        if status != "blocked":
            return False
        current_phase = str(getattr(result, "current_phase", "") or "").strip()
        if not current_phase or current_phase == cls.AUTH_RECOVERY_PHASE:
            return False
        text = " ".join(
            str(value or "")
            for value in (
                getattr(result, "reason_tag", ""),
                getattr(result, "message", ""),
                getattr(result, "current_url", ""),
            )
        ).lower()
        return any(marker in text for marker in cls.AUTH_RECOVERY_MARKERS)

    def _remaining_phases_from(self, phase_slug: str, phases: tuple[str, ...]) -> tuple[str, ...]:
        normalized = str(phase_slug or "").strip()
        if not normalized:
            return phases
        try:
            index = tuple(phases).index(normalized)
        except ValueError:
            return phases
        return tuple(phases[index:])

    def _run_site_with_auth_recovery(
        self,
        *,
        site_key: str,
        site_name: str,
        entry_url: str,
        session_id: str,
        turn_id: str,
        batch_id: str,
        phase_slugs: tuple[str, ...],
        resume: bool = False,
        apply_target_job_ids: tuple[str, ...] | None = None,
        phase_timeout_seconds_override: int | None = None,
        timeout_ms_override: int | None = None,
    ) -> Any:
        result = self.browser_runner.run_site(
            site_key=site_key,
            site_name=site_name,
            entry_url=entry_url,
            session_id=session_id,
            turn_id=turn_id,
            batch_id=batch_id,
            resume=resume,
            phase_slugs=phase_slugs,
            apply_target_job_ids=apply_target_job_ids,
            phase_timeout_seconds_override=phase_timeout_seconds_override,
            timeout_ms_override=timeout_ms_override,
        )
        if not self._phase_requires_auth_recovery(result):
            return result

        login_result = self.browser_runner.run_site(
            site_key=site_key,
            site_name=site_name,
            entry_url=str(getattr(result, "current_url", "") or entry_url),
            session_id=session_id,
            turn_id=turn_id,
            batch_id=batch_id,
            resume=True,
            phase_slugs=(self.AUTH_RECOVERY_PHASE,),
            timeout_ms_override=timeout_ms_override,
        )
        if str(getattr(login_result, "status", "") or "") != "ready":
            return login_result

        remaining_phases = self._remaining_phases_from(
            str(getattr(result, "current_phase", "") or ""),
            tuple(phase_slugs),
        )
        if not remaining_phases:
            return login_result
        return self.browser_runner.run_site(
            site_key=site_key,
            site_name=site_name,
            entry_url=str(
                getattr(login_result, "current_url", "")
                or getattr(result, "current_url", "")
                or entry_url
            ),
            session_id=session_id,
            turn_id=turn_id,
            batch_id=batch_id,
            resume=True,
            phase_slugs=remaining_phases,
            apply_target_job_ids=apply_target_job_ids,
            phase_timeout_seconds_override=phase_timeout_seconds_override,
            timeout_ms_override=timeout_ms_override,
        )

    def _run_job_rows(self, site_key: str, batch_id: str) -> list[dict[str, Any]]:
        list_run_jobs = getattr(self.site_tools.site_store, "list_run_jobs", None)
        if not callable(list_run_jobs):
            return []
        rows = list_run_jobs(site_key, batch_id)
        return [row for row in rows if isinstance(row, dict)]

    @staticmethod
    def _run_job_identity(row: dict[str, Any]) -> str:
        for field in ("job_id", "canonical_job_id", "url"):
            value = str(row.get(field) or "").strip()
            if value:
                return f"{field}:{value}"
        title = str(row.get("title") or "").strip()
        location = str(row.get("location") or "").strip()
        posted_label = str(row.get("posted_label") or "").strip()
        return f"fallback:{title}|{location}|{posted_label}" if title else ""

    @classmethod
    def _merged_run_job_rows(cls, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged_by_key: dict[str, dict[str, Any]] = {}
        ordered_keys: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = cls._run_job_identity(row)
            if not key:
                continue
            current = merged_by_key.get(key)
            if current is None:
                merged_by_key[key] = dict(row)
                ordered_keys.append(key)
                continue
            merged = dict(current)
            for field, value in row.items():
                if value is None or value == "":
                    continue
                merged[field] = value
            merged_by_key[key] = merged
        return [merged_by_key[key] for key in ordered_keys if key in merged_by_key]

    def _merged_run_job_rows_for_batch(self, site_key: str, batch_id: str) -> list[dict[str, Any]]:
        return self._merged_run_job_rows(self._run_job_rows(site_key, batch_id))

    @staticmethod
    def _terminal_application_status(row: dict[str, Any]) -> str:
        application_status = str(row.get("application_status") or "").strip().lower()
        if application_status in {
            "already_applied",
            "filtered_out",
            "submitted",
            "apply_failed",
            "blocked",
            "rejected",
            "closed",
            "withdrawn",
        }:
            return application_status
        decision_status = str(row.get("decision_status") or "").strip().lower()
        if decision_status == "already_applied":
            return "already_applied"
        return ""

    def _apply_counters_from_run(self, site_key: str, batch_id: str) -> dict[str, int]:
        rows = self._merged_run_job_rows_for_batch(site_key, batch_id)
        return apply_probe_counters(rows)

    @staticmethod
    def _apply_counter_payload(counters: dict[str, int]) -> dict[str, int]:
        return {
            "attempted": int(counters.get("attempted") or 0),
            "form_sampled": int(counters.get("form_sampled") or 0),
            "form_successful": int(counters.get("form_successful") or 0),
            "form_unsuccessful": int(counters.get("form_unsuccessful") or 0),
            "apply_path_attempted": int(counters.get("apply_path_attempted") or 0),
            "submitted": int(counters.get("submitted") or 0),
            "already_applied": int(counters.get("already_applied") or 0),
            "filtered_out": int(counters.get("filtered_out") or 0),
            "failed": int(counters.get("failed") or 0),
            "blocked": int(counters.get("blocked") or 0),
            "excluded_role_violations": int(counters.get("excluded_role_violations") or 0),
        }

    @staticmethod
    def _is_apply_row_terminal(row: dict[str, Any]) -> bool:
        decision_status = str(row.get("decision_status") or "").strip().lower()
        application_status = JobFlow._terminal_application_status(row)
        return decision_status in {"filtered_out", "already_applied"} or application_status in {
            "already_applied",
            "filtered_out",
            "submitted",
            "apply_failed",
            "blocked",
            "rejected",
            "closed",
            "withdrawn",
        }

    def _write_retrieval_snapshot(self, *, site_key: str, batch_id: str, current: dict[str, Any]) -> dict[str, Any]:
        rows = self._merged_run_job_rows_for_batch(site_key, batch_id)
        if not rows:
            return {}
        retrieve = current.get("retrieve") if isinstance(current.get("retrieve"), dict) else {}
        snapshot = self.job_planning_store.write_snapshot(
            site_key=site_key,
            batch_id=batch_id,
            jobs=rows,
            current_url=str(current.get("current_url") or current.get("entry_url") or ""),
            retrieval_complete=str(retrieve.get("status") or "") == "done",
            result_count=int(retrieve.get("count") or len(rows)),
            filters_summary={
                "current_url": str(current.get("current_url") or ""),
                "skill_path": str(current.get("skill_path") or ""),
            },
            stop_reason=str(current.get("reason_tag") or ""),
        )
        save_run_context = getattr(self.site_tools.site_store, "save_run_context", None)
        if callable(save_run_context):
            save_run_context(
                site_key,
                batch_id,
                {
                    "latest_search_snapshot": {
                        "snapshot_id": str(snapshot.get("snapshot_id") or ""),
                        "search_fingerprint": str(snapshot.get("search_fingerprint") or ""),
                        "path": str(snapshot.get("path") or ""),
                    }
                },
            )
        self.job_store.append_event(
            "job_search_snapshot.written",
            {
                "batch_id": batch_id,
                "site_key": site_key,
                "snapshot_id": str(snapshot.get("snapshot_id") or ""),
                "search_fingerprint": str(snapshot.get("search_fingerprint") or ""),
                "retrieval_complete": bool(snapshot.get("retrieval_complete")),
                "result_count": int(snapshot.get("result_count") or 0),
            },
        )
        return snapshot

    def _latest_search_snapshot_id(self, *, site_key: str, batch_id: str) -> str:
        load_run_context = getattr(self.site_tools.site_store, "load_run_context", None)
        context = load_run_context(site_key, batch_id) if callable(load_run_context) else {}
        latest = context.get("latest_search_snapshot") if isinstance(context, dict) else {}
        return str(latest.get("snapshot_id") or "") if isinstance(latest, dict) else ""

    def _decision_context_hash(self, site_key: str) -> str:
        context_hash = getattr(self.site_tools.site_store, "decision_context_hash", None)
        if not callable(context_hash):
            return ""
        try:
            return str(context_hash(site_key) or "")
        except Exception:
            return ""

    def _ensure_apply_plan(
        self,
        *,
        site_key: str,
        batch_id: str,
        session_id: str,
        turn_id: str,
    ) -> dict[str, Any]:
        plan = self.job_planning_store.load_apply_plan(batch_id=batch_id, site_key=site_key)
        if not plan.get("plan_items"):
            rows = self._merged_run_job_rows_for_batch(site_key, batch_id)
            match_history_rows = getattr(self.site_tools.site_store, "match_history_rows", None)
            history_matches = match_history_rows(site_key, rows) if callable(match_history_rows) else []
            plan = self.job_planning_store.write_apply_plan(
                site_key=site_key,
                batch_id=batch_id,
                jobs=rows,
                history_matches=history_matches,
                snapshot_id=self._latest_search_snapshot_id(site_key=site_key, batch_id=batch_id),
                apply_requested=True,
                decision_context_hash=self._decision_context_hash(site_key),
            )
            self.job_store.append_event(
                "job_apply_plan.written",
                {
                    "batch_id": batch_id,
                    "site_key": site_key,
                    "plan_id": str(plan.get("plan_id") or ""),
                    "snapshot_id": str(plan.get("snapshot_id") or ""),
                    "counts": plan.get("counts") if isinstance(plan.get("counts"), dict) else {},
                },
            )
        terminal_updates = [
            update
            for item in plan.get("plan_items", [])
            if isinstance(item, dict)
            for update in [self.job_planning_store.terminal_update_for_plan_item(item)]
            if update
        ]
        if terminal_updates:
            self.site_tools.site_store.update_run_jobs(site_key, terminal_updates, session_id, turn_id, batch_id)
        return plan

    @staticmethod
    def _apply_plan_counts(plan: dict[str, Any]) -> dict[str, int]:
        counts = plan.get("counts") if isinstance(plan.get("counts"), dict) else {}
        normalized: dict[str, int] = {}
        for key, value in counts.items():
            try:
                normalized[str(key)] = int(value or 0)
            except Exception:
                normalized[str(key)] = 0
        return normalized

    @staticmethod
    def _aggregate_apply_status(counters: dict[str, int]) -> str:
        if int(counters.get("failed") or 0):
            return "failed"
        if int(counters.get("blocked") or 0):
            return "blocked"
        return "done"

    def _pending_apply_rows(self, site_key: str, batch_id: str) -> list[dict[str, Any]]:
        return [row for row in self._merged_run_job_rows_for_batch(site_key, batch_id) if not self._is_apply_row_terminal(row)]

    def _seal_apply_row_terminal(
        self,
        *,
        site_key: str,
        batch_id: str,
        session_id: str,
        turn_id: str,
        job_id: str,
        status: str,
        error_text: str = "",
    ) -> None:
        self.site_tools.site_store.update_run_jobs(
            site_key,
            [
                {
                    "job_id": str(job_id or ""),
                    "application_status": status,
                    "last_apply_error": str(error_text or ""),
                }
            ],
            session_id,
            turn_id,
            batch_id,
        )

    def _finalize_apply_site_row(
        self,
        *,
        site_key: str,
        existing: dict[str, Any],
        batch_id: str,
        last_result: Any | None,
        message: str = "",
    ) -> dict[str, Any]:
        retrieve = dict(existing.get("retrieve") or {})
        apply = dict(existing.get("apply") or {})
        counters = self._apply_counters_from_run(site_key, batch_id)
        apply_status = self._aggregate_apply_status(counters)
        apply.update(
            {
                "status": apply_status,
                **self._apply_counter_payload(counters),
            }
        )
        retrieve["count"] = max(int(retrieve.get("count") or 0), counters["retrieved"])
        current_url = str(
            getattr(last_result, "current_url", "") or existing.get("current_url") or existing.get("entry_url") or ""
        )
        trace_ref = str(getattr(last_result, "trace_ref", "") or existing.get("trace_ref") or "")
        step_count = int(getattr(last_result, "step_count", 0) or existing.get("step_count") or 0)
        result_message = str(getattr(last_result, "message", "") or "").strip()
        site_status = "blocked" if apply_status == "blocked" else ("failed" if apply_status == "failed" else "completed")
        reason_tag = str(getattr(last_result, "reason_tag", "") or "").strip() or str(existing.get("reason_tag") or "").strip()
        if apply_status == "failed" and not reason_tag:
            reason_tag = "apply_failed"
        if apply_status == "blocked" and not reason_tag:
            reason_tag = "apply_blocked"
        message_text = str(message or "").strip()
        if not message_text and apply_status in {"failed", "blocked"}:
            message_text = result_message or str(existing.get("message") or "").strip()
        return {
            **existing,
            "status": site_status,
            "reason_tag": reason_tag,
            "message": message_text,
            "current_phase": "apply",
            "current_url": current_url,
            "trace_ref": trace_ref,
            "step_count": step_count,
            "retrieve": retrieve,
            "apply": apply,
        }

    def _apply_progress_site_row(
        self,
        *,
        site_key: str,
        existing: dict[str, Any],
        batch_id: str,
        last_result: Any | None,
    ) -> dict[str, Any]:
        retrieve = dict(existing.get("retrieve") or {})
        apply = dict(existing.get("apply") or {})
        counters = self._apply_counters_from_run(site_key, batch_id)
        apply.update(
            {
                "status": "running",
                **self._apply_counter_payload(counters),
            }
        )
        retrieve["count"] = max(int(retrieve.get("count") or 0), counters["retrieved"])
        current_url = str(
            getattr(last_result, "current_url", "") or existing.get("current_url") or existing.get("entry_url") or ""
        )
        trace_ref = str(getattr(last_result, "trace_ref", "") or existing.get("trace_ref") or "")
        step_count = int(getattr(last_result, "step_count", 0) or existing.get("step_count") or 0)
        return {
            **existing,
            "status": "running",
            "reason_tag": str(existing.get("reason_tag") or ""),
            "message": "",
            "current_phase": "apply",
            "current_url": current_url,
            "trace_ref": trace_ref,
            "step_count": step_count,
            "retrieve": retrieve,
            "apply": apply,
        }

    def _apply_site_phase_budget_seconds(self, *, job_count: int) -> int:
        count = max(0, int(job_count or 0))
        if count <= 0:
            return 0
        return max(1, int(self.APPLY_JOB_PHASE_TIMEOUT_SECONDS * count * self.APPLY_SITE_PHASE_BUDGET_FACTOR))

    def _apply_budget_exhausted_site_row(
        self,
        *,
        site_key: str,
        existing: dict[str, Any],
        batch_id: str,
        last_result: Any | None,
        message: str,
    ) -> dict[str, Any]:
        retrieve = dict(existing.get("retrieve") or {})
        apply = dict(existing.get("apply") or {})
        counters = self._apply_counters_from_run(site_key, batch_id)
        apply.update(
            {
                "status": "failed",
                **self._apply_counter_payload(counters),
            }
        )
        retrieve["count"] = max(int(retrieve.get("count") or 0), counters["retrieved"])
        current_url = str(
            getattr(last_result, "current_url", "") or existing.get("current_url") or existing.get("entry_url") or ""
        )
        trace_ref = str(getattr(last_result, "trace_ref", "") or existing.get("trace_ref") or "")
        step_count = int(getattr(last_result, "step_count", 0) or existing.get("step_count") or 0)
        return {
            **existing,
            "status": "completed" if counters["retrieved"] else "failed",
            "reason_tag": "apply_budget_exhausted",
            "message": message,
            "current_phase": "apply",
            "current_url": current_url,
            "trace_ref": trace_ref,
            "step_count": step_count,
            "retrieve": retrieve,
            "apply": apply,
        }

    def _apply_probe_capability(self, site_key: str) -> dict[str, Any]:
        return self.capability_store.get(
            site_key=site_key,
            phase="apply",
            candidate_id="apply_form_workflow",
        )

    def _apply_probe_is_accepted(self, site_key: str) -> bool:
        return self.capability_store.is_accepted(
            site_key=site_key,
            phase="apply",
            candidate_id="apply_form_workflow",
        )

    def _apply_probe_stop_reason(self, site_key: str, counters: dict[str, int]) -> str:
        if self._apply_probe_is_accepted(site_key):
            return ""
        unsuccessful = int(counters.get("form_unsuccessful") or 0)
        attempted = int(counters.get("form_sampled") or 0)
        unsuccessful_threshold = max(0, self.APPLY_PROBE_UNSUCCESSFUL_THRESHOLD)
        max_attempted = max(0, self.APPLY_PROBE_MAX_ATTEMPTED)
        if unsuccessful_threshold and unsuccessful >= unsuccessful_threshold:
            return "unsuccessful_threshold_reached"
        if max_attempted and attempted >= max_attempted:
            return "max_attempted_reached"
        return ""

    def _apply_probe_stop_site_row(
        self,
        *,
        site_key: str,
        existing: dict[str, Any],
        batch_id: str,
        last_result: Any | None,
        stop_reason: str,
    ) -> dict[str, Any]:
        retrieve = dict(existing.get("retrieve") or {})
        apply = dict(existing.get("apply") or {})
        counters = self._apply_counters_from_run(site_key, batch_id)
        run_rows = self._merged_run_job_rows_for_batch(site_key, batch_id)
        report = create_apply_probe_report(
            workspace=self.job_store.workspace,
            project_root=self.project_root,
            batch_id=batch_id,
            site_key=site_key,
            site_name=str(existing.get("site_name") or site_key),
            site_row=existing,
            counters=counters,
            run_rows=run_rows,
            stop_reason=stop_reason,
            max_attempted=self.APPLY_PROBE_MAX_ATTEMPTED,
            unsuccessful_threshold=self.APPLY_PROBE_UNSUCCESSFUL_THRESHOLD,
        )
        report_status = str(report.get("status") or "")
        auto_accept = report.get("auto_accept") if isinstance(report.get("auto_accept"), dict) else {}
        next_action = report.get("next_action") if isinstance(report.get("next_action"), dict) else {}
        report_paths = report.get("paths") if isinstance(report.get("paths"), dict) else {}
        capability: dict[str, Any] = {}
        if bool(auto_accept.get("eligible")):
            capability = self.capability_store.accept(
                site_key=site_key,
                phase="apply",
                candidate_id="apply_form_workflow",
                source_run_id=str(report.get("run_id") or ""),
                source_batch_id=batch_id,
                report_json=str(report_paths.get("report_json") or ""),
                report_md=str(report_paths.get("report_md") or ""),
                metrics=report.get("metrics") if isinstance(report.get("metrics"), dict) else {},
                reason=str(auto_accept.get("reason") or ""),
            )
        self.job_store.append_event(
            "evolution.apply_probe_report.generated",
            {
                "batch_id": batch_id,
                "site_key": site_key,
                "run_id": str(report.get("run_id") or ""),
                "status": report_status,
                "stop_reason": stop_reason,
                "report_json": str(report_paths.get("report_json") or ""),
                "report_md": str(report_paths.get("report_md") or ""),
                "auto_accept_status": str(auto_accept.get("status") or ""),
                "capability_id": str(capability.get("capability_id") or ""),
            },
        )
        apply_status = "probe_failed" if stop_reason == "unsuccessful_threshold_reached" else "probe_completed"
        apply.update(
            {
                "status": apply_status,
                **self._apply_counter_payload(counters),
                "probe": {
                    "candidate_id": "apply_form_workflow",
                    "status": report_status,
                    "stop_reason": stop_reason,
                    "next_action": next_action,
                    "auto_accept": auto_accept,
                    "capability": capability,
                    "report_json": str(report_paths.get("report_json") or ""),
                    "report_md": str(report_paths.get("report_md") or ""),
                },
            }
        )
        retrieve["count"] = max(int(retrieve.get("count") or 0), counters["retrieved"])
        current_url = str(
            getattr(last_result, "current_url", "") or existing.get("current_url") or existing.get("entry_url") or ""
        )
        trace_ref = str(getattr(last_result, "trace_ref", "") or existing.get("trace_ref") or "")
        step_count = int(getattr(last_result, "step_count", 0) or existing.get("step_count") or 0)
        if report_status == "needs_user_fact":
            site_status = "blocked"
        elif report_status in {"success", "keep_observing"}:
            site_status = "completed"
        else:
            site_status = "failed"
        return {
            **existing,
            "status": site_status,
            "reason_tag": f"apply_probe_{stop_reason}",
            "message": str(next_action.get("reason") or "Apply probe stopped and generated an evolution report."),
            "current_phase": "apply",
            "current_url": current_url,
            "trace_ref": trace_ref,
            "step_count": step_count,
            "retrieve": retrieve,
            "apply": apply,
        }

    def _apply_site_jobs(
        self,
        *,
        site_key: str,
        current: dict[str, Any],
        batch_id: str,
        session_id: str,
        turn_id: str,
        progress_callback: Any | None = None,
    ) -> dict[str, Any]:
        try:
            self.site_tools.ensure_default_resume_pdf()
        except Exception as exc:
            updated = dict(current)
            updated["reason_tag"] = "resume_pdf_unavailable"
            updated["message"] = str(exc)
            updated["apply"] = {
                **dict(current.get("apply") or {}),
                "status": "failed",
                "attempted": 0,
                "submitted": 0,
            }
            return updated

        last_result: Any | None = None
        apply_plan = self._ensure_apply_plan(
            site_key=site_key,
            batch_id=batch_id,
            session_id=session_id,
            turn_id=turn_id,
        )
        if apply_plan:
            apply_payload = dict(current.get("apply") or {})
            apply_payload["plan"] = {
                "plan_id": str(apply_plan.get("plan_id") or ""),
                "snapshot_id": str(apply_plan.get("snapshot_id") or ""),
                "path": str(apply_plan.get("path") or ""),
                "counts": self._apply_plan_counts(apply_plan),
            }
            current = {**current, "apply": apply_payload}
        initial_pending_rows = self._pending_apply_rows(site_key, batch_id)
        site_phase_budget_seconds = self._apply_site_phase_budget_seconds(job_count=len(initial_pending_rows))
        site_phase_deadline = time.monotonic() + float(site_phase_budget_seconds or 0)
        while True:
            if self._is_batch_cancelled(batch_id):
                return self._cancelled_site_row(current, current_phase="apply")
            pending_rows = self._pending_apply_rows(site_key, batch_id)
            if not pending_rows:
                break
            remaining_site_phase_seconds = site_phase_deadline - time.monotonic()
            if remaining_site_phase_seconds <= 0:
                return self._apply_budget_exhausted_site_row(
                    site_key=site_key,
                    existing=current,
                    batch_id=batch_id,
                    last_result=last_result,
                    message="apply site budget exhausted before all pending jobs were processed",
                )
            target = pending_rows[0]
            job_id = str(target.get("job_id") or "").strip()
            job_url = str(target.get("url") or "").strip()
            if not job_id:
                break
            if not job_url:
                self._seal_apply_row_terminal(
                    site_key=site_key,
                    batch_id=batch_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    job_id=job_id,
                    status="blocked",
                    error_text="missing job url",
                )
                continue

            phase_timeout_seconds_override = min(
                self.APPLY_JOB_PHASE_TIMEOUT_SECONDS,
                max(1, int(remaining_site_phase_seconds)),
            )
            last_result = self._run_site_with_auth_recovery(
                site_key=site_key,
                site_name=str(current.get("site_name") or site_key),
                entry_url=job_url,
                session_id=session_id,
                turn_id=turn_id,
                batch_id=batch_id,
                resume=False,
                phase_slugs=("apply",),
                apply_target_job_ids=(job_id,),
                phase_timeout_seconds_override=phase_timeout_seconds_override,
                timeout_ms_override=self.APPLY_JOB_TIMEOUT_MS,
            )
            if self._is_batch_cancelled(batch_id):
                return self._cancelled_site_row(current, current_phase="apply")

            latest_rows = {str(row.get("job_id") or ""): row for row in self._run_job_rows(site_key, batch_id)}
            latest_row = latest_rows.get(job_id) or {}
            if self._is_apply_row_terminal(latest_row):
                if callable(progress_callback):
                    current = progress_callback(
                        self._apply_progress_site_row(
                            site_key=site_key,
                            existing=current,
                            batch_id=batch_id,
                            last_result=last_result,
                        )
                    )
                stop_reason = self._apply_probe_stop_reason(site_key, self._apply_counters_from_run(site_key, batch_id))
                if stop_reason:
                    return self._apply_probe_stop_site_row(
                        site_key=site_key,
                        existing=current,
                        batch_id=batch_id,
                        last_result=last_result,
                        stop_reason=stop_reason,
                    )
                continue
            status = "blocked" if str(getattr(last_result, "status", "") or "") == "blocked" else "apply_failed"
            error_text = str(getattr(last_result, "message", "") or "").strip() or "apply phase ended without terminal job update"
            self._seal_apply_row_terminal(
                site_key=site_key,
                batch_id=batch_id,
                session_id=session_id,
                turn_id=turn_id,
                job_id=job_id,
                status=status,
                error_text=error_text,
            )
            if callable(progress_callback):
                current = progress_callback(
                    self._apply_progress_site_row(
                        site_key=site_key,
                        existing=current,
                        batch_id=batch_id,
                        last_result=last_result,
                    )
                )
            stop_reason = self._apply_probe_stop_reason(site_key, self._apply_counters_from_run(site_key, batch_id))
            if stop_reason:
                return self._apply_probe_stop_site_row(
                    site_key=site_key,
                    existing=current,
                    batch_id=batch_id,
                    last_result=last_result,
                    stop_reason=stop_reason,
                )

        if not self._pending_apply_rows(site_key, batch_id):
            self._promote_apply_run_to_history(
                site_key=site_key,
                batch_id=batch_id,
                session_id=session_id,
                turn_id=turn_id,
            )
        return self._finalize_apply_site_row(
            site_key=site_key,
            existing=current,
            batch_id=batch_id,
            last_result=last_result,
        )

    def _promote_retrieved_run_to_history(self, *, site_key: str, batch_id: str) -> None:
        promote = getattr(self.site_tools.site_store, "promote_run_jobs_to_history", None)
        if callable(promote):
            promote(site_key, batch_id)

    def _promote_apply_run_to_history(self, *, site_key: str, batch_id: str, session_id: str, turn_id: str) -> None:
        site_store = self.site_tools.site_store
        promote = getattr(site_store, "promote_run_jobs_to_history", None)
        if callable(promote):
            promote(site_key, batch_id)
        run_rows = self._run_job_rows(site_key, batch_id)
        update_decisions = getattr(site_store, "update_job_decisions", None)
        if callable(update_decisions):
            update_decisions(site_key, run_rows)

        applications: list[dict[str, Any]] = []
        for row in run_rows:
            if not isinstance(row, dict):
                continue
            status = self._terminal_application_status(row)
            if not status:
                continue
            detail: dict[str, Any] = {}
            error_text = str(row.get("last_apply_error") or "").strip()
            if error_text:
                detail["error"] = error_text
            applications.append(
                {
                    "job_id": str(row.get("job_id") or ""),
                    "canonical_job_id": str(row.get("canonical_job_id") or ""),
                    "status": status,
                    "submitted": status == "submitted",
                    "site_id": site_key,
                    "batch_id": batch_id,
                    "title": str(row.get("title") or ""),
                    "url": str(row.get("url") or ""),
                    "decision_context_hash": str(row.get("decision_context_hash") or ""),
                    "detail": detail,
                }
            )
        update_outcomes = getattr(site_store, "update_job_application_outcomes", None)
        if callable(update_outcomes):
            update_outcomes(site_key, applications)
        append_site_apps = getattr(site_store, "append_applications", None)
        if callable(append_site_apps):
            append_site_apps(site_key, applications, session_id, turn_id)
        for app in applications:
            self.application_store.append_application(
                {
                    "site_id": site_key,
                    "batch_id": batch_id,
                    "session_id": session_id,
                    "turn_id": turn_id,
                    **app,
                }
            )

    def _apply_result_to_site_row(
        self,
        *,
        result: Any,
        existing: dict[str, Any],
        batch_id: str,
    ) -> dict[str, Any]:
        site_key = str(existing.get("site_key") or getattr(result, "site_key", ""))
        site_name = str(existing.get("site_name") or getattr(result, "site_name", site_key))
        current_url = str(getattr(result, "current_url", "") or existing.get("current_url") or existing.get("entry_url") or "")
        trace_ref = str(getattr(result, "trace_ref", "") or "")
        step_count = int(getattr(result, "step_count", 0) or 0)
        reason_tag = str(getattr(result, "reason_tag", "") or "")
        message = str(getattr(result, "message", "") or "")
        current_phase = str(getattr(result, "current_phase", "") or "")
        retrieve = dict(existing.get("retrieve") or {})
        apply = dict(existing.get("apply") or {})
        counters = self._apply_counters_from_run(site_key, batch_id)

        if str(getattr(result, "status", "") or "") == "ready" and current_phase == "apply":
            apply_status = self._aggregate_apply_status(counters)
            apply.update(
                {
                    "status": apply_status,
                    **self._apply_counter_payload(counters),
                }
            )
            retrieve["count"] = max(int(retrieve.get("count") or 0), counters["retrieved"])
            site_status = "blocked" if apply_status == "blocked" else ("failed" if apply_status == "failed" else "completed")
            final_reason_tag = reason_tag or str(existing.get("reason_tag") or "").strip()
            if apply_status == "failed" and not final_reason_tag:
                final_reason_tag = "apply_failed"
            if apply_status == "blocked" and not final_reason_tag:
                final_reason_tag = "apply_blocked"
            return {
                **existing,
                "status": site_status,
                "reason_tag": final_reason_tag,
                "message": "",
                "current_phase": "apply",
                "current_url": current_url,
                "trace_ref": trace_ref,
                "step_count": step_count,
                "retrieve": retrieve,
                "apply": apply,
            }

        apply["status"] = "blocked" if str(getattr(result, "status", "") or "") == "blocked" else "failed"
        apply.update(self._apply_counter_payload(counters))
        retrieve["count"] = max(int(retrieve.get("count") or 0), counters["retrieved"])
        return {
            **existing,
            "status": "completed" if counters["retrieved"] else "failed",
            "reason_tag": reason_tag or ("apply_blocked" if apply["status"] == "blocked" else "apply_failed"),
            "message": message,
            "current_phase": current_phase or "apply",
            "current_url": current_url,
            "trace_ref": trace_ref,
            "step_count": step_count,
            "retrieve": retrieve,
            "apply": apply,
        }

    def create_batch(
        self,
        *,
        session_id: str,
        turn_id: str,
        user_message: str,
        apply_requested: bool,
        operation: str = OPERATION_JOB_SEARCH,
    ) -> dict[str, Any]:
        normalized_operation = self._normalize_operation(operation)
        active_sites = self.site_tools.site_store.list_sites("active")
        if not active_sites:
            return {}
        effective_apply_requested = bool(
            normalized_operation == self.OPERATION_JOB_SEARCH
            and apply_requested
            and self.ENABLE_BROWSER_APPLY_PHASE
        )

        site_rows: list[dict[str, Any]] = []
        for row in active_sites:
            site_key = str(row.get("site_key") or "")
            preflight = self.site_tools.preflight_site(site_key, apply_requested=effective_apply_requested)
            site_name = str(preflight.get("site_name") or row.get("canonical_company") or site_key)
            skill_path = str(preflight.get("skill_path") or "")
            entry_url = str(preflight.get("entry_url") or row.get("base_url") or "")
            allow_apply = bool(preflight.get("allow_apply")) and normalized_operation == self.OPERATION_JOB_SEARCH
            preflight_status = str(preflight.get("status") or "failed")
            if preflight_status != "ready":
                site_rows.append(
                    {
                        "site_key": site_key,
                        "site_name": site_name,
                        "operation": normalized_operation,
                        "status": preflight_status,
                        "reason_tag": str(preflight.get("reason_tag") or ""),
                        "entry_url": entry_url,
                        "skill_path": skill_path,
                        "retrieve": {"status": "skipped", "count": 0},
                        "apply": {"status": "skipped", "attempted": 0, "submitted": 0},
                    }
                )
                continue
            site_rows.append(
                self._ready_site_row(
                    site_key=site_key,
                    site_name=site_name,
                    entry_url=entry_url,
                    skill_path=skill_path,
                    allow_apply=allow_apply,
                    operation=normalized_operation,
                )
            )

        return self.job_store.create_batch(
            session_id=session_id,
            turn_id=turn_id,
            user_message=user_message,
            apply_requested=effective_apply_requested,
            operation=normalized_operation,
            sites=site_rows,
        )

    def fail_batch(self, *, batch_id: str, error: str) -> dict[str, Any]:
        batch = self.job_store.load_batch(batch_id)
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        updated_sites: dict[str, Any] = {}
        for site_key, row in sites.items():
            if not isinstance(row, dict):
                continue
            status = str(row.get("status") or "")
            if status in {"queued", "running", "ready"}:
                patched = dict(row)
                patched["status"] = "failed"
                patched["reason_tag"] = "batch_worker_failed"
                patched["message"] = str(error or "")
                updated_sites[str(site_key)] = patched
            else:
                updated_sites[str(site_key)] = row
        batch["sites"] = updated_sites
        batch["status"] = "failed"
        batch = self.job_store.save_batch(batch)
        self.job_store.append_event(
            "batch.failed",
            {
                "batch_id": str(batch.get("batch_id") or batch_id),
                "error": str(error or ""),
            },
        )
        self._generate_batch_report_if_possible(batch)
        return batch

    def run_batch(self, batch_id: str) -> str:
        batch = self.job_store.load_batch(batch_id)
        if not batch:
            return f"batch={batch_id} status=failed"
        normalized_operation = self._normalize_operation(str(batch.get("operation") or self.OPERATION_JOB_SEARCH))
        phase_slugs = self._phase_plan_for_operation(normalized_operation)
        session_id = str(batch.get("session_id") or "cli:default")
        turn_id = str(batch.get("turn_id") or "")
        effective_apply_requested = bool(
            normalized_operation == self.OPERATION_JOB_SEARCH
            and batch.get("apply_requested")
            and self.ENABLE_BROWSER_APPLY_PHASE
        )
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        runnable_keys = [
            str(site_key)
            for site_key, row in sites.items()
            if isinstance(row, dict) and str(row.get("status") or "") in {"queued", "running", "ready"}
        ]
        if not self.browser_runner:
            for site_key in runnable_keys:
                current = batch["sites"].get(site_key) if isinstance(batch.get("sites"), dict) else None
                if not isinstance(current, dict):
                    continue
                disabled = self._disabled_site_row(
                    site_key=site_key,
                    site_name=str(current.get("site_name") or site_key),
                    entry_url=str(current.get("entry_url") or ""),
                    skill_path=str(current.get("skill_path") or ""),
                    allow_apply=str((current.get("apply") or {}).get("status") or "") != "skipped",
                    operation=normalized_operation,
                )
                batch = self.job_store.update_site(batch, site_key, disabled)
            batch["status"] = self._compute_batch_status(batch)
            batch = self.job_store.save_batch(batch)
            self._generate_batch_report_if_possible(batch)
            return self._format_batch_summary(batch)

        batch_id = str(batch.get("batch_id") or "")
        batch_lock = threading.Lock()

        def _save_site_snapshot(site_key: str, updated: dict[str, Any], *, generate_report: bool) -> dict[str, Any]:
            nonlocal batch
            with batch_lock:
                latest = self.job_store.load_batch(batch_id) if batch_id else batch
                if str((latest or {}).get("status") or "") == "cancelled":
                    batch = latest or batch
                    latest_sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
                    latest_row = latest_sites.get(site_key)
                    return dict(latest_row) if isinstance(latest_row, dict) else self._cancelled_site_row(updated)
                batch = self.job_store.update_site(latest or batch, site_key, updated)
                batch["status"] = self._compute_batch_status(batch)
                batch = self.job_store.save_batch(batch)
                if generate_report:
                    self._generate_batch_report_if_possible(batch)
                latest_sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
                latest_row = latest_sites.get(site_key)
                return dict(latest_row) if isinstance(latest_row, dict) else dict(updated)

        def _job(site_key: str, current: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            if self._is_batch_cancelled(batch_id):
                return site_key, self._cancelled_site_row(current)
            allow_apply = (
                normalized_operation == self.OPERATION_JOB_SEARCH
                and str((current.get("apply") or {}).get("status") or "") != "skipped"
            )
            result = self._run_site_with_auth_recovery(
                site_key=site_key,
                site_name=str(current.get("site_name") or site_key),
                entry_url=str(current.get("entry_url") or ""),
                session_id=session_id,
                turn_id=turn_id,
                batch_id=batch_id,
                phase_slugs=phase_slugs,
            )
            if self._is_batch_cancelled(batch_id):
                return site_key, self._cancelled_site_row(current)
            updated = self._browser_result_to_site_row(
                result=result,
                existing=current,
                allow_apply=allow_apply,
                operation=normalized_operation,
            )
            operation_done = (
                normalized_operation == self.OPERATION_APPLICATION_STATUS_REVIEW
                and str(updated.get("status") or "") == "completed"
            )
            if operation_done:
                _save_site_snapshot(site_key, updated, generate_report=True)
                return site_key, updated
            retrieval_done = str((updated.get("retrieve") or {}).get("status") or "") == "done"
            if retrieval_done:
                self._promote_retrieved_run_to_history(site_key=site_key, batch_id=batch_id)
                snapshot = self._write_retrieval_snapshot(site_key=site_key, batch_id=batch_id, current=updated)
                if snapshot:
                    retrieve_payload = dict(updated.get("retrieve") or {})
                    retrieve_payload["snapshot_id"] = str(snapshot.get("snapshot_id") or "")
                    retrieve_payload["search_fingerprint"] = str(snapshot.get("search_fingerprint") or "")
                    retrieve_payload["snapshot_path"] = str(snapshot.get("path") or "")
                    updated = {**updated, "retrieve": retrieve_payload}
                _save_site_snapshot(site_key, updated, generate_report=True)
            if self._is_batch_cancelled(batch_id):
                return site_key, self._cancelled_site_row(updated, current_phase=str(updated.get("current_phase") or ""))
            if (
                effective_apply_requested
                and allow_apply
                and retrieval_done
                and str((updated.get("apply") or {}).get("status") or "") == "pending"
            ):
                updated = self._apply_site_jobs(
                    site_key=site_key,
                    current=updated,
                    batch_id=batch_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    progress_callback=lambda row: _save_site_snapshot(site_key, row, generate_report=True),
                )
            return site_key, updated

        runnable_rows = [
            (site_key, row)
            for site_key, row in (batch.get("sites") or {}).items()
            if site_key in runnable_keys and isinstance(row, dict)
        ]
        workers = min(self.site_parallelism, max(1, len(runnable_rows)))
        if runnable_rows:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_job, site_key, row) for site_key, row in runnable_rows]
                for future in concurrent.futures.as_completed(futures):
                    site_key, updated = future.result()
                    _save_site_snapshot(site_key, updated, generate_report=True)

        latest_batch = self.job_store.load_batch(batch_id) if batch_id else batch
        if str((latest_batch or {}).get("status") or "") == "cancelled":
            batch = latest_batch or batch
            self._generate_batch_report_if_possible(batch)
            return self._format_batch_summary(batch)
        batch["status"] = self._compute_batch_status(batch)
        batch = self.job_store.save_batch(batch)
        self._generate_batch_report_if_possible(batch)
        return self._format_batch_summary(batch)

    def start_batch(
        self,
        *,
        session_id: str,
        turn_id: str,
        user_message: str,
        apply_requested: bool,
        operation: str = OPERATION_JOB_SEARCH,
    ) -> str:
        batch = self.create_batch(
            session_id=session_id,
            turn_id=turn_id,
            user_message=user_message,
            apply_requested=apply_requested,
            operation=operation,
        )
        if not batch:
            return "当前没有已注册的 active sites。请先完成公司注册。"
        return self.run_batch(str(batch.get("batch_id") or ""))

    def _parse_resume_signal(self, message: str) -> tuple[str, str] | None:
        raw = message.strip()
        match = re.match(
            r"^([A-Za-z0-9][A-Za-z0-9\-]*)\s+(done|ok|ready|完成|y|yes|n|no|是|否|好|取消)$",
            raw,
            flags=re.I,
        )
        if not match:
            return None
        site_key = match.group(1).strip().lower()
        decision = match.group(2).strip().lower()
        return site_key, decision

    def handle_resume_message(self, *, session_id: str, message: str, turn_id: str) -> str | None:
        parsed = self._parse_resume_signal(message)
        if not parsed:
            return None
        site_key, _decision = parsed
        batch = self.job_store.latest_open_batch(session_id)
        if not batch:
            return None
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        current = sites.get(site_key)
        if not isinstance(current, dict):
            return None
        if str(current.get("status") or "") not in {"blocked_login", "blocked"}:
            return None
        if not self.browser_runner:
            replacement = self._disabled_site_row(
                site_key=site_key,
                site_name=str(current.get("site_name") or site_key),
                entry_url=str(current.get("entry_url") or current.get("current_url") or ""),
                skill_path=str(current.get("skill_path") or ""),
                allow_apply=str((current.get("apply") or {}).get("status") or "") != "skipped",
            )
        else:
            result = self._run_site_with_auth_recovery(
                site_key=site_key,
                site_name=str(current.get("site_name") or site_key),
                entry_url=str(current.get("entry_url") or current.get("current_url") or ""),
                session_id=session_id,
                turn_id=turn_id,
                batch_id=str(batch.get("batch_id") or ""),
                resume=True,
                phase_slugs=self.DISCOVERY_PHASES,
            )
            replacement = self._browser_result_to_site_row(
                result=result,
                existing=current,
                allow_apply=str((current.get("apply") or {}).get("status") or "") != "skipped",
            )
        batch = self.job_store.update_site(batch, site_key, replacement)
        batch["status"] = self._compute_batch_status(batch)
        batch = self.job_store.save_batch(batch)
        if str(batch.get("status") or "") != "waiting_user":
            self._generate_batch_report_if_possible(batch)
        return self._format_batch_summary(batch)
