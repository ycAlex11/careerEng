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
    ) -> dict[str, Any]:
        site = self.site_store.register(site_name, base_url=base_url)
        site_id = str(site["site_id"])
        target_url = base_url or str(site.get("base_url") or f"https://{site_id}.com")

        search_result = self.playwright.discover_jobs(target_url, max_items=20)
        jobs = search_result.get("items", []) if isinstance(search_result, dict) else []
        if not isinstance(jobs, list):
            jobs = []

        normalized_jobs = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            normalized_jobs.append(
                {
                    "title": str(job.get("title") or ""),
                    "url": str(job.get("url") or ""),
                    "company": site_name,
                }
            )

        self.site_store.append_jobs(site_id, normalized_jobs, session_id=session_id, turn_id=turn_id)
        self.site_store.append_event(
            site_id,
            "jobs.search",
            {
                "target_url": target_url,
                "jobs_count": len(normalized_jobs),
                "search_ok": bool(search_result.get("ok")) if isinstance(search_result, dict) else False,
                "search_error": str(search_result.get("error") or "") if isinstance(search_result, dict) else "",
            },
        )

        has_skill = self.site_store.has_skill(site_id)
        return {
            "site_id": site_id,
            "site_name": site_name,
            "target_url": target_url,
            "jobs": normalized_jobs,
            "jobs_count": len(normalized_jobs),
            "has_skill": has_skill,
            "apply_requested": apply_requested,
            "search_ok": bool(search_result.get("ok")) if isinstance(search_result, dict) else False,
            "search_error": str(search_result.get("error") or "") if isinstance(search_result, dict) else "",
            "await_apply_confirmation": bool(apply_requested and has_skill),
            "search_only_no_skill": bool(apply_requested and not has_skill),
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
                    "submitted": bool(result.get("ok") and result.get("clicked")),
                    "detail": result,
                }
            )

        self.site_store.append_applications(site_id, applications, session_id=session_id, turn_id=turn_id)
        self.site_store.append_event(site_id, "jobs.apply", {"count": len(applications)})
        return {"ok": True, "applied": applications}
