"""Site workflow orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.storage.site_store import SiteStore
from careereng.tools.playwright_tools import PlaywrightSessionOpenError, PlaywrightTools
from careereng.utils import extract_markdown_section, now_iso, parse_front_matter


class SiteTools:
    def __init__(self, site_store: SiteStore, playwright: PlaywrightTools):
        self.site_store = site_store
        self.playwright = playwright
        self.project_root: Path | None = None

    def default_headless(self) -> bool:
        return bool(getattr(self.playwright, "headless", False))

    def keep_browser_open(self) -> bool:
        return bool(getattr(self.playwright, "keep_open", False))

    def _browser_closed_result(self, target_url: str = "") -> dict[str, Any]:
        return {
            "ok": False,
            "status": "browser_closed",
            "error": "browser_closed",
            "url": target_url,
        }

    def _site_skill_state(self, site_id: str) -> dict[str, Any]:
        skill = self.site_store.load_skill(site_id)
        meta = skill.get("front_matter") if isinstance(skill.get("front_matter"), dict) else {}
        status = str(meta.get("status") or "draft").strip().lower() or "draft"
        if status not in {"draft", "ready"}:
            status = "draft"
        return {
            "exists": bool(skill.get("exists")),
            "path": str(skill.get("path") or ""),
            "status": status,
            "apply_enabled": bool(meta.get("apply_enabled")),
            "allow_anonymous_discovery": bool(meta.get("allow_anonymous_discovery")),
            "front_matter": meta,
            "body": str(skill.get("body") or ""),
        }

    def _read_skill_body(self, path: Path) -> str:
        if not path.exists():
            return ""
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return ""
        _, body = parse_front_matter(text)
        return body

    def _project_search_core_text(self) -> str:
        root = self.project_root
        if not isinstance(root, Path):
            return ""
        return self._read_skill_body(root / "skills" / "search" / "SKILL.md")

    def _project_jobs_stage_text(self, stage_name: str) -> str:
        root = self.project_root
        if not isinstance(root, Path):
            return ""
        body = self._read_skill_body(root / "skills" / "search" / "jobs" / "SKILL.md")
        return extract_markdown_section(body, stage_name, level=2)

    def _site_stage_text(self, site_id: str, stage_name: str) -> str:
        skill = self.site_store.load_skill(site_id)
        body = str(skill.get("body") or "")
        return extract_markdown_section(body, stage_name, level=2)

    def _build_stage_guidance(self, site_id: str, stage_name: str) -> str:
        parts: list[str] = []
        core = self._project_search_core_text().strip()
        if core:
            parts.append("## Search Core\n" + core)
        jobs_stage = self._project_jobs_stage_text(stage_name).strip()
        if jobs_stage:
            parts.append("## Project Jobs Stage\n" + jobs_stage)
        site_stage = self._site_stage_text(site_id, stage_name).strip()
        if site_stage:
            parts.append("## Site Override\n" + site_stage)
        return "\n\n".join(part for part in parts if part)

    def _bullet_lines(self, text: str) -> list[str]:
        rows: list[str] = []
        for raw in str(text or "").splitlines():
            line = raw.strip()
            if not line.startswith(("- ", "* ")):
                continue
            value = line[2:].strip().strip("`").strip()
            if not value:
                continue
            rows.append(value)
        return rows

    def _stage_signal_list(
        self,
        site_id: str,
        stage_name: str,
        subsection_names: list[str],
        *,
        defaults: list[str] | None = None,
    ) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for source in (self._site_stage_text(site_id, stage_name), self._project_jobs_stage_text(stage_name)):
            if not source:
                continue
            for subsection in subsection_names:
                block = extract_markdown_section(source, subsection, level=3)
                for row in self._bullet_lines(block):
                    key = row.lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(row)
        for row in defaults or []:
            key = str(row).lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(str(row))
        return merged

    def build_signal_config(self, site_id: str) -> dict[str, list[str]]:
        return {
            "auth_positive": self._stage_signal_list(
                site_id,
                "Session Preparation",
                ["Visible Authenticated Signals", "Authenticated Signals"],
                defaults=["avatar", "account", "account menu", "profile", "profile menu", "my account", "candidate home"],
            ),
            "auth_negative": self._stage_signal_list(
                site_id,
                "Session Preparation",
                ["Visible Unauthenticated Signals", "Unauthenticated Signals", "Negative Signals"],
                defaults=[
                    "sign in",
                    "log in",
                    "login",
                    "continue with google",
                    "use another account",
                    "verification",
                    "verify",
                    "account picker",
                    "captcha",
                    "create profile",
                    "register",
                ],
            ),
            "auth_confirmation_actions": self._stage_signal_list(
                site_id,
                "Session Preparation",
                ["Optional Safe Confirmation Actions", "Safe Confirmation Actions"],
                defaults=["account", "profile", "avatar"],
            ),
            "auth_confirmation_signals": self._stage_signal_list(
                site_id,
                "Session Preparation",
                ["Authenticated Confirmation Signals", "Confirmation Signals"],
                defaults=["sign out", "log out", "my account", "view profile"],
            ),
            "channel_ready": self._stage_signal_list(
                site_id,
                "Channel Discovery",
                ["Channel Ready Signals", "Success Signal"],
                defaults=["search jobs", "job search", "filters", "jobs found", "job cards", "open positions"],
            ),
            "channel_negative": self._stage_signal_list(
                site_id,
                "Channel Discovery",
                ["Channel Negative Signals", "Negative Signals"],
                defaults=[
                    "action center",
                    "previous applications",
                    "application history",
                    "candidate home",
                    "marketing page",
                    "product navigation",
                ],
            ),
            "list_signals": self._stage_signal_list(
                site_id,
                "Channel Discovery",
                ["List Signals"],
                defaults=["location", "posted", "requisition", "job id", "employment type", "apply", "full time"],
            ),
            "detail_signals": self._stage_signal_list(
                site_id,
                "Channel Discovery",
                ["Detail Signals"],
                defaults=[
                    "job description",
                    "responsibilities",
                    "qualifications",
                    "requirements",
                    "what you'll do",
                    "职位描述",
                    "岗位职责",
                    "任职要求",
                    "工作内容",
                ],
            ),
        }

    def build_auto_login_config(self, site_id: str) -> dict[str, Any]:
        actions = self._stage_signal_list(
            site_id,
            "Session Preparation",
            ["Safe Auto Login Actions"],
            defaults=[],
        )
        allow_single_account_tile = False
        action_labels: list[str] = []
        for row in actions:
            lowered = str(row or "").strip().lower()
            if not lowered:
                continue
            if lowered == "single remembered account tile":
                allow_single_account_tile = True
                continue
            action_labels.append(str(row))
        manual_takeover_signals = self._stage_signal_list(
            site_id,
            "Session Preparation",
            ["Manual Takeover Signals"],
            defaults=[
                "password",
                "verification",
                "verify",
                "captcha",
                "mfa",
                "two-factor",
                "two factor",
                "one-time code",
                "security code",
                "check your email",
                "email verification",
            ],
        )
        return {
            "action_labels": action_labels,
            "allow_single_account_tile": allow_single_account_tile,
            "manual_takeover_signals": manual_takeover_signals,
            "max_attempts": 2,
        }

    def handle_site_request(
        self,
        *,
        site_name: str,
        base_url: str,
        apply_requested: bool,
        session_id: str,
        turn_id: str,
        source_type: str = "manual",
    ) -> dict[str, Any]:
        existing = self.site_store.find_site(site_name)
        existing_site_key = str(existing.get("site_key") or "") if isinstance(existing, dict) else ""
        skill_preexisted = bool(existing_site_key and self.site_store.has_skill(existing_site_key))
        site = self.site_store.register(site_name, base_url=base_url, source_type=source_type)
        site_id = str(site["site_id"])
        target_url = str(site.get("base_url") or "")
        skill_path, _ = self.site_store.ensure_skill_template(site_id)
        skill_template_created = not skill_preexisted
        session_payload = self.site_store.ensure_browser_session(site_id)
        self.site_store.append_event(
            site_id,
            "site.registered",
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "site_name": site_name,
                "base_url": target_url,
                "apply_requested": bool(apply_requested),
                "source_type": source_type,
                "skill_template_created": skill_template_created,
            },
        )
        return {
            "site_id": site_id,
            "site_name": str(site.get("canonical_company") or site_name),
            "raw_name": str(site.get("raw_name") or site_name),
            "base_url": target_url,
            "has_skill": self.site_store.has_skill(site_id),
            "skill_path": str(skill_path.relative_to(self.site_store.workspace)),
            "skill_template_created": skill_template_created,
            "apply_requested": apply_requested,
            "status": str(site.get("status") or "active"),
            "registry_id": str(site.get("registry_id") or ""),
            "source_type": source_type,
            "registration_only": True,
            "browser_profile_dir": session_payload.get("profile_dir") or "",
        }

    def preflight_site(self, site_id: str, *, apply_requested: bool) -> dict[str, Any]:
        row = self.site_store.find_site(site_id)
        if not row:
            return {
                "site_key": site_id,
                "site_name": site_id,
                "status": "failed",
                "reason_tag": "site_missing",
                "message": "site not found",
            }

        canonical = str(row.get("canonical_company") or site_id)
        entry_url = str(row.get("base_url") or "")
        status = str(row.get("status") or "active")
        skill = self._site_skill_state(site_id)
        session = self.site_store.ensure_browser_session(site_id)
        skill_path = skill.get("path") or ""
        apply_enabled = bool(skill.get("apply_enabled"))
        allow_apply = bool(apply_requested and apply_enabled)

        if status != "active":
            return {
                "site_key": site_id,
                "site_name": canonical,
                "status": "skipped",
                "reason_tag": "site_inactive",
                "message": "site is inactive",
                "entry_url": entry_url,
                "skill_path": skill_path,
            }
        if not entry_url:
            return {
                "site_key": site_id,
                "site_name": canonical,
                "status": "skipped",
                "reason_tag": "entry_url_missing",
                "message": "entry_url missing",
                "entry_url": entry_url,
                "skill_path": skill_path,
            }
        if not skill.get("exists"):
            return {
                "site_key": site_id,
                "site_name": canonical,
                "status": "skipped",
                "reason_tag": "skill_missing",
                "message": "site skill missing",
                "entry_url": entry_url,
                "skill_path": skill_path,
            }
        if skill.get("status") != "ready":
            return {
                "site_key": site_id,
                "site_name": canonical,
                "status": "skipped",
                "reason_tag": "skill_not_ready",
                "message": "site skill is not ready",
                "entry_url": entry_url,
                "skill_path": skill_path,
            }
        return {
            "site_key": site_id,
            "site_name": canonical,
            "status": "ready",
            "reason_tag": "",
            "message": "",
            "entry_url": entry_url,
            "skill_path": skill_path,
            "session_ready": bool(session.get("session_ready")),
            "apply_enabled": apply_enabled,
            "allow_apply": allow_apply,
        }

    def prepare_session(
        self,
        site_id: str,
        *,
        run_id: str = "",
        run_session=None,
        target_url: str = "",
    ) -> dict[str, Any]:
        row = self.site_store.find_site(site_id)
        if not row:
            return {"ok": False, "status": "failed", "reason_tag": "site_missing", "message": "site not found"}
        site_key = str(row.get("site_key") or site_id)
        entry_url = str(target_url or row.get("base_url") or "")
        session = self.site_store.ensure_browser_session(site_key)
        profile_dir = str(session.get("profile_dir") or self.site_store.browser_profile_dir(site_key))
        signal_config = self.build_signal_config(site_key)
        auto_login_config = self.build_auto_login_config(site_key)

        session_preparer = getattr(run_session, "prepare_session", None) if run_session is not None else None
        if callable(session_preparer):
            try:
                prepared = session_preparer(entry_url, signal_config=signal_config, auto_login_config=auto_login_config)
            except Exception:
                prepared = self._browser_closed_result(entry_url)
        else:
            preparer = getattr(self.playwright, "prepare_session_with_profile", None)
            if callable(preparer):
                prepared = preparer(
                    profile_dir,
                    entry_url,
                    signal_config=signal_config,
                    auto_login_config=auto_login_config,
                )
            else:
                validator = getattr(self.playwright, "inspect_authenticated_with_profile", None)
                if callable(validator):
                    prepared = validator(profile_dir, entry_url, signal_config=signal_config)
                else:
                    prepared = self.playwright.validate_session(profile_dir, entry_url)

        prepared_status = str(prepared.get("status") or ("authenticated" if prepared.get("ok") else "need_auth"))
        current_url = str(prepared.get("url") or entry_url)
        session_update = {
            "active_run_id": run_id,
            "last_validated_at": now_iso(),
            "last_validation_result": prepared_status,
        }
        if bool(prepared.get("ok")):
            session_update["session_ready"] = True
            session_update["browser_status"] = "running"
            session_update["last_manual_login_at"] = now_iso()
            session = self.site_store.save_browser_session(site_key, session_update)
            self.site_store.append_event(
                site_key,
                "browser.session_validated",
                {"run_id": run_id, "result": prepared},
            )
            return {
                "ok": True,
                "status": "authenticated",
                "session": session,
                "current_url": current_url,
                "auth_status": str(prepared.get("auth_status") or "authenticated"),
                "workflow_status": str(prepared.get("workflow_status") or ""),
                "message": "authentication is ready",
                "detail": prepared,
            }

        session_update["session_ready"] = False
        if str(prepared_status or "") == "browser_closed":
            session_update["browser_status"] = "stopped"
        session = self.site_store.save_browser_session(site_key, session_update)
        self.site_store.append_event(
            site_key,
            "browser.session_not_ready",
            {"run_id": run_id, "entry_url": entry_url, "result": prepared},
        )
        if prepared_status.lower() == "profile_locked":
            return {
                "ok": False,
                "status": "profile_locked",
                "session": session,
                "current_url": current_url,
                "auth_status": str(prepared.get("auth_status") or "ambiguous"),
                "workflow_status": str(prepared.get("workflow_status") or ""),
                "message": f"{site_key} 的登录浏览器还开着。请关闭该窗口后，再回复 `{site_key} done`。",
                "detail": prepared,
            }
        return {
            "ok": False,
            "status": "need_auth",
            "session": session,
            "current_url": current_url,
            "auth_status": str(prepared.get("auth_status") or "need_auth"),
            "workflow_status": str(prepared.get("workflow_status") or ""),
            "message": f"{site_key} 需要先完成登录，关闭窗口后再回复 `{site_key} done`。",
            "detail": prepared,
        }

    def open_site_run_session(
        self,
        site_id: str,
        *,
        force_profile: bool = False,
        target_url: str = "",
        headless_override: bool | None = None,
        allow_launch: bool = True,
        prefer_worker: bool = False,
    ):
        session = self.site_store.ensure_browser_session(site_id)
        profile_dir = str(session.get("profile_dir") or "")

        if (
            prefer_worker
            and profile_dir
            and isinstance(self.playwright, PlaywrightTools)
            and isinstance(self.project_root, Path)
        ):
            try:
                from careereng.site_worker import open_remote_site_session

                return open_remote_site_session(
                    project_root=self.project_root,
                    workspace=self.site_store.workspace,
                    site_key=site_id,
                    target_url=target_url,
                    headless=headless_override,
                )
            except Exception as exc:
                return {
                    "ok": False,
                    "status": "launch_failed",
                    "message": str(exc).strip() or "failed to open remote site session",
                    "detail": {
                        "target_url": target_url,
                        "profile_dir": profile_dir,
                        "headless": headless_override,
                        "prefer_worker": True,
                    },
                }

        opener = getattr(self.playwright, "open_site_session", None)
        if not callable(opener):
            return None
        if (force_profile or profile_dir) and profile_dir:
            try:
                return opener(
                    profile_dir=profile_dir,
                    target_url=target_url,
                    headless=headless_override,
                    allow_launch=allow_launch,
                )
            except PlaywrightSessionOpenError as exc:
                return {
                    "ok": False,
                    "status": exc.status,
                    "message": exc.message,
                    "detail": exc.detail,
                    "target_url": target_url,
                }
        try:
            return opener()
        except PlaywrightSessionOpenError as exc:
            return {
                "ok": False,
                "status": exc.status,
                "message": exc.message,
                "detail": exc.detail,
                "target_url": target_url,
            }

    def close_site_run_session(self, run_session) -> None:
        if run_session is None:
            return
        close = getattr(run_session, "close", None)
        if callable(close):
            close()

    def retrieve_jobs(
        self,
        site_id: str,
        *,
        session_id: str,
        turn_id: str,
        run_session=None,
        target_url: str = "",
    ) -> dict[str, Any]:
        row = self.site_store.find_site(site_id)
        if not row:
            return {"ok": False, "error": "site_missing", "jobs": []}
        site_key = str(row.get("site_key") or site_id)
        site_name = str(row.get("canonical_company") or site_id)
        entry_url = str(target_url or row.get("base_url") or "")
        session = self.site_store.ensure_browser_session(site_key)
        profile_dir = str(session.get("profile_dir") or "")
        guidance_text = self._build_stage_guidance(site_key, "Channel Discovery")
        signal_config = self.build_signal_config(site_key)
        auto_login_config = self.build_auto_login_config(site_key)
        if run_session is not None and hasattr(run_session, "discover_jobs_guided"):
            try:
                result = run_session.discover_jobs_guided(
                    entry_url,
                    guidance_text=guidance_text,
                    signal_config=signal_config,
                    auto_login_config=auto_login_config,
                    max_items=20,
                )
            except Exception:
                result = self._browser_closed_result(entry_url)
        elif run_session is not None and hasattr(run_session, "discover_jobs"):
            try:
                result = run_session.discover_jobs(entry_url, max_items=20)
            except Exception:
                result = self._browser_closed_result(entry_url)
        elif profile_dir and hasattr(self.playwright, "discover_jobs_with_profile_guided"):
            result = self.playwright.discover_jobs_with_profile_guided(
                profile_dir,
                entry_url,
                guidance_text=guidance_text,
                signal_config=signal_config,
                auto_login_config=auto_login_config,
                max_items=20,
            )
        elif profile_dir and hasattr(self.playwright, "discover_jobs_with_profile"):
            result = self.playwright.discover_jobs_with_profile(profile_dir, entry_url, max_items=20)
        elif hasattr(self.playwright, "discover_jobs_guided"):
            result = self.playwright.discover_jobs_guided(
                entry_url,
                guidance_text=guidance_text,
                signal_config=signal_config,
                auto_login_config=auto_login_config,
                max_items=20,
            )
        else:
            result = self.playwright.discover_jobs(entry_url, max_items=20)
        state = str(result.get("state") or "")
        if state == "need_auth":
            return {
                "ok": False,
                "error": "need_auth",
                "state": "need_auth",
                "jobs": [],
                "detail": result,
                "current_url": str(result.get("url") or entry_url),
            }
        if not bool(result.get("ok")):
            error_code = str(result.get("error") or result.get("status") or "retrieve_failed")
            self.site_store.append_event(
                site_key,
                "jobs.retrieve_failed",
                {"session_id": session_id, "turn_id": turn_id, "entry_url": entry_url, "detail": result},
            )
            return {
                "ok": False,
                "error": error_code,
                "jobs": [],
                "detail": result,
                "current_url": str(result.get("url") or entry_url),
            }

        raw_jobs = result.get("items") if isinstance(result.get("items"), list) else []
        jobs: list[dict[str, Any]] = []
        for job in raw_jobs:
            if not isinstance(job, dict):
                continue
            title = str(job.get("title") or "").strip()
            url = str(job.get("url") or "").strip()
            if not title or not url:
                continue
            jobs.append(
                {
                    "title": title,
                    "url": url,
                    "description": str(job.get("description") or "").strip(),
                    "card_text": str(job.get("card_text") or "").strip(),
                    "employer": site_name,
                    "company": site_name,
                    "discovery_site": site_key,
                    "location": str(job.get("location") or "").strip(),
                    "posted_at": str(job.get("posted_at") or "").strip(),
                    "posted_label": str(job.get("posted_label") or "").strip(),
                    "employment_type": str(job.get("employment_type") or "").strip(),
                    "match_label": str(job.get("match_label") or "").strip(),
                    "apply_state": str(job.get("apply_state") or "").strip(),
                }
            )
        stored_jobs = self.site_store.append_jobs(site_key, jobs, session_id=session_id, turn_id=turn_id)
        self.site_store.append_event(
            site_key,
            "jobs.retrieved",
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "entry_url": entry_url,
                "job_count": len(stored_jobs),
            },
        )
        return {
            "ok": True,
            "jobs": stored_jobs,
            "detail": result,
            "entry_url": entry_url,
            "current_url": str(result.get("url") or entry_url),
        }

    def apply_now(self, site_id: str, jobs: list[dict[str, Any]], session_id: str, turn_id: str, run_session=None) -> dict[str, Any]:
        skill_state = self._site_skill_state(site_id)
        if not skill_state.get("exists"):
            return {"ok": False, "error": "skill_missing", "applied": []}

        session = self.site_store.ensure_browser_session(site_id)
        profile_dir = str(session.get("profile_dir") or "")
        applications = []
        for job in jobs:
            url = str(job.get("url") or "")
            if not url:
                continue
            apply_state = str(job.get("apply_state") or "").strip().lower()
            if "view application" in apply_state:
                applications.append(
                    {
                        "site_id": site_id,
                        "title": str(job.get("title") or ""),
                        "url": url,
                        "job_id": str(job.get("job_id") or ""),
                        "canonical_job_id": str(job.get("canonical_job_id") or ""),
                        "employer": str(job.get("employer") or job.get("company") or ""),
                        "discovery_site": str(job.get("discovery_site") or ""),
                        "submission_site": site_id,
                        "submitted": False,
                        "status": "already_applied",
                        "detail": {"status": "already_applied", "apply_state": apply_state},
                    }
                )
                continue
            if run_session is not None and hasattr(run_session, "quick_apply"):
                try:
                    result = run_session.quick_apply(url)
                except Exception:
                    result = self._browser_closed_result(url)
            elif profile_dir and hasattr(self.playwright, "quick_apply_with_profile"):
                result = self.playwright.quick_apply_with_profile(profile_dir, url)
            else:
                result = self.playwright.quick_apply(url)
            explicit_status = str(result.get("status") or "")
            if explicit_status == "already_applied":
                status = "already_applied"
            else:
                submitted = bool(result.get("submitted")) if "submitted" in result else bool(result.get("ok") and result.get("clicked"))
                if submitted:
                    status = "submitted"
                elif explicit_status:
                    status = explicit_status
                else:
                    status = "apply_failed"
            applications.append(
                {
                    "site_id": site_id,
                    "title": str(job.get("title") or ""),
                    "url": url,
                    "job_id": str(job.get("job_id") or ""),
                    "canonical_job_id": str(job.get("canonical_job_id") or ""),
                    "employer": str(job.get("employer") or job.get("company") or ""),
                    "discovery_site": str(job.get("discovery_site") or ""),
                    "submission_site": site_id,
                    "submitted": status == "submitted",
                    "status": status,
                    "detail": result,
                }
            )

        self.site_store.append_applications(site_id, applications, session_id=session_id, turn_id=turn_id)
        self.site_store.update_job_application_outcomes(site_id, applications)
        self.site_store.append_event(site_id, "jobs.apply", {"count": len(applications)})
        return {"ok": True, "applied": applications}
