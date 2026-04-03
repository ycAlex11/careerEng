"""Batch retrieve/apply orchestration for registered sites."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from careereng.storage.application_store import ApplicationStore
from careereng.storage.job_store import JobStore
from careereng.tools.site_tools import SiteTools


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
        if status == "skipped":
            skill_path = str(row.get("skill_path") or "")
            suffix = f" 请补充 {skill_path}。" if skill_path else ""
            return f"- {site_name} [{site_key}]: 已跳过（{reason or 'preflight_skip'}）。{suffix}".rstrip()
        if status == "blocked_login":
            message = str(row.get("message") or "")
            if message:
                return f"- {site_name} [{site_key}]: {message}"
            return f"- {site_name} [{site_key}]: 浏览器自动化已禁用。"
        if str(retrieve.get("status") or "") == "failed":
            return f"- {site_name} [{site_key}]: 岗位检索失败（{reason or 'retrieve_failed'}）。"
        retrieve_count = int(retrieve.get("count") or 0)
        if str(apply.get("status") or "") == "done":
            submitted = int(apply.get("submitted") or 0)
            attempted = int(apply.get("attempted") or 0)
            already_applied = int(apply.get("already_applied") or 0)
            suffix = f"，已存在申请 {already_applied} 个" if already_applied else ""
            return f"- {site_name} [{site_key}]: 已检索 {retrieve_count} 个岗位，尝试投递 {attempted} 个，成功 {submitted} 个{suffix}。"
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

    def start_batch(self, *, session_id: str, turn_id: str, user_message: str, apply_requested: bool) -> str:
        active_sites = self.site_tools.site_store.list_sites("active")
        if not active_sites:
            return "当前没有已注册的 active sites。请先完成公司注册。"

        site_rows: list[dict[str, Any]] = []
        for row in active_sites:
            site_key = str(row.get("site_key") or "")
            preflight = self.site_tools.preflight_site(site_key, apply_requested=apply_requested)
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
                self._disabled_site_row(
                    site_key=site_key,
                    site_name=site_name,
                    entry_url=entry_url,
                    skill_path=skill_path,
                    allow_apply=allow_apply,
                )
            )

        batch = self.job_store.create_batch(
            session_id=session_id,
            turn_id=turn_id,
            user_message=user_message,
            apply_requested=apply_requested,
            sites=site_rows,
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
        site_key, _decision = parsed
        batch = self.job_store.latest_open_batch(session_id)
        if not batch:
            return None
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        current = sites.get(site_key)
        if not isinstance(current, dict):
            return None
        if str(current.get("status") or "") != "blocked_login":
            return None
        replacement = self._disabled_site_row(
            site_key=site_key,
            site_name=str(current.get("site_name") or site_key),
            entry_url=str(current.get("entry_url") or current.get("current_url") or ""),
            skill_path=str(current.get("skill_path") or ""),
            allow_apply=str((current.get("apply") or {}).get("status") or "") != "skipped",
        )
        batch = self.job_store.update_site(batch, site_key, replacement)
        batch["status"] = self._compute_batch_status(batch)
        batch = self.job_store.save_batch(batch)
        return self._format_batch_summary(batch)
