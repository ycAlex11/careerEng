"""Batch retrieve/apply orchestration for registered sites."""

from __future__ import annotations

import concurrent.futures
import re
import threading
import time
from pathlib import Path
from typing import Any

from careereng.config.schema import BrowserBudgetsConfig
from careereng.reporting.job_report import generate_job_batch_report
from careereng.storage.application_store import ApplicationStore
from careereng.storage.job_store import JobStore
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

    @property
    def APPLY_JOB_PHASE_TIMEOUT_SECONDS(self) -> int:
        return int(self.browser_budgets.apply_job_phase_timeout_seconds)

    @property
    def APPLY_JOB_TIMEOUT_MS(self) -> int:
        return int(self.browser_budgets.apply_job_timeout_ms)

    @property
    def APPLY_SITE_PHASE_BUDGET_FACTOR(self) -> float:
        return float(self.browser_budgets.apply_site_phase_budget_factor)

    def close(self) -> None:
        closer = getattr(self.browser_runner, "close", None)
        if callable(closer):
            closer()
        return None

    def _compute_batch_status(self, batch: dict[str, Any]) -> str:
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        rows = [row for row in sites.values() if isinstance(row, dict)]
        if any(str(row.get("status") or "") in {"queued", "running"} for row in rows):
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
        if str(retrieve.get("status") or "") == "failed":
            return f"- {site_name} [{site_key}]: 岗位检索失败（{reason or 'retrieve_failed'}）。"
        retrieve_count = int(retrieve.get("count") or 0)
        if str(apply.get("status") or "") == "done":
            submitted = int(apply.get("submitted") or 0)
            attempted = int(apply.get("attempted") or 0)
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
            return f"- {site_name} [{site_key}]: 已检索 {retrieve_count} 个岗位，尝试投递 {attempted} 个，成功 {submitted} 个{suffix}。"
        if str(apply.get("status") or "") == "failed":
            return f"- {site_name} [{site_key}]: 已检索 {retrieve_count} 个岗位，投递阶段失败（{reason or 'apply_failed'}）。"
        if str(apply.get("status") or "") == "blocked":
            return f"- {site_name} [{site_key}]: 已检索 {retrieve_count} 个岗位，投递阶段阻塞（{reason or 'apply_blocked'}）。"
        return f"- {site_name} [{site_key}]: 已检索 {retrieve_count} 个岗位，未执行投递。"

    @classmethod
    def _ready_message_for_phase(cls, phase_slug: str, *, authenticated_ready: bool, jobs_surface_ready: bool) -> str:
        normalized = str(phase_slug or "").strip()
        if authenticated_ready:
            if normalized == "job_retrieval":
                return "登录已就绪，岗位检索已完成，等待后续投递。"
            if normalized == "job_filtering":
                return "登录已就绪，岗位筛选已完成，等待后续岗位检索。"
            if normalized == "channel_discovery":
                return "登录已就绪，岗位入口已定位，等待后续岗位检索。"
            return "登录已就绪，等待后续岗位检索。"
        if jobs_surface_ready:
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
    ) -> dict[str, Any]:
        return {
            "site_key": site_key,
            "site_name": site_name,
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
    ) -> dict[str, Any]:
        return {
            "site_key": site_key,
            "site_name": site_name,
            "status": "running",
            "reason_tag": "",
            "entry_url": entry_url,
            "skill_path": skill_path,
            "retrieve": {"status": "running", "count": 0},
            "apply": {
                "status": "pending" if allow_apply else "skipped",
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
    ) -> dict[str, Any]:
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
            if current_phase == "job_retrieval":
                return {
                    "site_key": site_key,
                    "site_name": site_name,
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
                    "status": "pending" if allow_apply else "skipped",
                    "attempted": 0,
                    "submitted": 0,
                },
            }
        if status == "blocked":
            return {
                "site_key": site_key,
                "site_name": site_name,
                "status": "blocked",
                "reason_tag": reason_tag,
                "message": message or f"{site_key} 需要先完成登录，关闭窗口后再回复 `{site_key} done`。",
                "entry_url": entry_url,
                "current_phase": current_phase,
                "current_url": current_url,
                "trace_ref": trace_ref,
                "step_count": step_count,
                "skill_path": skill_path,
                "retrieve": {"status": "blocked", "count": retrieved_count},
                "apply": {
                    "status": "pending" if allow_apply else "skipped",
                    "attempted": 0,
                    "submitted": 0,
                },
            }
        return {
            "site_key": site_key,
            "site_name": site_name,
            "status": "failed",
            "reason_tag": reason_tag or "browser_runtime_failed",
            "message": message,
            "entry_url": entry_url,
            "current_phase": current_phase,
            "current_url": current_url,
            "trace_ref": trace_ref,
            "step_count": step_count,
            "skill_path": skill_path,
            "retrieve": {"status": "failed", "count": retrieved_count, "error": message},
            "apply": {
                "status": "failed" if allow_apply else "skipped",
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
        if application_status in {"already_applied", "submitted", "apply_failed", "blocked"}:
            return application_status
        decision_status = str(row.get("decision_status") or "").strip().lower()
        if decision_status == "already_applied":
            return "already_applied"
        return ""

    def _apply_counters_from_run(self, site_key: str, batch_id: str) -> dict[str, int]:
        rows = self._merged_run_job_rows_for_batch(site_key, batch_id)
        counts = {
            "retrieved": len(rows),
            "attempted": 0,
            "submitted": 0,
            "already_applied": 0,
            "filtered_out": 0,
            "failed": 0,
            "blocked": 0,
        }
        for row in rows:
            decision_status = str(row.get("decision_status") or "").strip().lower()
            application_status = self._terminal_application_status(row)
            if application_status == "already_applied":
                counts["already_applied"] += 1
            if decision_status == "filtered_out":
                counts["filtered_out"] += 1
            if application_status in {"submitted", "apply_failed", "blocked"}:
                counts["attempted"] += 1
            if application_status == "submitted":
                counts["submitted"] += 1
            elif application_status == "apply_failed":
                counts["failed"] += 1
            elif application_status == "blocked":
                counts["blocked"] += 1
        return counts

    @staticmethod
    def _is_apply_row_terminal(row: dict[str, Any]) -> bool:
        decision_status = str(row.get("decision_status") or "").strip().lower()
        application_status = JobFlow._terminal_application_status(row)
        return decision_status in {"filtered_out", "already_applied"} or application_status in {
            "already_applied",
            "submitted",
            "apply_failed",
            "blocked",
        }

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
                "attempted": counters["attempted"],
                "submitted": counters["submitted"],
                "already_applied": counters["already_applied"],
                "filtered_out": counters["filtered_out"],
                "failed": counters["failed"],
                "blocked": counters["blocked"],
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
                "attempted": counters["attempted"],
                "submitted": counters["submitted"],
                "already_applied": counters["already_applied"],
                "filtered_out": counters["filtered_out"],
                "failed": counters["failed"],
                "blocked": counters["blocked"],
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

    def _apply_site_jobs(
        self,
        *,
        site_key: str,
        current: dict[str, Any],
        batch_id: str,
        session_id: str,
        turn_id: str,
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
        initial_pending_rows = self._pending_apply_rows(site_key, batch_id)
        site_phase_budget_seconds = self._apply_site_phase_budget_seconds(job_count=len(initial_pending_rows))
        site_phase_deadline = time.monotonic() + float(site_phase_budget_seconds or 0)
        while True:
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

            latest_rows = {str(row.get("job_id") or ""): row for row in self._run_job_rows(site_key, batch_id)}
            latest_row = latest_rows.get(job_id) or {}
            if self._is_apply_row_terminal(latest_row):
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
                    "attempted": counters["attempted"],
                    "submitted": counters["submitted"],
                    "already_applied": counters["already_applied"],
                    "filtered_out": counters["filtered_out"],
                    "failed": counters["failed"],
                    "blocked": counters["blocked"],
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
        apply["attempted"] = counters["attempted"]
        apply["submitted"] = counters["submitted"]
        apply["already_applied"] = counters["already_applied"]
        apply["filtered_out"] = counters["filtered_out"]
        apply["failed"] = counters["failed"]
        apply["blocked"] = counters["blocked"]
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

    def start_batch(self, *, session_id: str, turn_id: str, user_message: str, apply_requested: bool) -> str:
        active_sites = self.site_tools.site_store.list_sites("active")
        if not active_sites:
            return "当前没有已注册的 active sites。请先完成公司注册。"
        effective_apply_requested = bool(apply_requested and self.ENABLE_BROWSER_APPLY_PHASE)

        site_rows: list[dict[str, Any]] = []
        runnable_keys: list[str] = []
        for row in active_sites:
            site_key = str(row.get("site_key") or "")
            preflight = self.site_tools.preflight_site(site_key, apply_requested=effective_apply_requested)
            site_name = str(preflight.get("site_name") or row.get("canonical_company") or site_key)
            skill_path = str(preflight.get("skill_path") or "")
            entry_url = str(preflight.get("entry_url") or row.get("base_url") or "")
            allow_apply = bool(preflight.get("allow_apply"))
            preflight_status = str(preflight.get("status") or "failed")
            if preflight_status != "ready":
                site_rows.append(
                    {
                        "site_key": site_key,
                        "site_name": site_name,
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
                )
            )
            runnable_keys.append(site_key)

        batch = self.job_store.create_batch(
            session_id=session_id,
            turn_id=turn_id,
            user_message=user_message,
            apply_requested=effective_apply_requested,
            sites=site_rows,
        )
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
                batch = self.job_store.update_site(latest or batch, site_key, updated)
                batch["status"] = self._compute_batch_status(batch)
                batch = self.job_store.save_batch(batch)
                if generate_report:
                    self._generate_batch_report_if_possible(batch)
                return batch

        def _job(site_key: str, current: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            allow_apply = str((current.get("apply") or {}).get("status") or "") != "skipped"
            result = self._run_site_with_auth_recovery(
                site_key=site_key,
                site_name=str(current.get("site_name") or site_key),
                entry_url=str(current.get("entry_url") or ""),
                session_id=session_id,
                turn_id=turn_id,
                batch_id=batch_id,
                phase_slugs=self.DISCOVERY_PHASES,
            )
            updated = self._browser_result_to_site_row(result=result, existing=current, allow_apply=allow_apply)
            retrieval_done = str((updated.get("retrieve") or {}).get("status") or "") == "done"
            if retrieval_done:
                self._promote_retrieved_run_to_history(site_key=site_key, batch_id=batch_id)
                _save_site_snapshot(site_key, updated, generate_report=True)
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

        batch["status"] = self._compute_batch_status(batch)
        batch = self.job_store.save_batch(batch)
        self._generate_batch_report_if_possible(batch)
        return self._format_batch_summary(batch)

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
