"""Site workflow orchestration."""

from __future__ import annotations

from typing import Any

from careereng.storage.site_store import SiteStore
from careereng.tools.playwright_tools import PlaywrightTools


class SiteTools:
    def __init__(self, site_store: SiteStore, playwright: PlaywrightTools):
        self.site_store = site_store
        self.playwright = playwright

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
        site = self.site_store.register(site_name, base_url=base_url, source_type=source_type)
        site_id = str(site["site_id"])
        target_url = str(site.get("base_url") or "")
        skill_path, skill_template_created = self.site_store.ensure_skill_template(site_id)
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
        }

    def apply_now(self, site_id: str, jobs: list[dict[str, Any]], session_id: str, turn_id: str) -> dict[str, Any]:
        if not self.site_store.has_skill(site_id):
            return {"ok": False, "error": "skill_missing", "applied": []}

        applications = []
        for job in jobs[:3]:
            url = str(job.get("url") or "")
            if not url:
                continue
            result = self.playwright.quick_apply(url)
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
                    "submitted": bool(result.get("ok") and result.get("clicked")),
                    "detail": result,
                }
            )

        self.site_store.append_applications(site_id, applications, session_id=session_id, turn_id=turn_id)
        self.site_store.append_event(site_id, "jobs.apply", {"count": len(applications)})
        return {"ok": True, "applied": applications}
