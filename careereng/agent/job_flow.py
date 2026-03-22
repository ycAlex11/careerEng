"""Batch retrieve/apply orchestration for registered sites."""

from __future__ import annotations

import concurrent.futures
import re
from pathlib import Path
from typing import Any

from careereng.storage.application_store import ApplicationStore
from careereng.storage.job_store import JobStore
from careereng.tools.site_tools import SiteTools
from careereng.utils import dump_front_matter


class JobFlow:
    def __init__(
        self,
        *,
        project_root: Path,
        job_store: JobStore,
        application_store: ApplicationStore,
        site_tools: SiteTools,
        search_strategy: Any,
        profile_store: Any,
        cv_store: Any,
        intent_store: Any,
        site_parallelism: int = 2,
    ):
        self.project_root = project_root
        self.job_store = job_store
        self.application_store = application_store
        self.site_tools = site_tools
        self.search_strategy = search_strategy
        self.profile_store = profile_store
        self.cv_store = cv_store
        self.intent_store = intent_store
        self.site_parallelism = max(1, int(site_parallelism or 1))

    def close(self) -> None:
        return None

    def _load_project_job_skill_text(self) -> str:
        path = self.project_root / "skills" / "search" / "jobs" / "SKILL.md"
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return ""

    def _load_site_skill_text(self, site_id: str) -> str:
        payload = self.site_tools.site_store.load_skill(site_id)
        if not bool(payload.get("exists")):
            return ""
        front_matter = payload.get("front_matter") if isinstance(payload.get("front_matter"), dict) else {}
        body = str(payload.get("body") or "")
        if front_matter:
            return dump_front_matter(front_matter, body)
        return body

    def _append_apply_rows(self, site_id: str, applied_rows: list[dict[str, Any]]) -> tuple[int, int, int]:
        submitted = 0
        attempted = 0
        already_applied = 0
        for row in applied_rows:
            if not isinstance(row, dict):
                continue
            ok = bool(row.get("submitted"))
            stage = str(row.get("status") or ("submitted" if ok else "apply_failed"))
            if stage == "already_applied":
                already_applied += 1
            else:
                attempted += 1
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
                    "decision_scope": "batch",
                    "auto_decision": "already_applied" if stage == "already_applied" else "apply",
                    "submitted": ok,
                    "stage": stage,
                    "stage_updated_at": row.get("ts") or "",
                    "error": row.get("detail", {}).get("error") if isinstance(row.get("detail"), dict) else "",
                }
            )
        return attempted, submitted, already_applied

    def _compute_batch_status(self, batch: dict[str, Any]) -> str:
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        rows = [row for row in sites.values() if isinstance(row, dict)]
        if any(str(row.get("status") or "") in {"queued", "running"} for row in rows):
            return "running"
        if any(str(row.get("status") or "") == "blocked_login" for row in rows):
            return "waiting_user"
        if any(
            str((row.get("apply") or {}).get("status") or "") in {"failed", "blocked"}
            or str((row.get("retrieve") or {}).get("status") or "") == "failed"
            or str(row.get("status") or "") in {"failed", "skipped"}
            for row in rows
        ):
            return "partial_completed"
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
        if status == "blocked_login":
            message = str(row.get("message") or "")
            if message:
                return f"- {site_name} [{site_key}]: {message}"
            return f"- {site_name} [{site_key}]: 已打开登录浏览器，完成登录后回复 `{site_key} done` 继续。"
        if status == "skipped":
            skill_path = str(row.get("skill_path") or "")
            suffix = f" 请补充 {skill_path}。" if skill_path else ""
            return f"- {site_name} [{site_key}]: 已跳过（{reason or 'preflight_skip'}）。{suffix}".rstrip()
        if str(retrieve.get("status") or "") == "failed":
            return f"- {site_name} [{site_key}]: 岗位检索失败（{reason or 'retrieve_failed'}）。"
        retrieve_count = int(retrieve.get("count") or 0)
        apply_status = str(apply.get("status") or "skipped")
        if apply_status == "done":
            submitted = int(apply.get("submitted") or 0)
            attempted = int(apply.get("attempted") or 0)
            already_applied = int(apply.get("already_applied") or 0)
            suffix = f"，已存在申请 {already_applied} 个" if already_applied else ""
            return f"- {site_name} [{site_key}]: 已检索 {retrieve_count} 个岗位，尝试投递 {attempted} 个，成功 {submitted} 个{suffix}。"
        if apply_status in {"failed", "blocked"}:
            return f"- {site_name} [{site_key}]: 已检索 {retrieve_count} 个岗位，但未完成投递（{reason or 'manual_guidance_needed'}）。"
        return f"- {site_name} [{site_key}]: 已检索 {retrieve_count} 个岗位，未执行投递。"

    def _format_batch_summary(self, batch: dict[str, Any]) -> str:
        status = str(batch.get("status") or "unknown")
        lines = [f"batch={batch.get('batch_id')} status={status}"]
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        for site_key in sorted(sites.keys()):
            row = sites.get(site_key)
            if isinstance(row, dict):
                lines.append(self._format_site_line(row))
        return "\n".join(lines)

    def _default_headless(self) -> bool:
        getter = getattr(self.site_tools, "default_headless", None)
        if callable(getter):
            return bool(getter())
        return bool(getattr(self.site_tools.playwright, "headless", False))

    def _keep_browser_open(self) -> bool:
        getter = getattr(self.site_tools, "keep_browser_open", None)
        if callable(getter):
            return bool(getter())
        return bool(getattr(self.site_tools.playwright, "keep_open", False))

    def _save_site_progress(
        self,
        site_key: str,
        *,
        phase: str | None = None,
        pending_action: str | None = None,
        current_url: str | None = None,
        current_job_id: str | None = None,
        current_job_url: str | None = None,
        visible: bool | None = None,
        browser_status: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {}
        if phase is not None:
            payload["resume_phase"] = str(phase or "idle")
        if pending_action is not None:
            payload["pending_action"] = str(pending_action or "")
        if current_url is not None:
            payload["last_known_url"] = str(current_url or "")
        if current_job_id is not None:
            payload["current_job_id"] = str(current_job_id or "")
        if current_job_url is not None:
            payload["current_job_url"] = str(current_job_url or "")
        if visible is not None:
            payload["visible_mode"] = "visible" if bool(visible) else "headless"
        if browser_status is not None:
            payload["browser_status"] = str(browser_status or "stopped")
        if payload:
            self.site_tools.site_store.save_browser_session(site_key, payload)

    def _load_site_progress(self, site_key: str) -> dict[str, Any]:
        return self.site_tools.site_store.ensure_browser_session(site_key)

    def _target_url_for_row(self, row: dict[str, Any]) -> str:
        site_key = str(row.get("site_key") or "")
        progress = self._load_site_progress(site_key) if site_key else {}
        return str(row.get("current_url") or progress.get("last_known_url") or row.get("entry_url") or "")

    def _manual_login_message(self, site_key: str, status: str, fallback: str = "") -> str:
        normalized = str(status or "")
        if normalized == "profile_locked":
            return f"{site_key} 浏览器配置正在被占用。请关闭现有窗口后再回复 `{site_key} done`。"
        if normalized in {"launch_failed", "session_open_failed", "browser_open_failed", "navigate_failed", "browser_not_running"}:
            detail = f" {fallback}" if fallback else ""
            return f"{site_key} 浏览器打开失败。{detail}".rstrip()
        return fallback or f"已为 {site_key} 打开登录浏览器。请完成登录后关闭窗口，再回复 `{site_key} done`。"

    def _need_auth_result(
        self,
        *,
        site_key: str,
        site_name: str,
        allow_apply: bool,
        reason_tag: str,
        message: str,
        current_url: str,
    ) -> dict[str, Any]:
        return {
            "site_key": site_key,
            "site_name": site_name,
            "status": "need_auth",
            "reason_tag": reason_tag or "need_auth",
            "message": message or f"已为 {site_key} 打开登录浏览器。请完成登录后关闭窗口，再回复 `{site_key} done`。",
            "current_url": current_url,
            "skill_path": self.site_tools._site_skill_state(site_key).get("path") or "",
            "retrieve": {"status": "blocked", "count": 0, "error": reason_tag or "need_auth"},
            "apply": {"status": "pending" if allow_apply else "skipped", "attempted": 0, "submitted": 0},
        }

    def _open_site_session(self, site_key: str, *, target_url: str, visible: bool):
        session = self.site_tools.open_site_run_session(
            site_key,
            force_profile=True,
            target_url=target_url,
            headless_override=not visible,
            allow_launch=True,
            prefer_worker=True,
        )
        if isinstance(session, dict) and not bool(session.get("ok", True)):
            return None, {
                "status": str(session.get("status") or "session_open_failed"),
                "message": str(session.get("message") or ""),
                "detail": session.get("detail") if isinstance(session.get("detail"), dict) else {},
            }
        if session is None:
            return None, {"status": "session_open_failed", "message": "", "detail": {}}
        return session, {}

    def _open_manual_login_browser(
        self,
        *,
        site_key: str,
        site_name: str,
        target_url: str,
        allow_apply: bool,
        turn_id: str,
    ) -> dict[str, Any]:
        run_session, open_error = self._open_site_session(site_key, target_url=target_url, visible=True)
        if run_session is not None:
            self._save_site_progress(
                site_key,
                phase="prepare_session",
                pending_action="wait_user_login",
                current_url=target_url,
                visible=True,
                browser_status="running",
            )
            return self._need_auth_result(
                site_key=site_key,
                site_name=site_name,
                allow_apply=allow_apply,
                reason_tag="need_auth_browser_open",
                message=f"已为 {site_key} 打开登录浏览器。请完成登录后关闭窗口，再回复 `{site_key} done`。",
                current_url=target_url,
            )
        status = str(open_error.get("status") or "browser_open_failed")
        self._save_site_progress(
            site_key,
            phase="prepare_session",
            pending_action="wait_user_login",
            current_url=target_url,
            visible=True,
            browser_status="stopped",
        )
        return self._need_auth_result(
            site_key=site_key,
            site_name=site_name,
            allow_apply=allow_apply,
            reason_tag=status,
            message=self._manual_login_message(site_key, status, str(open_error.get("message") or "").strip()),
            current_url=target_url,
        )

    def _run_single_site(
        self,
        site_key: str,
        *,
        session_id: str,
        turn_id: str,
        allow_apply: bool,
        start_url: str = "",
        force_visible: bool = False,
    ) -> dict[str, Any]:
        row = self.site_tools.site_store.find_site(site_key) or {}
        site_name = str(row.get("canonical_company") or site_key)
        entry_url = str(start_url or row.get("base_url") or "")
        current_visible = bool(force_visible or not self._default_headless())
        keep_session = bool(current_visible or self._keep_browser_open())

        self._save_site_progress(
            site_key,
            phase="prepare_session",
            pending_action="",
            current_url=entry_url,
            visible=current_visible,
            browser_status="starting",
        )

        run_session, open_error = self._open_site_session(site_key, target_url=entry_url, visible=current_visible)
        if run_session is None:
            self._save_site_progress(
                site_key,
                phase="prepare_session",
                pending_action="wait_user_login",
                current_url=entry_url,
                visible=current_visible,
                browser_status="stopped",
            )
            return self._need_auth_result(
                site_key=site_key,
                site_name=site_name,
                allow_apply=allow_apply,
                reason_tag=str(open_error.get("status") or "browser_open_failed"),
                message=self._manual_login_message(
                    site_key,
                    str(open_error.get("status") or "browser_open_failed"),
                    str(open_error.get("message") or "").strip(),
                ),
                current_url=entry_url,
            )

        prepared = self.site_tools.prepare_session(
            site_key,
            run_id=turn_id,
            run_session=run_session,
            target_url=entry_url,
        )
        if not bool(prepared.get("ok")):
            current_url = str(prepared.get("current_url") or entry_url)
            if current_visible:
                self._save_site_progress(
                    site_key,
                    phase="prepare_session",
                    pending_action="wait_user_login",
                    current_url=current_url,
                    visible=True,
                    browser_status="running",
                )
                return self._need_auth_result(
                    site_key=site_key,
                    site_name=site_name,
                    allow_apply=allow_apply,
                    reason_tag=str(prepared.get("status") or "need_auth"),
                    message=f"已为 {site_key} 打开登录浏览器。请完成登录后关闭窗口，再回复 `{site_key} done`。",
                    current_url=current_url,
                )
            self.site_tools.close_site_run_session(run_session)
            return self._open_manual_login_browser(
                site_key=site_key,
                site_name=site_name,
                target_url=current_url,
                allow_apply=allow_apply,
                turn_id=turn_id,
            )

        active_url = str(prepared.get("current_url") or entry_url)
        self._save_site_progress(
            site_key,
            phase="channel_discovery",
            pending_action="",
            current_url=active_url,
            visible=current_visible,
            browser_status="running",
        )

        retrieved = self.site_tools.retrieve_jobs(
            site_key,
            session_id=session_id,
            turn_id=turn_id,
            run_session=run_session,
            target_url=active_url,
        )
        if not bool(retrieved.get("ok")):
            error_code = str(retrieved.get("error") or "")
            current_url = str(retrieved.get("current_url") or active_url)
            if error_code in {"need_auth", "browser_closed"}:
                if current_visible:
                    self._save_site_progress(
                        site_key,
                        phase="channel_discovery",
                        pending_action="wait_user_login",
                        current_url=current_url,
                        visible=True,
                        browser_status="running" if error_code != "browser_closed" else "stopped",
                    )
                    return self._need_auth_result(
                        site_key=site_key,
                        site_name=site_name,
                        allow_apply=allow_apply,
                        reason_tag=error_code or "need_auth",
                        message=f"已为 {site_key} 打开登录浏览器。请完成登录后关闭窗口，再回复 `{site_key} done`。",
                        current_url=current_url,
                    )
                self.site_tools.close_site_run_session(run_session)
                return self._open_manual_login_browser(
                    site_key=site_key,
                    site_name=site_name,
                    target_url=current_url,
                    allow_apply=allow_apply,
                    turn_id=turn_id,
                )
            if not keep_session:
                self.site_tools.close_site_run_session(run_session)
            self._save_site_progress(
                site_key,
                phase="channel_discovery",
                pending_action="retrieve_failed",
                current_url=current_url,
                visible=current_visible,
                browser_status="running" if keep_session else "stopped",
            )
            return {
                "site_key": site_key,
                "site_name": site_name,
                "status": "failed",
                "reason_tag": "retrieve_failed",
                "current_url": current_url,
                "skill_path": self.site_tools._site_skill_state(site_key).get("path") or "",
                "retrieve": {"status": "failed", "count": 0, "error": error_code},
                "apply": {"status": "skipped", "attempted": 0, "submitted": 0},
            }

        jobs = [j for j in (retrieved.get("jobs") or []) if isinstance(j, dict)]
        result = {
            "site_key": site_key,
            "site_name": site_name,
            "status": "done",
            "reason_tag": "",
            "current_url": str(retrieved.get("current_url") or active_url),
            "skill_path": self.site_tools._site_skill_state(site_key).get("path") or "",
            "retrieve": {"status": "done", "count": len(jobs)},
            "apply": {"status": "skipped", "attempted": 0, "submitted": 0},
        }
        if not allow_apply or not jobs:
            if not keep_session:
                self.site_tools.close_site_run_session(run_session)
            self._save_site_progress(
                site_key,
                phase="complete",
                pending_action="",
                current_url=result["current_url"],
                visible=current_visible,
                browser_status="running" if keep_session else "stopped",
                current_job_id="",
                current_job_url="",
            )
            return result

        persona = self.profile_store.load_doc()
        intent = self.intent_store.load_doc()
        cv_text = self.cv_store.load_current_text()
        project_job_skill_text = self._load_project_job_skill_text()
        site_job_skill_text = self._load_site_skill_text(site_key)
        chosen = self.search_strategy.evaluate_jobs_for_apply(
            site_name=site_name,
            jobs=jobs,
            persona=persona,
            intent=intent,
            cv_text=cv_text,
            project_job_skill_text=project_job_skill_text,
            site_job_skill_text=site_job_skill_text,
        )
        self.site_tools.site_store.update_job_decisions(site_key, jobs)
        chosen_ids = {str(j.get("job_id") or "") for j in chosen if isinstance(j, dict)}
        for job in jobs:
            if not isinstance(job, dict):
                continue
            self.application_store.append_event(
                "fit.evaluated",
                {
                    "site_name": site_name,
                    "job_id": str(job.get("job_id") or ""),
                    "title": job.get("title"),
                    "confidence": float(job.get("fit_confidence") or 0.0),
                    "apply": str(job.get("job_id") or "") in chosen_ids,
                    "reason": str(job.get("fit_reason") or "strategy_engine"),
                },
            )
        first_job = chosen[0] if chosen else {}
        self._save_site_progress(
            site_key,
            phase="apply",
            pending_action="",
            current_url=result["current_url"],
            current_job_id=str(first_job.get("job_id") or ""),
            current_job_url=str(first_job.get("url") or ""),
            visible=current_visible,
            browser_status="running",
        )

        apply_result = self.site_tools.apply_now(
            site_key,
            chosen,
            session_id=session_id,
            turn_id=turn_id,
            run_session=run_session,
        )
        if not keep_session:
            self.site_tools.close_site_run_session(run_session)
        applied_rows = apply_result.get("applied") if isinstance(apply_result.get("applied"), list) else []
        attempted, submitted, already_applied = self._append_apply_rows(site_key, applied_rows)
        if attempted == 0 and already_applied > 0:
            result["apply"] = {
                "status": "done",
                "attempted": 0,
                "submitted": 0,
                "already_applied": already_applied,
            }
            self._save_site_progress(
                site_key,
                phase="complete",
                pending_action="",
                current_url=result["current_url"],
                current_job_id="",
                current_job_url="",
                visible=current_visible,
                browser_status="running" if keep_session else "stopped",
            )
            return result
        if attempted == 0:
            result["apply"] = {
                "status": "blocked",
                "attempted": 0,
                "submitted": 0,
                "reason_tag": "manual_guidance_needed",
            }
            result["reason_tag"] = "manual_guidance_needed"
            self._save_site_progress(
                site_key,
                phase="apply",
                pending_action="manual_guidance_needed",
                current_url=result["current_url"],
                visible=current_visible,
                browser_status="running" if keep_session else "stopped",
            )
            return result
        if submitted == 0:
            result["apply"] = {
                "status": "failed",
                "attempted": attempted,
                "submitted": submitted,
                "reason_tag": "manual_guidance_needed",
            }
            result["reason_tag"] = "manual_guidance_needed"
            self._save_site_progress(
                site_key,
                phase="apply",
                pending_action="manual_guidance_needed",
                current_url=result["current_url"],
                visible=current_visible,
                browser_status="running" if keep_session else "stopped",
            )
            return result
        result["apply"] = {
            "status": "done",
            "attempted": attempted,
            "submitted": submitted,
            "already_applied": already_applied,
        }
        self._save_site_progress(
            site_key,
            phase="complete",
            pending_action="",
            current_url=result["current_url"],
            current_job_id="",
            current_job_url="",
            visible=current_visible,
            browser_status="running" if keep_session else "stopped",
        )
        return result

    def start_batch(self, *, session_id: str, turn_id: str, user_message: str, apply_requested: bool) -> str:
        active_sites = self.site_tools.site_store.list_sites("active")
        if not active_sites:
            return "当前没有已注册的 active sites。请先完成公司注册。"

        site_rows: list[dict[str, Any]] = []
        runnable: list[tuple[str, bool]] = []
        for row in active_sites:
            site_key = str(row.get("site_key") or "")
            preflight = self.site_tools.preflight_site(site_key, apply_requested=apply_requested)
            site_row = {
                "site_key": site_key,
                "site_name": str(preflight.get("site_name") or row.get("canonical_company") or site_key),
                "status": str(preflight.get("status") or "queued"),
                "reason_tag": str(preflight.get("reason_tag") or ""),
                "entry_url": str(preflight.get("entry_url") or row.get("base_url") or ""),
                "skill_path": str(preflight.get("skill_path") or ""),
                "retrieve": {"status": "pending", "count": 0},
                "apply": {
                    "status": "pending" if bool(preflight.get("allow_apply")) else "skipped",
                    "attempted": 0,
                    "submitted": 0,
                },
            }
            if site_row["status"] == "ready":
                site_row["status"] = "queued"
                runnable.append((site_key, bool(preflight.get("allow_apply"))))
            site_rows.append(site_row)

        batch = self.job_store.create_batch(
            session_id=session_id,
            turn_id=turn_id,
            user_message=user_message,
            apply_requested=apply_requested,
            sites=site_rows,
        )

        if runnable:
            use_parallel = len(runnable) > 1
            if use_parallel:
                workers = min(max(1, len(runnable)), max(1, self.site_parallelism))
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                    future_map = {}
                    for site_key, allow_apply in runnable:
                        batch = self.job_store.update_site(batch, site_key, {"status": "running"})
                        future_map[
                            pool.submit(
                                self._run_single_site,
                                site_key,
                                session_id=session_id,
                                turn_id=turn_id,
                                allow_apply=allow_apply,
                            )
                        ] = site_key
                    for future in concurrent.futures.as_completed(future_map):
                        site_key = future_map[future]
                        try:
                            site_result = future.result()
                        except Exception as exc:
                            site_result = {
                                "site_key": site_key,
                                "site_name": site_key,
                                "status": "failed",
                                "reason_tag": "executor_error",
                                "retrieve": {"status": "failed", "count": 0, "error": str(exc)},
                                "apply": {"status": "skipped", "attempted": 0, "submitted": 0},
                            }
                        if str(site_result.get("status") or "") == "need_auth":
                            site_result["status"] = "blocked_login"
                        batch = self.job_store.update_site(batch, site_key, site_result)
                        self.job_store.append_event(
                            "site.finished",
                            {
                                "batch_id": batch.get("batch_id"),
                                "site_key": site_key,
                                "status": site_result.get("status"),
                                "retrieve": site_result.get("retrieve"),
                                "apply": site_result.get("apply"),
                            },
                        )
            else:
                for site_key, allow_apply in runnable:
                    batch = self.job_store.update_site(batch, site_key, {"status": "running"})
                    site_result = self._run_single_site(
                        site_key,
                        session_id=session_id,
                        turn_id=turn_id,
                        allow_apply=allow_apply,
                    )
                    if str(site_result.get("status") or "") == "need_auth":
                        site_result["status"] = "blocked_login"
                    batch = self.job_store.update_site(batch, site_key, site_result)
                    self.job_store.append_event(
                        "site.finished",
                        {
                            "batch_id": batch.get("batch_id"),
                            "site_key": site_key,
                            "status": site_result.get("status"),
                            "retrieve": site_result.get("retrieve"),
                            "apply": site_result.get("apply"),
                        },
                    )

        batch["status"] = self._compute_batch_status(batch)
        batch = self.job_store.save_batch(batch)
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
        site_key, decision = parsed
        batch = self.job_store.latest_open_batch(session_id)
        if not batch:
            return None
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        current = sites.get(site_key)
        if not isinstance(current, dict):
            return None
        if str(current.get("status") or "") != "blocked_login":
            return None

        if decision in {"n", "no", "否", "取消"}:
            self._save_site_progress(
                site_key,
                phase="idle",
                pending_action="",
                current_job_id="",
                current_job_url="",
                browser_status="stopped",
            )
            batch = self.job_store.update_site(
                batch,
                site_key,
                {
                    "status": "skipped",
                    "reason_tag": "user_declined_login",
                    "retrieve": {"status": "skipped", "count": 0},
                    "apply": {"status": "skipped", "attempted": 0, "submitted": 0},
                },
            )
            batch["status"] = self._compute_batch_status(batch)
            batch = self.job_store.save_batch(batch)
            return self._format_batch_summary(batch)

        target_url = self._target_url_for_row(current)
        allow_apply = str((current.get("apply") or {}).get("status") or "") != "skipped"
        site_result = self._run_single_site(
            site_key,
            session_id=session_id,
            turn_id=turn_id,
            allow_apply=allow_apply,
            start_url=target_url,
            force_visible=True,
        )
        if str(site_result.get("status") or "") == "need_auth":
            site_result["status"] = "blocked_login"
        batch = self.job_store.update_site(batch, site_key, site_result)
        batch["status"] = self._compute_batch_status(batch)
        batch = self.job_store.save_batch(batch)
        return self._format_batch_summary(batch)
