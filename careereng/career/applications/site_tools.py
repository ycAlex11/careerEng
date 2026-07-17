"""Lightweight site registration and preflight helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.evolution.work_items import create_site_skill_bootstrap_card
from careereng.career.resume.export import default_apply_resume_pdf_path, ensure_default_resume_pdf
from careereng.career.applications.site_store import SiteStore


class SiteTools:
    def __init__(self, site_store: SiteStore):
        self.site_store = site_store
        self.project_root: Path | None = None
        self.playwright = None

    def default_headless(self) -> bool:
        return False

    def keep_browser_open(self) -> bool:
        return False

    def default_resume_pdf_path(self) -> Path:
        return default_apply_resume_pdf_path(self.site_store.workspace)

    def ensure_default_resume_pdf(self) -> Path:
        return ensure_default_resume_pdf(self.site_store.workspace)

    def _display_path(self, path: Path | str) -> str:
        if not str(path or "").strip():
            return ""
        resolved = Path(path)
        for base in (self.project_root, getattr(self.site_store, "project_root", None), self.site_store.workspace):
            if not base:
                continue
            try:
                return str(resolved.relative_to(Path(base)))
            except ValueError:
                continue
        return str(resolved)

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
        skill_state = self._site_skill_state(site_id)
        action_card: dict[str, Any] = {}
        if skill_template_created or str(skill_state.get("status") or "draft") == "draft":
            action_card = create_site_skill_bootstrap_card(
                workspace=self.site_store.workspace,
                project_root=self.project_root or self.site_store.project_root,
                site_key=site_id,
                site_name=str(site.get("canonical_company") or site_name),
                base_url=target_url,
                skill_path=skill_path,
                registry_id=str(site.get("registry_id") or ""),
            )
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
                "action_card_id": action_card.get("card_id") or "",
            },
        )
        return {
            "site_id": site_id,
            "site_name": str(site.get("canonical_company") or site_name),
            "raw_name": str(site.get("raw_name") or site_name),
            "base_url": target_url,
            "has_skill": self.site_store.has_skill(site_id),
            "skill_path": self._display_path(skill_path),
            "skill_template_created": skill_template_created,
            "action_card_id": str(action_card.get("card_id") or ""),
            "action_card_path": self._display_path(
                self.site_store.workspace / str(action_card.get("markdown_path") or "")
            )
            if action_card.get("markdown_path")
            else "",
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
        skill_path = self._display_path(skill.get("path") or "")
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
            "authenticated_ready": bool(session.get("authenticated_ready") or session.get("session_ready")),
            "jobs_surface_ready": bool(session.get("jobs_surface_ready")),
            "apply_enabled": apply_enabled,
            "allow_apply": allow_apply,
        }

    def _disabled_result(self, *, target_url: str = "") -> dict[str, Any]:
        return {
            "ok": False,
            "status": "browser_automation_disabled",
            "error": "browser_automation_disabled",
            "message": "browser automation is disabled in the current build",
            "current_url": target_url,
            "url": target_url,
            "jobs": [],
            "applied": [],
        }

    def prepare_session(
        self,
        site_id: str,
        *,
        run_id: str = "",
        run_session=None,
        target_url: str = "",
    ) -> dict[str, Any]:
        return self._disabled_result(target_url=target_url)

    def open_site_run_session(
        self,
        site_id: str,
        *,
        force_profile: bool = False,
        target_url: str = "",
        headless_override: bool | None = None,
        allow_launch: bool = True,
        prefer_worker: bool = True,
    ):
        return self._disabled_result(target_url=target_url)

    def close_site_run_session(self, run_session) -> None:
        return None

    def retrieve_jobs(
        self,
        site_id: str,
        *,
        session_id: str,
        turn_id: str,
        run_session=None,
        target_url: str = "",
    ) -> dict[str, Any]:
        return self._disabled_result(target_url=target_url)

    def apply_now(
        self,
        site_id: str,
        jobs: list[dict[str, Any]],
        session_id: str,
        turn_id: str,
        run_session=None,
    ) -> dict[str, Any]:
        return self._disabled_result()
