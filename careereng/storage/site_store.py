"""Site-level storage for registry, jobs/applications/events."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from careereng.storage.jsonl import JSONLStore
from careereng.utils import dump_front_matter, ensure_dir, make_id, now_iso, parse_front_matter, read_json, safe_file_stem, today_str, write_json


class SiteStore:
    RUN_JOB_STRING_FIELDS = (
        "batch_id",
        "session_id",
        "turn_id",
        "canonical_job_id",
        "site_id",
        "employer",
        "title",
        "url",
        "location",
        "posted_at",
        "posted_label",
        "posted_label_raw",
        "employment_type",
        "match_label",
        "apply_state",
        "card_text",
        "description_ref",
        "jd_sync_status",
        "decision_status",
        "decision_rule_source",
        "decision_rule_name",
        "site_match_signal_raw",
        "match_reason_initial",
        "match_reason_final",
        "fit_reason",
        "fit_source",
        "application_status",
        "last_apply_error",
    )
    RUN_JOB_NUMERIC_FIELDS = (
        "match_score_initial",
        "match_score_final",
        "fit_confidence",
    )
    RUN_JOB_BOOL_FIELDS = ("fit_apply",)

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.sites_dir = ensure_dir(workspace / "sites")
        self.registry = JSONLStore(self.sites_dir / "registry.jsonl")
        self._migrate_existing_sites()

    def site_dir(self, site_id: str) -> Path:
        return self.sites_dir / safe_file_stem(site_id)

    def _site_dirs(self) -> list[Path]:
        rows: list[Path] = []
        for path in sorted(self.sites_dir.iterdir()):
            if not path.is_dir():
                continue
            rows.append(path)
        return rows

    def _ensure_site_tree(self, root: Path) -> None:
        ensure_dir(root / "jobs")
        ensure_dir(root / "jobs" / "runs")
        history_path = root / "jobs" / "history_jobs.json"
        if not history_path.exists():
            history_path.write_text("[]\n", encoding="utf-8")
        ensure_dir(root / "jobs" / "discoveries")
        ensure_dir(root / "jobs" / "descriptions")
        ensure_dir(root / "applications")
        ensure_dir(root / "events")
        ensure_dir(root / "skills")
        ensure_dir(root / "browser")
        ensure_dir(root / "browser" / "user_data")
        JSONLStore(root / "jobs" / "catalog.jsonl")
        JSONLStore(root / "events" / "all.jsonl")

    def _default_site_skill_text(self, site_id: str) -> str:
        body = (
            "# Site Skill\n\n"
            "Use this file to describe how this site should be handled.\n\n"
            "## Session Preparation\n\n"
            "### Authentication\n\n"
            "- Describe whether manual login is needed and where the login flow begins.\n"
            "- Describe what account type should be used and any safe takeover points.\n\n"
            "### Ready Signal\n\n"
            "- Describe what the logged-in ready state looks like before discovery continues.\n\n"
            "## Channel Discovery\n\n"
            "### Navigation\n\n"
            "- Describe how to reach the real jobs surface from the entry URL.\n"
            "- Describe any known redirects, new tabs, ATS handoffs, or site-specific stop conditions.\n\n"
            "### Success Signal\n\n"
            "- Describe what should count as a real jobs list or reliable application entry.\n\n"
            "## Apply\n\n"
            "### Matching Override\n\n"
            "- Describe any site-native matching or already-applied signals that should override the project default.\n\n"
            "### Form Filling\n\n"
            "- Describe site-specific form answers, safe defaults, and fields that require user takeover.\n\n"
            "### Site Signals\n\n"
            "- Describe what counts as already applied, submitted successfully, or clearly blocked on this site.\n\n"
            "### Escalation\n\n"
            "- Describe when the agent should stop and ask the user to take over.\n"
        )
        return dump_front_matter(
            {
                "id": f"site-{site_id}",
                "name": f"{site_id} Site Skill",
                "version": "v1",
                "updated_at": now_iso()[:10],
                "scope": "site",
                "site_key": site_id,
                "status": "draft",
                "apply_enabled": False,
            },
            body,
        )

    def _default_browser_session(self, site_id: str) -> dict[str, Any]:
        profile_dir = self.site_dir(site_id) / "browser" / "user_data"
        return {
            "site_key": site_id,
            "profile_dir": str(profile_dir),
            "login_required": True,
            "session_ready": False,
            "authenticated_ready": False,
            "jobs_surface_ready": False,
            "last_manual_login_at": "",
            "last_validated_at": "",
            "last_validation_result": "unknown",
            "active_run_id": "",
            "last_browser_pid": 0,
            "browser_status": "stopped",
            "last_browser_opened_at": "",
            "resume_phase": "idle",
            "pending_action": "",
            "last_known_url": "",
            "current_job_id": "",
            "current_job_url": "",
            "visible_mode": "headless",
            "current_step_id": "",
            "current_step_attempt": 0,
            "current_step_status": "",
            "expected_outcome": "",
            "last_step_error": "",
            "current_trace_ref": "",
            "mcp_log_path": "",
        }

    def _site_json_path(self, site_id: str) -> Path:
        return self.site_dir(site_id) / "site.json"

    def browser_profile_dir(self, site_id: str) -> Path:
        root = self.site_dir(site_id)
        self._ensure_site_tree(root)
        return root / "browser" / "user_data"

    def browser_session_path(self, site_id: str) -> Path:
        root = self.site_dir(site_id)
        self._ensure_site_tree(root)
        return root / "browser" / "session.json"

    def ensure_browser_session(self, site_id: str) -> dict[str, Any]:
        root = self.site_dir(site_id)
        self._ensure_site_tree(root)
        session_path = self.browser_session_path(site_id)
        current = read_json(session_path)
        site_payload = read_json(root / "site.json")
        payload = self._default_browser_session(site_id)
        if isinstance(current, dict):
            payload.update({k: v for k, v in current.items() if v not in (None,)})
        payload["site_key"] = site_id
        payload["profile_dir"] = str(self.browser_profile_dir(site_id))
        payload.pop("mcp_port", None)
        payload.pop("mcp_endpoint", None)
        if isinstance(site_payload, dict):
            site_payload.pop("mcp_port", None)
        write_json(session_path, payload)
        return payload

    def load_browser_session(self, site_id: str) -> dict[str, Any]:
        session = read_json(self.browser_session_path(site_id))
        if session:
            return self.ensure_browser_session(site_id)
        return self.ensure_browser_session(site_id)

    def save_browser_session(self, site_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.ensure_browser_session(site_id)
        current.update(payload or {})
        current["site_key"] = site_id
        current["profile_dir"] = str(self.browser_profile_dir(site_id))
        current.pop("mcp_port", None)
        current.pop("mcp_endpoint", None)
        write_json(self.browser_session_path(site_id), current)
        return current

    def load_skill(self, site_id: str) -> dict[str, Any]:
        skill_path = self.site_dir(site_id) / "skills" / "SKILL.md"
        if not skill_path.exists():
            return {"path": skill_path, "exists": False, "front_matter": {}, "body": ""}
        text = skill_path.read_text(encoding="utf-8")
        front_matter, body = parse_front_matter(text)
        return {
            "path": skill_path,
            "exists": True,
            "front_matter": front_matter if isinstance(front_matter, dict) else {},
            "body": body,
        }

    def _normalize_company_name(self, value: str) -> str:
        raw = re.sub(r"\s+", " ", str(value or "").replace("_", " ").strip())
        return raw or "site"

    def _guess_legacy_company_name(self, value: str, fallback_key: str = "") -> str:
        raw = self._normalize_company_name(value or fallback_key)
        cleaned = re.sub(r"\s*[\(\[]\s*(china|cn|中国)\s*[\)\]]\s*$", "", raw, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*-\s*(china|cn|中国)\s*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+(china|cn|中国)\s*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_/")
        return cleaned or raw

    def _registry_row_key_set(self, row: dict[str, Any]) -> set[str]:
        values = {
            safe_file_stem(str(row.get("site_key") or "")),
            safe_file_stem(str(row.get("canonical_company") or "")),
            safe_file_stem(str(row.get("raw_name") or "")),
        }
        return {value for value in values if value}

    def _find_registry_row_index(
        self,
        rows: list[dict[str, Any]],
        *,
        raw_name: str,
        canonical_company: str,
        site_key: str,
    ) -> int | None:
        needle = {
            safe_file_stem(site_key),
            safe_file_stem(canonical_company),
            safe_file_stem(raw_name),
            safe_file_stem(self._guess_legacy_company_name(raw_name)),
        }
        needle = {value for value in needle if value}
        for idx, row in enumerate(rows):
            if self._registry_row_key_set(row) & needle:
                return idx
        return None

    def _url_host_key(self, url: str) -> str:
        normalized = self._normalize_url(url)
        if not normalized:
            return ""
        try:
            parsed = urlparse(normalized)
        except Exception:
            return ""
        host = (parsed.netloc or "").strip().lower()
        if host.startswith("www."):
            host = host[4:]
        return host

    def _find_registry_row_index_by_base_url(self, rows: list[dict[str, Any]], base_url: str) -> int | None:
        normalized_target = self._normalize_url(base_url)
        if not normalized_target:
            return None
        target_host = self._url_host_key(normalized_target)
        for idx, row in enumerate(rows):
            row_url = self._normalize_url(str(row.get("base_url") or ""))
            if not row_url:
                continue
            if row_url == normalized_target:
                return idx
            if target_host and self._url_host_key(row_url) == target_host:
                return idx
        return None

    def _normalize_registry_row(self, row: dict[str, Any]) -> dict[str, Any]:
        canonical_company = self._normalize_company_name(
            str(row.get("canonical_company") or row.get("raw_name") or row.get("site_key") or "site")
        )
        site_key = safe_file_stem(str(row.get("site_key") or canonical_company))
        raw_name = str(row.get("raw_name") or canonical_company)
        status = str(row.get("status") or "active")
        if status not in {"active", "inactive"}:
            status = "active"
        registered_at = str(row.get("registered_at") or row.get("updated_at") or now_iso())
        updated_at = str(row.get("updated_at") or registered_at)
        return {
            "registry_id": str(row.get("registry_id") or make_id("site")),
            "canonical_company": canonical_company,
            "site_key": site_key,
            "raw_name": raw_name,
            "status": status,
            "base_url": self._normalize_url(str(row.get("base_url") or "")),
            "source_type": str(row.get("source_type") or "manual"),
            "registered_at": registered_at,
            "updated_at": updated_at,
        }

    def _read_registry_rows(self) -> list[dict[str, Any]]:
        rows = self.registry.read_all()
        normalized: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = self._normalize_registry_row(row)
            normalized[item["site_key"]] = item
        return list(normalized.values())

    def _write_registry_rows(self, rows: list[dict[str, Any]]) -> None:
        normalized: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = self._normalize_registry_row(row)
            normalized[item["site_key"]] = item
        ordered = sorted(
            normalized.values(),
            key=lambda row: (0 if row.get("status") == "active" else 1, str(row.get("canonical_company") or "").lower()),
        )
        self.registry.write_all(ordered)

    def _history_jobs_path(self, site_id: str) -> Path:
        root = self.site_dir(site_id)
        self._ensure_site_tree(root)
        return root / "jobs" / "history_jobs.json"

    def _job_runs_dir(self, site_id: str) -> Path:
        root = self.site_dir(site_id)
        self._ensure_site_tree(root)
        return root / "jobs" / "runs"

    def _job_run_path(self, site_id: str, batch_id: str) -> Path:
        batch_key = safe_file_stem(batch_id or "adhoc_run")
        return self._job_runs_dir(site_id) / f"{batch_key}.jsonl"

    def _load_history_jobs(self, site_id: str) -> list[dict[str, Any]]:
        path = self._history_jobs_path(site_id)
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(payload, list):
            return []
        return [row for row in payload if isinstance(row, dict)]

    def _write_history_jobs(self, site_id: str, rows: list[dict[str, Any]]) -> None:
        path = self._history_jobs_path(site_id)
        ensure_dir(path.parent)
        path.write_text(
            json.dumps([row for row in rows if isinstance(row, dict)], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _inspect_legacy_job_data(self, root: Path) -> tuple[bool, bool]:
        catalog = root / "jobs" / "catalog.jsonl"
        had_catalog_data = bool(catalog.exists() and catalog.read_text(encoding="utf-8").strip())
        discoveries_dir = root / "jobs" / "discoveries"
        had_discovery_data = False
        if discoveries_dir.exists():
            for path in discoveries_dir.rglob("*.jsonl"):
                if path.is_file() and path.read_text(encoding="utf-8").strip():
                    had_discovery_data = True
                    break
        return had_catalog_data, had_discovery_data or had_catalog_data

    def _migrate_existing_sites(self) -> None:
        rows = self._read_registry_rows()
        synced_rows: list[dict[str, Any]] = []
        registry_changed = False
        seen_site_keys: set[str] = set()

        for path in self._site_dirs():
            original_root = path
            original = read_json(path / "site.json")
            raw_name = str(original.get("raw_name") or original.get("display_name") or original.get("canonical_company") or path.name)
            canonical_company = self._guess_legacy_company_name(raw_name, fallback_key=path.name)
            desired_key = safe_file_stem(canonical_company)
            root = path
            if desired_key and desired_key != path.name:
                target = self.sites_dir / desired_key
                if not target.exists():
                    path.rename(target)
                    root = target
                    registry_changed = True

            self._ensure_site_tree(root)
            current = read_json(root / "site.json")
            had_catalog_data, legacy_dirty = self._inspect_legacy_job_data(root)
            site_key = root.name
            seen_site_keys.add(site_key)
            history_path = self._history_jobs_path(site_key)
            history_rows = self._load_history_jobs(site_key)
            if not history_rows:
                legacy_catalog_rows = JSONLStore(root / "jobs" / "catalog.jsonl").read_all()
                if legacy_catalog_rows:
                    history_path.write_text(
                        json.dumps(legacy_catalog_rows, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
            idx = self._find_registry_row_index(
                rows,
                raw_name=raw_name,
                canonical_company=canonical_company,
                site_key=site_key,
            )
            existing_row = rows[idx] if idx is not None else {}
            registry_id = str(existing_row.get("registry_id") or current.get("registry_id") or make_id("site"))
            registered_at = str(existing_row.get("registered_at") or current.get("created_at") or now_iso())
            created_at = str(current.get("created_at") or original.get("created_at") or registered_at)
            desired_payload = dict(current)
            desired_payload.update(
                {
                    "registry_id": registry_id,
                    "site_id": site_key,
                    "site_key": site_key,
                    "display_name": canonical_company,
                    "canonical_company": canonical_company,
                    "raw_name": raw_name,
                    "base_url": self._normalize_url(str(current.get("base_url") or original.get("base_url") or "")),
                    "status": str(current.get("status") or original.get("status") or "active"),
                    "source_type": str(current.get("source_type") or original.get("source_type") or "migrated"),
                    "created_at": created_at,
                }
            )
            desired_payload.pop("mcp_port", None)
            if legacy_dirty or bool(current.get("legacy_discoveries_dirty")):
                desired_payload["legacy_discoveries_dirty"] = True

            payload_changed = (
                had_catalog_data
                or original_root != root
                or not (root / "site.json").exists()
                or "mcp_port" in current
                or "mcp_endpoint" in current
            )
            for key in (
                "site_id",
                "site_key",
                "display_name",
                "canonical_company",
                "raw_name",
                "base_url",
                "status",
                "source_type",
                "created_at",
                "legacy_discoveries_dirty",
                "registry_id",
            ):
                if desired_payload.get(key) != current.get(key):
                    payload_changed = True
                    break
            desired_payload["updated_at"] = now_iso() if payload_changed else str(current.get("updated_at") or desired_payload["created_at"])
            if payload_changed:
                write_json(root / "site.json", desired_payload)

            desired_row = {
                "registry_id": registry_id,
                "canonical_company": str(desired_payload.get("canonical_company") or canonical_company),
                "site_key": site_key,
                "raw_name": str(desired_payload.get("raw_name") or raw_name),
                "status": str(desired_payload.get("status") or "active"),
                "base_url": self._normalize_url(str(desired_payload.get("base_url") or "")),
                "source_type": str(desired_payload.get("source_type") or "migrated"),
                "registered_at": registered_at,
                "updated_at": str(desired_payload.get("updated_at") or now_iso()),
            }
            row_changed = idx is None
            if idx is not None:
                for key in (
                    "canonical_company",
                    "site_key",
                    "raw_name",
                    "status",
                    "base_url",
                    "source_type",
                    "registered_at",
                ):
                    if desired_row.get(key) != existing_row.get(key):
                        row_changed = True
                        break
            synced_rows.append(desired_row)
            self.ensure_skill_template(site_key)
            self.ensure_browser_session(site_key)
            if row_changed:
                desired_row["updated_at"] = now_iso()
                registry_changed = True

        if len(synced_rows) != len(rows):
            registry_changed = True
        elif any(safe_file_stem(str(row.get("site_key") or "")) not in seen_site_keys for row in rows):
            registry_changed = True

        if registry_changed:
            self._write_registry_rows(synced_rows)

    def _build_site_payload(
        self,
        *,
        site_key: str,
        canonical_company: str,
        raw_name: str,
        base_url: str,
        source_type: str,
        registry_id: str,
        created_at: str,
        updated_at: str,
        status: str,
        existing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(existing or {})
        payload.pop("mcp_port", None)
        payload.update(
            {
                "registry_id": registry_id,
                "site_id": site_key,
                "site_key": site_key,
                "display_name": canonical_company,
                "canonical_company": canonical_company,
                "raw_name": raw_name,
                "base_url": base_url,
                "source_type": source_type,
                "status": status,
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )
        if existing and existing.get("legacy_discoveries_dirty"):
            payload["legacy_discoveries_dirty"] = True
        return payload

    def register(self, site: str, base_url: str = "", source_type: str = "manual") -> dict[str, Any]:
        rows = self._read_registry_rows()
        raw_name = self._normalize_company_name(site)
        canonical_company = raw_name
        site_key = safe_file_stem(canonical_company)
        normalized_base_url = self._normalize_url(base_url)
        now = now_iso()
        idx = self._find_registry_row_index(
            rows,
            raw_name=raw_name,
            canonical_company=canonical_company,
            site_key=site_key,
        )
        if idx is None and normalized_base_url:
            idx = self._find_registry_row_index_by_base_url(rows, normalized_base_url)
        existing_row = rows[idx] if idx is not None else {}
        registry_id = str(existing_row.get("registry_id") or make_id("site"))
        registered_at = str(existing_row.get("registered_at") or now)
        site_key = safe_file_stem(str(existing_row.get("site_key") or site_key))
        canonical_company = self._normalize_company_name(str(existing_row.get("canonical_company") or canonical_company))
        root = self.site_dir(site_key)
        self._ensure_site_tree(root)
        current = read_json(root / "site.json")
        resolved_base_url = normalized_base_url or self._normalize_url(str(current.get("base_url") or existing_row.get("base_url") or ""))
        resolved_source_type = str(source_type or current.get("source_type") or existing_row.get("source_type") or "manual")
        created_at = str(current.get("created_at") or registered_at or now)
        payload = self._build_site_payload(
            site_key=site_key,
            canonical_company=canonical_company,
            raw_name=raw_name,
            base_url=resolved_base_url,
            source_type=resolved_source_type,
            registry_id=registry_id,
            created_at=created_at,
            updated_at=now,
            status="active",
            existing=current,
        )
        write_json(root / "site.json", payload)
        self.ensure_skill_template(site_key)
        self.ensure_browser_session(site_key)

        row = {
            "registry_id": registry_id,
            "canonical_company": canonical_company,
            "site_key": site_key,
            "raw_name": raw_name,
            "status": "active",
            "base_url": resolved_base_url,
            "source_type": resolved_source_type,
            "registered_at": registered_at,
            "updated_at": now,
        }
        if idx is None:
            rows.append(row)
        else:
            rows[idx] = row
        self._write_registry_rows(rows)
        return payload

    def list_sites(self, status: str | None = None) -> list[dict[str, Any]]:
        rows = self._read_registry_rows()
        if status:
            rows = [row for row in rows if str(row.get("status") or "") == status]
        return [dict(row) for row in rows]

    def find_site(self, name: str, *, include_inactive: bool = True) -> dict[str, Any] | None:
        rows = self._read_registry_rows()
        canonical_company = self._normalize_company_name(name)
        site_key = safe_file_stem(canonical_company)
        idx = self._find_registry_row_index(rows, raw_name=name, canonical_company=canonical_company, site_key=site_key)
        if idx is None:
            return None
        row = rows[idx]
        if not include_inactive and str(row.get("status") or "") != "active":
            return None
        return row

    def _set_status(self, name: str, *, status: str, base_url: str = "") -> dict[str, Any]:
        rows = self._read_registry_rows()
        canonical_company = self._normalize_company_name(name)
        site_key = safe_file_stem(canonical_company)
        idx = self._find_registry_row_index(rows, raw_name=name, canonical_company=canonical_company, site_key=site_key)
        if idx is None:
            raise KeyError(name)
        row = dict(rows[idx])
        now = now_iso()
        row["status"] = status
        if base_url:
            row["base_url"] = self._normalize_url(base_url)
        row["updated_at"] = now
        rows[idx] = row
        self._write_registry_rows(rows)

        root = self.site_dir(str(row.get("site_key") or site_key))
        self._ensure_site_tree(root)
        current = read_json(root / "site.json")
        payload = self._build_site_payload(
            site_key=str(row.get("site_key") or site_key),
            canonical_company=self._normalize_company_name(str(row.get("canonical_company") or canonical_company)),
            raw_name=str(row.get("raw_name") or name),
            base_url=self._normalize_url(str(row.get("base_url") or "")),
            source_type=str(row.get("source_type") or "manual"),
            registry_id=str(row.get("registry_id") or make_id("site")),
            created_at=str(current.get("created_at") or row.get("registered_at") or now),
            updated_at=now,
            status=status,
            existing=current,
        )
        write_json(root / "site.json", payload)
        self.ensure_skill_template(str(row.get("site_key") or site_key))
        self.ensure_browser_session(str(row.get("site_key") or site_key))
        return row

    def activate(self, name: str, *, base_url: str = "") -> dict[str, Any]:
        return self._set_status(name, status="active", base_url=base_url)

    def deactivate(self, name: str) -> dict[str, Any]:
        return self._set_status(name, status="inactive")

    def has_skill(self, site_id: str) -> bool:
        skill = self.site_dir(site_id) / "skills" / "SKILL.md"
        return skill.exists() and bool(skill.read_text(encoding="utf-8").strip())

    def ensure_skill_template(self, site_id: str) -> tuple[Path, bool]:
        skill = self.site_dir(site_id) / "skills" / "SKILL.md"
        existed = skill.exists()
        if not existed:
            skill.write_text(self._default_site_skill_text(site_id), encoding="utf-8")
        return skill, not existed

    def _normalize_url(self, url: str) -> str:
        raw = (url or "").strip()
        if not raw:
            return ""
        try:
            parsed = urlparse(raw)
        except Exception:
            return raw
        if not parsed.scheme or not parsed.netloc:
            return raw
        kept_qs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False) if not k.lower().startswith("utm_")]
        norm = parsed._replace(query=urlencode(sorted(kept_qs)), fragment="")
        return urlunparse(norm)

    def _job_ids(self, site_id: str, title: str, employer: str, url: str) -> tuple[str, str]:
        canonical_source = "|".join(
            [
                safe_file_stem(employer or site_id),
                safe_file_stem(title),
                safe_file_stem(self._normalize_url(url)),
            ]
        )
        canonical_job_id = "cj_" + hashlib.sha1(canonical_source.encode("utf-8")).hexdigest()[:16]
        scoped_source = site_id + "|" + canonical_source
        job_id = "job_" + hashlib.sha1(scoped_source.encode("utf-8")).hexdigest()[:16]
        return job_id, canonical_job_id

    @staticmethod
    def _normalize_job_text(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip()).lower()

    def _job_source_ref(self, row: dict[str, Any]) -> str:
        if not isinstance(row, dict):
            return ""
        explicit_fields = (
            "source_job_ref",
            "source_job_id",
            "job_number",
            "job_number_raw",
            "requisition_id",
            "requisition_number",
            "pid",
        )
        for field in explicit_fields:
            value = self._normalize_job_text(str(row.get(field) or ""))
            if value:
                return f"{field}:{value}"

        url = self._normalize_url(str(row.get("url") or ""))
        if not url:
            return ""
        try:
            parsed = urlparse(url)
        except Exception:
            return ""
        query = {k.lower(): v for k, v in parse_qsl(parsed.query, keep_blank_values=False)}
        for field in ("pid", "jobid", "job_id", "jobnumber", "job_number", "requisitionid", "reqid"):
            value = self._normalize_job_text(query.get(field, ""))
            if value:
                return f"{field}:{value}"
        match = re.search(r"/job[s]?/([^/?#]+)", parsed.path, flags=re.IGNORECASE)
        if match:
            return f"path-job:{self._normalize_job_text(match.group(1))}"
        return ""

    def _history_match_keys(self, site_id: str, row: dict[str, Any]) -> list[str]:
        if not isinstance(row, dict):
            return []
        site = safe_file_stem(site_id)
        keys: list[str] = []

        url = self._normalize_url(str(row.get("url") or ""))
        if url:
            keys.append(f"url|{site}|{url}")

        source_ref = self._job_source_ref(row)
        if source_ref:
            keys.append(f"source|{site}|{source_ref}")

        title = self._normalize_job_text(str(row.get("title") or ""))
        location = self._normalize_job_text(str(row.get("location") or ""))
        posted = self._normalize_job_text(str(row.get("posted_label_raw") or row.get("posted_label") or ""))
        if title and location and posted:
            keys.append(f"fallback|{site}|{title}|{location}|{posted}")

        deduped: list[str] = []
        seen: set[str] = set()
        for key in keys:
            if key and key not in seen:
                seen.add(key)
                deduped.append(key)
        return deduped

    def _history_identity_seed(self, site_id: str, row: dict[str, Any]) -> str:
        keys = self._history_match_keys(site_id, row)
        if keys:
            return keys[0]
        site = safe_file_stem(site_id)
        title = self._normalize_job_text(str(row.get("title") or ""))
        location = self._normalize_job_text(str(row.get("location") or ""))
        posted = self._normalize_job_text(str(row.get("posted_label_raw") or row.get("posted_label") or ""))
        card_text = self._normalize_job_text(str(row.get("card_text") or ""))
        return f"opaque|{site}|{title}|{location}|{posted}|{card_text}"

    def _history_ids(self, site_id: str, row: dict[str, Any]) -> tuple[str, str]:
        seed = self._history_identity_seed(site_id, row)
        canonical_job_id = "cj_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
        job_id = "job_" + hashlib.sha1(f"{site_id}|{seed}".encode("utf-8")).hexdigest()[:16]
        return job_id, canonical_job_id

    @staticmethod
    def _merge_job_row(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for field in (
            "batch_id",
            "session_id",
            "turn_id",
            "canonical_job_id",
            "site_id",
            "employer",
            "title",
            "url",
            "location",
            "posted_at",
            "posted_label",
            "posted_label_raw",
            "employment_type",
            "match_label",
            "apply_state",
            "description_ref",
        ):
            value = str(incoming.get(field) or "").strip()
            if value:
                merged[field] = value
        card_text = str(incoming.get("card_text") or "").strip()
        if card_text:
            merged["card_text"] = card_text[:2000]
        ts = str(incoming.get("ts") or "").strip()
        if ts:
            merged["ts"] = ts
        return merged

    def _persist_description_ref(self, *, root: Path, job_id: str, description: str) -> str:
        text = str(description or "").strip()
        if not text:
            return ""
        ensure_dir(root / "jobs" / "descriptions")
        doc_id = hashlib.sha1((job_id + "|" + text).encode("utf-8")).hexdigest()[:16]
        desc_path = root / "jobs" / "descriptions" / f"{doc_id}.md"
        if not desc_path.exists():
            desc_path.write_text(text, encoding="utf-8")
        return str(desc_path.relative_to(self.workspace))

    def _merge_run_job_row(
        self,
        *,
        base: dict[str, Any],
        incoming: dict[str, Any],
        root: Path,
        job_id: str,
        session_id: str,
        turn_id: str,
        batch_id: str,
    ) -> dict[str, Any]:
        merged = dict(base)
        merged["job_id"] = job_id
        merged["ts"] = now_iso()
        if batch_id:
            merged["batch_id"] = batch_id
        if session_id:
            merged["session_id"] = session_id
        if turn_id:
            merged["turn_id"] = turn_id

        for field in self.RUN_JOB_STRING_FIELDS:
            value = incoming.get(field)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                if field == "url":
                    merged[field] = self._normalize_url(text)
                elif field == "card_text":
                    merged[field] = text[:2000]
                else:
                    merged[field] = text
        for field in self.RUN_JOB_NUMERIC_FIELDS:
            value = incoming.get(field)
            if value in (None, ""):
                continue
            try:
                merged[field] = float(value)
            except Exception:
                continue
        for field in self.RUN_JOB_BOOL_FIELDS:
            if field in incoming:
                merged[field] = bool(incoming.get(field))

        description = str(incoming.get("description") or "").strip()
        if description:
            merged["description_ref"] = self._persist_description_ref(
                root=root,
                job_id=job_id,
                description=description,
            )
        return merged

    def preview_history_new_flags(self, site_id: str, jobs: list[dict[str, Any]]) -> list[bool]:
        history_rows = self._load_history_jobs(site_id)
        lookup: dict[str, str] = {}
        for row in history_rows:
            if not isinstance(row, dict):
                continue
            job_id = str(row.get("job_id") or "").strip()
            if not job_id:
                continue
            for key in self._history_match_keys(site_id, row):
                lookup.setdefault(key, job_id)

        flags: list[bool] = []
        for job in jobs:
            keys = self._history_match_keys(site_id, job if isinstance(job, dict) else {})
            flags.append(not any(key in lookup for key in keys))
        return flags

    def append_event(self, site_id: str, name: str, payload: dict[str, Any]) -> None:
        JSONLStore(self.site_dir(site_id) / "events" / "all.jsonl").append(
            {
                "ts": now_iso(),
                "name": name,
                "payload": payload,
            }
        )

    def append_step_trace(self, site_id: str, turn_id: str, payload: dict[str, Any]) -> str:
        trace_dir = self.site_dir(site_id) / "events" / "traces"
        ensure_dir(trace_dir)
        trace_path = trace_dir / f"{turn_id}.jsonl"
        JSONLStore(trace_path).append({"ts": now_iso(), **(payload or {})})
        return str(trace_path.relative_to(self.workspace))

    def list_jobs(self, site_id: str) -> list[dict[str, Any]]:
        return self._load_history_jobs(site_id)

    def list_run_jobs(self, site_id: str, batch_id: str) -> list[dict[str, Any]]:
        rows = JSONLStore(self._job_run_path(site_id, batch_id)).read_all()
        return [row for row in rows if isinstance(row, dict)]

    def append_jobs(
        self,
        site_id: str,
        jobs: list[dict[str, Any]],
        session_id: str,
        turn_id: str,
        batch_id: str = "",
    ) -> list[dict[str, Any]]:
        root = self.site_dir(site_id)
        ensure_dir(root / "jobs" / "runs")
        ensure_dir(root / "jobs" / "descriptions")
        run_store = JSONLStore(self._job_run_path(site_id, batch_id))
        rows = run_store.read_all()

        appended_rows: list[dict[str, Any]] = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            title = str(job.get("title") or "").strip()
            url = self._normalize_url(str(job.get("url") or ""))
            employer = str(job.get("employer") or job.get("company") or root.name)
            if not title and not url:
                continue
            job_id, canonical_job_id = self._job_ids(site_id, title, employer, url)
            now = now_iso()
            description_ref = self._persist_description_ref(
                root=root,
                job_id=job_id,
                description=str(job.get("description") or ""),
            )

            snapshot = {
                "observation_id": make_id("obs"),
                "ts": now,
                "batch_id": str(batch_id or ""),
                "session_id": session_id,
                "turn_id": turn_id,
                "job_id": job_id,
                "canonical_job_id": canonical_job_id,
                "site_id": site_id,
                "employer": employer,
                "title": title,
                "url": url,
                "location": str(job.get("location") or ""),
                "posted_at": str(job.get("posted_at") or ""),
                "posted_label": str(job.get("posted_label") or ""),
                "employment_type": str(job.get("employment_type") or ""),
                "match_label": str(job.get("match_label") or ""),
                "apply_state": str(job.get("apply_state") or ""),
                "card_text": str(job.get("card_text") or "")[:2000],
                "description_ref": description_ref,
                "posted_label_raw": str(job.get("posted_label") or ""),
            }
            rows.append(snapshot)
            appended_rows.append(snapshot)

        run_rows = [row for row in rows if isinstance(row, dict)]
        run_rows.sort(key=lambda r: (str(r.get("ts") or ""), str(r.get("observation_id") or ""), str(r.get("job_id") or "")))
        run_store.write_all(run_rows)
        return appended_rows

    def update_run_jobs(
        self,
        site_id: str,
        jobs: list[dict[str, Any]],
        session_id: str,
        turn_id: str,
        batch_id: str = "",
    ) -> list[dict[str, Any]]:
        root = self.site_dir(site_id)
        run_store = JSONLStore(self._job_run_path(site_id, batch_id))
        rows = [row for row in run_store.read_all() if isinstance(row, dict)]
        index = {str(row.get("job_id") or ""): idx for idx, row in enumerate(rows) if str(row.get("job_id") or "")}

        updated_rows: list[dict[str, Any]] = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            job_id = str(job.get("job_id") or "").strip()
            if not job_id:
                continue
            base = rows[index[job_id]] if job_id in index else {"job_id": job_id}
            merged = self._merge_run_job_row(
                base=base,
                incoming=job,
                root=root,
                job_id=job_id,
                session_id=session_id,
                turn_id=turn_id,
                batch_id=str(batch_id or ""),
            )
            if job_id in index:
                rows[index[job_id]] = merged
            else:
                index[job_id] = len(rows)
                rows.append(merged)
            updated_rows.append(merged)

        rows.sort(key=lambda r: (str(r.get("ts") or ""), str(r.get("observation_id") or ""), str(r.get("job_id") or "")))
        run_store.write_all(rows)
        return updated_rows

    def promote_run_jobs_to_history(self, site_id: str, batch_id: str) -> list[dict[str, Any]]:
        history_rows = self._load_history_jobs(site_id)
        history_index: dict[str, dict[str, Any]] = {
            str(row.get("job_id") or ""): dict(row)
            for row in history_rows
            if isinstance(row, dict) and str(row.get("job_id") or "")
        }
        history_lookup: dict[str, str] = {}
        for job_id, row in history_index.items():
            for key in self._history_match_keys(site_id, row):
                history_lookup.setdefault(key, job_id)

        run_rows = self.list_run_jobs(site_id, batch_id)
        if not run_rows:
            return []

        aggregated_rows: dict[str, dict[str, Any]] = {}
        ordered_targets: list[str] = []
        for row in run_rows:
            if not isinstance(row, dict):
                continue
            match_keys = self._history_match_keys(site_id, row)
            target = ""
            for key in match_keys:
                existing_target = history_lookup.get(key, "")
                if existing_target:
                    target = existing_target
                    break
            if not target:
                target = "new:" + self._history_identity_seed(site_id, row)
            current = aggregated_rows.get(target)
            if current is None:
                aggregated_rows[target] = dict(row)
                ordered_targets.append(target)
            else:
                aggregated_rows[target] = self._merge_job_row(current, row)
            for key in match_keys:
                history_lookup[key] = target

        promoted_ids: list[str] = []
        for target in ordered_targets:
            row = aggregated_rows[target]
            now = str(row.get("ts") or now_iso())
            if target.startswith("new:"):
                job_id, canonical_job_id = self._history_ids(site_id, row)
                current = history_index.get(job_id)
                if current is None:
                    current = dict(row)
                    current["job_id"] = job_id
                    current["canonical_job_id"] = canonical_job_id
                    current["first_seen_at"] = now
                    current["last_seen_at"] = now
                    current["seen_count"] = 1
                    current["is_active"] = True
                    current["features_ref"] = str(current.get("features_ref") or "")
                    history_index[job_id] = current
                else:
                    current = self._merge_job_row(current, row)
                    current["job_id"] = job_id
                    current["canonical_job_id"] = canonical_job_id
                    current["last_seen_at"] = now
                    current["seen_count"] = int(current.get("seen_count") or 0) + 1
                    current["is_active"] = True
                    history_index[job_id] = current
            else:
                job_id = target
                current = history_index.get(job_id)
            if current is None:
                continue
            if not target.startswith("new:"):
                current = self._merge_job_row(current, row)
                current["last_seen_at"] = now
                current["seen_count"] = int(current.get("seen_count") or 0) + 1
                current["is_active"] = True
                history_index[job_id] = current
            for key in self._history_match_keys(site_id, current):
                history_lookup[key] = job_id
            promoted_ids.append(job_id)

        merged_rows = list(history_index.values())
        merged_rows.sort(key=lambda r: (str(r.get("first_seen_at") or ""), str(r.get("job_id") or "")))
        self._write_history_jobs(site_id, merged_rows)
        return [history_index[job_id] for job_id in promoted_ids if job_id in history_index]

    def update_job_decisions(self, site_id: str, jobs: list[dict[str, Any]]) -> None:
        rows = self._load_history_jobs(site_id)
        index = {str(row.get("job_id") or ""): row for row in rows if str(row.get("job_id") or "")}
        changed = False
        for job in jobs:
            if not isinstance(job, dict):
                continue
            job_id = str(job.get("job_id") or "")
            current = index.get(job_id)
            if not current:
                continue
            if "fit_apply" not in job and "fit_reason" not in job:
                continue
            current["fit_apply"] = bool(job.get("fit_apply"))
            try:
                current["fit_confidence"] = float(job.get("fit_confidence") or 0.0)
            except Exception:
                current["fit_confidence"] = 0.0
            current["fit_reason"] = str(job.get("fit_reason") or "")
            current["fit_source"] = str(job.get("fit_source") or "")
            current["decision_status"] = str(
                job.get("decision_status") or ("recommended_apply" if current["fit_apply"] else "filtered_out")
            )
            current["decision_updated_at"] = now_iso()
            changed = True
        if changed:
            self._write_history_jobs(site_id, rows)

    def update_job_application_outcomes(self, site_id: str, applications: list[dict[str, Any]]) -> None:
        rows = self._load_history_jobs(site_id)
        index = {str(row.get("job_id") or ""): row for row in rows if str(row.get("job_id") or "")}
        changed = False
        for app in applications:
            if not isinstance(app, dict):
                continue
            job_id = str(app.get("job_id") or "")
            current = index.get(job_id)
            if not current:
                continue
            status = str(app.get("status") or ("submitted" if app.get("submitted") else "apply_failed"))
            current["application_status"] = status
            current["application_updated_at"] = now_iso()
            current["last_apply_error"] = str(
                app.get("detail", {}).get("error") if isinstance(app.get("detail"), dict) else ""
            )
            if status == "submitted":
                current["last_submitted_at"] = now_iso()
            changed = True
        if changed:
            self._write_history_jobs(site_id, rows)

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

    def append_job_features(self, site_id: str, features: list[dict[str, Any]], session_id: str, turn_id: str) -> None:
        store = JSONLStore(self.site_dir(site_id) / "jobs" / "features.jsonl")
        for row in features:
            if not isinstance(row, dict):
                continue
            store.append(
                {
                    "ts": now_iso(),
                    "session_id": session_id,
                    "turn_id": turn_id,
                    **row,
                }
            )
