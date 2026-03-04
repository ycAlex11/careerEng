"""Site-level storage for jobs/applications/events."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.storage.jsonl import JSONLStore
from careereng.utils import ensure_dir, now_iso, safe_file_stem, today_str, write_json, read_json


class SiteStore:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.sites_dir = ensure_dir(workspace / "sites")

    def site_dir(self, site_id: str) -> Path:
        return self.sites_dir / safe_file_stem(site_id)

    def register(self, site: str, base_url: str = "") -> dict[str, Any]:
        site_id = safe_file_stem(site)
        root = self.site_dir(site_id)
        ensure_dir(root / "jobs")
        ensure_dir(root / "applications")
        ensure_dir(root / "events")
        ensure_dir(root / "skills")
        site_json = root / "site.json"
        payload = read_json(site_json)
        payload.update(
            {
                "site_id": site_id,
                "display_name": site,
                "base_url": base_url or payload.get("base_url", ""),
                "updated_at": now_iso(),
            }
        )
        if "created_at" not in payload:
            payload["created_at"] = now_iso()
        write_json(site_json, payload)
        return payload

    def list_sites(self) -> list[dict[str, Any]]:
        sites: list[dict[str, Any]] = []
        for path in sorted(self.sites_dir.glob("*/site.json")):
            data = read_json(path)
            if data:
                sites.append(data)
        return sites

    def has_skill(self, site_id: str) -> bool:
        skill = self.site_dir(site_id) / "skills" / "SKILL.md"
        return skill.exists() and bool(skill.read_text(encoding="utf-8").strip())

    def append_event(self, site_id: str, name: str, payload: dict[str, Any]) -> None:
        JSONLStore(self.site_dir(site_id) / "events" / "all.jsonl").append(
            {
                "ts": now_iso(),
                "name": name,
                "payload": payload,
            }
        )

    def append_jobs(self, site_id: str, jobs: list[dict[str, Any]], session_id: str, turn_id: str) -> None:
        store = JSONLStore(self.site_dir(site_id) / "jobs" / "catalog.jsonl")
        for job in jobs:
            row = {
                "ts": now_iso(),
                "session_id": session_id,
                "turn_id": turn_id,
                **job,
            }
            store.append(row)

    def append_applications(self, site_id: str, applications: list[dict[str, Any]], session_id: str, turn_id: str) -> None:
        store = JSONLStore(self.site_dir(site_id) / "applications" / f"{today_str()}.jsonl")
        for app in applications:
            row = {
                "ts": now_iso(),
                "session_id": session_id,
                "turn_id": turn_id,
                **app,
            }
            store.append(row)
