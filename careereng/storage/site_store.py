"""Site-level storage for registry, jobs/applications/events."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse

from careereng.skill_schema import (
    MATCHING_POLICY_SECTION,
    SITE_POLICY_SECTION,
    context_hash,
    hash_text,
    load_job_skill_policies,
    normalize_posted_window_policy,
    policy_hash,
    section_hash,
    section_text,
)
from careereng.skill_schema.schema import file_hash
from careereng.storage.job_identity import infer_site_job_id_from_url, normalize_identity_url
from careereng.storage.jsonl import JSONLStore
from careereng.storage.posted_time import current_posted_age_observation, normalize_posted_fields
from careereng.utils import dump_front_matter, ensure_dir, make_id, now_iso, parse_front_matter, read_json, safe_file_stem, today_str, write_json


class SiteStore:
    WORKDAY_JOB_NUMBER_RE = re.compile(r"(?<![A-Z0-9])JR\d{3,}\b", flags=re.IGNORECASE)
    YEAR_PREFIXED_JOB_ID_RE = re.compile(r"\b(?:19|20)\d{2}[-_](\d{3,})\b", flags=re.IGNORECASE)
    SITE_JOB_ID_LIKE_RE = re.compile(r"(?:\d{3,}|(?:19|20)\d{2}[-_]\d{3,}|[a-z]{1,12}[-_]?\d{3,})")
    APPLICATION_REVIEW_NON_JOB_PATH_SEGMENTS = {
        "dashboard",
        "candidate-home",
        "candidate_home",
        "candidatehome",
        "my-applications",
        "my_applications",
        "application-center",
        "action-center",
        "applications",
        "profile",
    }
    DURABLE_APPLICATION_STATUSES = {
        "already_applied",
        "filtered_out",
        "submitted",
        "rejected",
        "closed",
        "withdrawn",
    }
    RUNTIME_STOP_ERROR_MARKERS = (
        "cannot schedule new futures after shutdown",
        "cannot schedule new futures after interpreter shutdown",
        "interpreter shutdown",
        "batch_cancelled",
        "batch cancelled",
        "cancelled by batch-stop",
    )
    CLOSED_OR_UNAVAILABLE_MARKERS = (
        "0 jobs found",
        "no job openings",
        "page you are looking for doesn't exist",
        "page you are looking for does not exist",
        "job not found",
        "position closed",
        "position has been closed",
        "no longer accepting",
        "no longer available",
        "missing-page",
        "direct job url resolved to missing",
    )
    FILTERED_OUT_CONFLICT_MARKERS = (
        "filtered out",
        "do not apply",
        "not recommended",
        "posted_too_old",
        "too old",
        "older than",
        "exceeds",
        "outside apply candidate",
        "apply limit",
        "application window",
        "site rule limit",
    )
    MIXED_FULLTIME_INTERNSHIP_RE = re.compile(
        r"(全职\s*[/／|、,，和或]*\s*实习|实习\s*[/／|、,，和或]*\s*全职|"
        r"full[-\s]*time\s*(?:/|\\|or|and|,|，|、|／)\s*(?:intern|internship)|"
        r"(?:intern|internship)\s*(?:/|\\|or|and|,|，|、|／)\s*full[-\s]*time)",
        flags=re.IGNORECASE,
    )

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
        "posted_observed_at",
        "inferred_posted_date",
        "employment_type",
        "match_label",
        "apply_state",
        "site_job_id",
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
        "application_status_raw",
        "last_apply_error",
        "decision_reason_type",
        "decision_context_hash",
        "block_reason_type",
        "failure_pattern",
        "loop_control_action",
        "recommended_target",
        "loop_scope",
        "gap_type",
        "recommended_action",
        "target",
        "resume_policy",
        "current_item_ref",
        "evidence",
        "refinement_hint",
        "active_run_local_proposal_id",
        "active_run_local_proposal_memory_id",
        "active_run_local_proposal_pattern",
        "active_run_local_proposal_source",
    )
    RUN_JOB_NUMERIC_FIELDS = (
        "match_score_initial",
        "match_score_final",
        "fit_confidence",
        "observed_posted_age_days",
    )
    RUN_JOB_BOOL_FIELDS = ("fit_apply", "observed_posted_age_is_lower_bound")

    def __init__(self, workspace: Path, project_root: Path | None = None):
        self.workspace = Path(workspace)
        self.project_root = Path(project_root) if project_root is not None else self.workspace.parent
        self.site_skills_dir = ensure_dir(self.project_root / "skills" / "search" / "jobs" / "sites")
        self.sites_dir = ensure_dir(self.workspace / "sites")
        self.registry = JSONLStore(self.sites_dir / "registry.jsonl")
        self._migrate_existing_sites()

    def site_dir(self, site_id: str) -> Path:
        return self.sites_dir / safe_file_stem(site_id)

    def site_skill_path(self, site_id: str) -> Path:
        return self.site_skills_dir / safe_file_stem(site_id) / "SKILL.md"

    def legacy_project_site_skill_path(self, site_id: str) -> Path:
        return self.project_root / "skills" / "sites" / safe_file_stem(site_id) / "SKILL.md"

    def legacy_site_skill_path(self, site_id: str) -> Path:
        return self.site_dir(site_id) / "skills" / "SKILL.md"

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
            "## Site Policy\n\n"
            "### Retrieval Policy\n\n"
            "- Describe site-specific posted-window rules, pagination stop rules, and whether old roles should still be recorded.\n\n"
            "### Job Identity Policy\n\n"
            "- If this site uses hash-routed job detail URLs, declare the pattern in front matter, for example `job_identity.fragment_job_route_patterns: ['#/job/{site_job_id}']`.\n"
            "- Record real job detail URLs and site-native job IDs when visible; do not record a generic company jobs-list URL as a job URL.\n\n"
            "### Application Review Policy\n\n"
            "- Describe how this site exposes submitted, active, inactive, rejected, closed, or withdrawn applications.\n\n"
            "## Matching Policy\n\n"
            "### Application Gate\n\n"
            "- Describe site-native match labels, hard exclusions, and when the shared project matching rule should be used.\n\n"
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
                "status": "ready",
                "apply_enabled": False,
                "job_identity": {},
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

    def _migrate_legacy_site_skill(self, site_id: str) -> Path:
        skill_path = self.site_skill_path(site_id)
        legacy_path = self.legacy_project_site_skill_path(site_id)
        if not skill_path.exists() and legacy_path.exists():
            ensure_dir(skill_path.parent)
            shutil.copy2(legacy_path, skill_path)
        return skill_path

    def load_skill(self, site_id: str) -> dict[str, Any]:
        skill_path = self._migrate_legacy_site_skill(site_id)
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

    def _job_identity_policy(self, site_id: str) -> dict[str, Any]:
        skill = self.load_skill(site_id)
        front_matter = skill.get("front_matter") if isinstance(skill.get("front_matter"), dict) else {}
        policy = front_matter.get("job_identity")
        return dict(policy) if isinstance(policy, dict) else {}

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

    def _job_run_context_path(self, site_id: str, batch_id: str) -> Path:
        batch_key = safe_file_stem(batch_id or "adhoc_run")
        return self._job_runs_dir(site_id) / f"{batch_key}.context.json"

    def load_run_context(self, site_id: str, batch_id: str) -> dict[str, Any]:
        path = self._job_run_context_path(site_id, batch_id)
        payload = read_json(path)
        return payload if isinstance(payload, dict) else {}

    def save_run_context(self, site_id: str, batch_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.load_run_context(site_id, batch_id)
        current.update(payload or {})
        path = self._job_run_context_path(site_id, batch_id)
        ensure_dir(path.parent)
        write_json(path, current)
        return current

    @staticmethod
    def _safe_read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except Exception:
            return "<unreadable>"

    @staticmethod
    def _section_hash_with_fallback(markdown: str, section_title: str, fallback_title: str = "") -> str:
        text = section_text(markdown, section_title)
        if not text and fallback_title:
            text = section_text(markdown, fallback_title)
        return hash_text(text)

    def decision_context_versions(self, site_id: str) -> dict[str, str]:
        project_skill_path = self.project_root / "skills" / "search" / "jobs" / "SKILL.md"
        site_skill_path = self.site_skill_path(site_id)
        project_skill_text = self._safe_read_text(project_skill_path)
        site_skill_text = self._safe_read_text(site_skill_path)
        skill_policies = load_job_skill_policies(self.project_root, site_id)
        retrieval_policy_hash = policy_hash(skill_policies.get("retrieval_policy", {}))
        apply_candidate_policy_hash = policy_hash(skill_policies.get("apply_candidate_policy", {}))
        profile_hash = context_hash(
            {
                "persona": file_hash(self.workspace / "profile" / "persona.md"),
                "application_profile": file_hash(self.workspace / "profile" / "application_profile.md"),
            }
        )
        return {
            "cv_hash": file_hash(self.workspace / "cv" / "current" / "cv.md"),
            "profile_hash": profile_hash,
            "project_matching_policy_hash": self._section_hash_with_fallback(
                project_skill_text,
                MATCHING_POLICY_SECTION,
                "Apply",
            ),
            "site_policy_hash": section_hash(site_skill_text, SITE_POLICY_SECTION),
            "site_matching_policy_hash": self._section_hash_with_fallback(site_skill_text, MATCHING_POLICY_SECTION, "Apply"),
            "site_apply_workflow_hash": section_hash(site_skill_text, "Apply"),
            "retrieval_policy_hash": retrieval_policy_hash,
            "apply_candidate_policy_hash": apply_candidate_policy_hash,
        }

    def decision_context_hash(self, site_id: str) -> str:
        return context_hash(self.decision_context_versions(site_id))

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

    @staticmethod
    def _history_apply_state_for_application_status(value: Any) -> str:
        status = str(value or "").strip().lower()
        return {
            "submitted": "terminal_submitted",
            "already_applied": "terminal_already_applied",
            "filtered_out": "terminal_filtered_out",
            "apply_failed": "terminal_apply_failed",
            "blocked": "terminal_blocked",
            "resumable": "resumable_application",
            "rejected": "terminal_rejected",
            "closed": "terminal_closed",
            "withdrawn": "terminal_withdrawn",
        }.get(status, "")

    @classmethod
    def _review_text(cls, *values: object) -> str:
        return " ".join(
            cls._normalize_review_compare(value)
            for value in values
            if str(value or "").strip()
        )

    @classmethod
    def _review_indicates_non_terminal_application(cls, *, status: object, raw: object, stage: object) -> bool:
        text = cls._review_text(status, raw, stage)
        if not text:
            return False
        return any(
            marker in text
            for marker in (
                "not submitted",
                "not started",
                "not applied",
                "draft",
                "incomplete",
                "continue application",
                "resume application",
                "application started",
                "started application",
            )
        )

    @classmethod
    def _application_status_from_review(cls, *, status: object, raw: object, stage: object) -> str:
        text = cls._review_text(status, raw, stage)
        if not text:
            return ""
        if cls._review_indicates_non_terminal_application(status=status, raw=raw, stage=stage):
            return "resumable"
        if any(marker in text for marker in ("declined", "rejected", "inactive", "closed")):
            return "rejected"
        if "withdrawn" in text:
            return "withdrawn"
        if any(
            marker in text
            for marker in (
                "application received",
                "application in review",
                "in process",
                "in progress",
                "submitted",
                "active",
                "resume review",
                "assessment",
                "interview",
                "offer",
            )
        ):
            return "submitted"
        return ""

    @classmethod
    def _row_text(cls, row: dict[str, Any], fields: tuple[str, ...]) -> str:
        return " ".join(str(row.get(field) or "") for field in fields).lower()

    @classmethod
    def _indicates_closed_or_unavailable(cls, row: dict[str, Any]) -> bool:
        application_status = str(row.get("application_status") or "").strip().lower()
        apply_state = str(row.get("apply_state") or "").strip().lower()
        if application_status == "closed" or apply_state == "terminal_closed":
            return True
        text = cls._row_text(
            row,
            (
                "application_status_raw",
                "last_apply_error",
                "fit_reason",
                "match_reason_final",
                "decision_rule_source",
                "decision_rule_name",
            ),
        )
        return any(marker in text for marker in cls.CLOSED_OR_UNAVAILABLE_MARKERS)

    @classmethod
    def _has_filtered_out_conflict_text(cls, row: dict[str, Any]) -> bool:
        text = cls._row_text(
            row,
            (
                "match_label",
                "fit_reason",
                "match_reason_initial",
                "match_reason_final",
                "decision_rule_source",
                "decision_rule_name",
                "last_apply_error",
            ),
        )
        return any(marker in text for marker in cls.FILTERED_OUT_CONFLICT_MARKERS)

    @classmethod
    def _has_mixed_fulltime_internship_text(cls, row: dict[str, Any]) -> bool:
        text = cls._row_text(
            row,
            (
                "title",
                "employment_type",
                "job_type",
                "fit_reason",
                "match_reason_initial",
                "match_reason_final",
                "decision_rule_source",
                "decision_rule_name",
                "last_apply_error",
            ),
        )
        return bool(cls.MIXED_FULLTIME_INTERNSHIP_RE.search(text))

    @classmethod
    def _posted_window_exclusion(cls, row: dict[str, Any], policy: dict[str, Any] | None = None) -> bool:
        payload = dict(policy or {})
        try:
            window_days = int(payload.get("posted_window_days") or 0)
        except Exception:
            window_days = 0
        if window_days <= 0:
            return False
        posted_age = current_posted_age_observation(normalize_posted_fields(dict(row)))
        days = posted_age.get("days")
        if days is None:
            return str(payload.get("unknown_posted_age") or "review").strip().lower() == "filtered_out"
        try:
            age_days = int(days)
        except Exception:
            return False
        comparison = str(payload.get("posted_window_comparison") or "strictly_less_than").strip().lower()
        return age_days > window_days if comparison == "less_than_or_equal" else age_days >= window_days

    @classmethod
    def _normalize_filtered_out_decision(
        cls,
        row: dict[str, Any],
        *,
        apply_candidate_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_posted_fields(dict(row))
        application_status = str(normalized.get("application_status") or "").strip().lower()
        decision_status = str(normalized.get("decision_status") or "").strip().lower()
        match_label = str(normalized.get("match_label") or "").strip().lower()
        apply_state = str(normalized.get("apply_state") or "").strip().lower()

        submitted_raw_statuses = {
            "application received",
            "application submitted",
            "submitted successfully",
            "your application was submitted successfully",
            "thank you for applying",
        }
        if application_status in submitted_raw_statuses:
            if not str(normalized.get("application_status_raw") or "").strip():
                normalized["application_status_raw"] = str(normalized.get("application_status") or "").strip()
            normalized["application_status"] = "submitted"
            application_status = "submitted"

        if cls._indicates_closed_or_unavailable(normalized):
            normalized["application_status"] = "closed"
            normalized["apply_state"] = "terminal_closed"
            normalized["decision_reason_type"] = "closed"
            if decision_status in {"", "filtered_out", "not_needed", "not_applicable"}:
                normalized["decision_status"] = "closed"
            return normalized

        if application_status == "submitted":
            normalized["application_status"] = "submitted"
            normalized["apply_state"] = "terminal_submitted"
            normalized["decision_status"] = "recommended_apply"
            normalized["fit_apply"] = True
            if match_label in {"filtered_out", "posted_too_old", "not_recommended", "underfit", "low_fit"}:
                normalized.pop("match_label", None)
            if cls._has_filtered_out_conflict_text(normalized):
                for field in ("fit_reason", "match_reason_initial", "match_reason_final"):
                    value = str(normalized.get(field) or "").strip().lower()
                    if any(marker in value for marker in cls.FILTERED_OUT_CONFLICT_MARKERS):
                        normalized.pop(field, None)
            if cls._normalize_decision_reason_type(normalized.get("decision_reason_type")) in {
                "time",
                "cv",
                "matching_policy",
                "hard_excluded",
                "closed",
            }:
                normalized.pop("decision_reason_type", None)
            return normalized

        if application_status == "already_applied":
            normalized["application_status"] = "already_applied"
            normalized["apply_state"] = "terminal_already_applied"
            normalized["decision_status"] = "already_applied"
            if match_label in {"filtered_out", "posted_too_old", "not_recommended", "underfit", "low_fit"}:
                normalized.pop("match_label", None)
            if cls._has_filtered_out_conflict_text(normalized):
                for field in ("fit_reason", "match_reason_initial", "match_reason_final"):
                    value = str(normalized.get(field) or "").strip().lower()
                    if any(marker in value for marker in cls.FILTERED_OUT_CONFLICT_MARKERS):
                        normalized.pop(field, None)
            if cls._normalize_decision_reason_type(normalized.get("decision_reason_type")) in {
                "time",
                "cv",
                "matching_policy",
                "hard_excluded",
                "closed",
                "unknown",
            }:
                normalized.pop("decision_reason_type", None)
            normalized.pop("decision_context_hash", None)
            normalized.pop("context_versions", None)
            return normalized

        if application_status in {"rejected", "withdrawn"}:
            normalized["apply_state"] = f"terminal_{application_status}"
            if decision_status == "filtered_out":
                normalized.pop("decision_status", None)
            if cls._normalize_decision_reason_type(normalized.get("decision_reason_type")) in {
                "time",
                "cv",
                "matching_policy",
                "hard_excluded",
                "unknown",
            }:
                normalized.pop("decision_reason_type", None)
            return normalized

        filtered_signal = (
            application_status == "filtered_out"
            or decision_status == "filtered_out"
            or match_label == "filtered_out"
            or apply_state in {"filtered_out", "filtered_out_before_apply", "terminal_filtered_out", "not_started_filtered_out"}
        )
        if not filtered_signal:
            return normalized
        if decision_status in {"", "decided", "not_needed", "not_applicable"}:
            normalized["decision_status"] = "filtered_out"
        if application_status in {"", "not_started", "pending"}:
            normalized["application_status"] = "filtered_out"
            application_status = "filtered_out"
        if application_status == "filtered_out" and apply_state in {
            "",
            "filtered_out",
            "filtered_out_before_apply",
            "not_started",
            "not_started_filtered_out",
        }:
            normalized["apply_state"] = "terminal_filtered_out"
        reason_type = cls._infer_decision_reason_type(
            normalized,
            apply_candidate_policy=apply_candidate_policy,
        )
        if reason_type == "time":
            inferred_without_existing = cls._infer_decision_reason_type(
                normalized,
                prefer_existing=False,
                apply_candidate_policy=apply_candidate_policy,
            )
            if inferred_without_existing != "time" and not cls._posted_window_exclusion(normalized, apply_candidate_policy):
                reason_type = inferred_without_existing
        if reason_type:
            normalized["decision_reason_type"] = reason_type
        posted_age = cls._infer_posted_age(normalized)
        posted_age_days = posted_age.get("days")
        if posted_age_days is not None and normalized.get("observed_posted_age_days") in (None, ""):
            normalized["observed_posted_age_days"] = posted_age_days
        if posted_age.get("is_lower_bound") and normalized.get("observed_posted_age_is_lower_bound") in (None, ""):
            normalized["observed_posted_age_is_lower_bound"] = True
        return normalized

    @staticmethod
    def _normalize_decision_reason_type(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        return normalized if normalized in {"time", "cv", "matching_policy", "hard_excluded", "closed", "unknown"} else ""

    @classmethod
    def _infer_decision_reason_type(
        cls,
        row: dict[str, Any],
        *,
        prefer_existing: bool = True,
        apply_candidate_policy: dict[str, Any] | None = None,
    ) -> str:
        if cls._indicates_closed_or_unavailable(row):
            return "closed"
        existing = cls._normalize_decision_reason_type(row.get("decision_reason_type"))
        if prefer_existing and existing:
            if existing == "hard_excluded" and cls._has_mixed_fulltime_internship_text(row):
                return "matching_policy"
            return existing
        text = " ".join(
            str(row.get(field) or "")
            for field in (
                "title",
                "fit_reason",
                "match_reason_initial",
                "match_reason_final",
                "last_apply_error",
                "application_status",
                "decision_status",
                "decision_rule_name",
                "decision_rule_source",
            )
        ).lower()
        if cls._has_mixed_fulltime_internship_text(row):
            return "matching_policy"
        if any(
            token in text
            for token in ("intern", "internship", "new grad", "campus", "graduate program", "校招", "校园招聘", "应届", "实习")
        ):
            return "hard_excluded"
        cv_markers = (
            "cv",
            "resume",
            "persona",
            "profile",
            "full cv",
            "experience",
            "evidence",
            "background",
            "lacks",
            "lack ",
            "insufficient",
            "falls short",
            "does not show",
            "not clearly supported",
            "缺少",
            "不符合简历",
        )
        if any(token in text for token in cv_markers):
            return "cv"
        time_markers = (
            "posted age",
            "older than",
            "time window",
            "date posted",
            "outside apply candidate",
            "posted_too_old",
            "too old",
            "exceeds",
            "apply limit",
            "application window",
            "site rule limit",
            "30+ days",
            "发布时间",
        )
        if cls._posted_window_exclusion(row, apply_candidate_policy) or any(token in text for token in time_markers):
            return "time"
        if any(token in text for token in ("match", "matching", "threshold", "score", "strong match", "good match", "rule")):
            return "matching_policy"
        return "unknown"

    @staticmethod
    def _infer_posted_age_days(row: dict[str, Any]) -> int | None:
        posted_age = SiteStore._infer_posted_age(row)
        days = posted_age.get("days")
        return int(days) if days is not None else None

    @staticmethod
    def _infer_posted_age(row: dict[str, Any]) -> dict[str, Any]:
        return current_posted_age_observation(normalize_posted_fields(dict(row)))

    @classmethod
    def _normalize_history_row_for_write(cls, row: dict[str, Any]) -> dict[str, Any]:
        normalized = cls._normalize_filtered_out_decision(row)
        apply_state = cls._history_apply_state_for_application_status(normalized.get("application_status"))
        if apply_state:
            normalized["apply_state"] = apply_state
        return normalized

    def _history_strong_merge_keys(self, site_id: str, row: dict[str, Any]) -> list[str]:
        return [
            key
            for key in self.job_identity_keys(site_id, row)
            if key.startswith(("source-value|", "source|"))
        ]

    @classmethod
    def _history_latest_timestamp(cls, row: dict[str, Any]) -> str:
        for field in (
            "application_review_checked_at",
            "application_updated_at",
            "decision_updated_at",
            "last_seen_at",
            "posted_observed_at",
            "ts",
            "first_seen_at",
        ):
            value = str(row.get(field) or "").strip()
            if value:
                return value
        return ""

    @classmethod
    def _history_status_rank(cls, row: dict[str, Any]) -> int:
        status = str(row.get("application_status") or "").strip().lower()
        decision_status = str(row.get("decision_status") or "").strip().lower()
        apply_state = str(row.get("apply_state") or "").strip().lower()
        if status in {"rejected", "withdrawn", "closed"}:
            return 90
        if status == "submitted" or apply_state == "terminal_submitted":
            return 85
        if status == "already_applied" or apply_state == "terminal_already_applied":
            return 80
        if status in {"blocked", "apply_failed"} or apply_state in {"terminal_blocked", "terminal_apply_failed"}:
            return 60
        if status == "resumable" or apply_state == "resumable_application":
            return 55
        if status == "filtered_out" or decision_status == "filtered_out" or apply_state == "terminal_filtered_out":
            return 20
        return 10

    @classmethod
    def _history_primary_row(cls, left: dict[str, Any], right: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        left_review_ts = str(left.get("application_review_checked_at") or "").strip()
        right_review_ts = str(right.get("application_review_checked_at") or "").strip()
        if left_review_ts or right_review_ts:
            if right_review_ts > left_review_ts:
                return right, left
            if left_review_ts > right_review_ts:
                return left, right
        left_rank = cls._history_status_rank(left)
        right_rank = cls._history_status_rank(right)
        if right_rank > left_rank:
            return right, left
        if left_rank > right_rank:
            return left, right
        if cls._history_latest_timestamp(right) > cls._history_latest_timestamp(left):
            return right, left
        return left, right

    @staticmethod
    def _merge_earliest(*values: Any) -> str:
        candidates = sorted(str(value or "").strip() for value in values if str(value or "").strip())
        return candidates[0] if candidates else ""

    @staticmethod
    def _merge_latest(*values: Any) -> str:
        candidates = sorted(str(value or "").strip() for value in values if str(value or "").strip())
        return candidates[-1] if candidates else ""

    @classmethod
    def _merge_duplicate_history_rows(cls, primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
        merged = dict(primary)
        primary_non_terminal_review = cls._review_indicates_non_terminal_application(
            status=primary.get("application_review_status"),
            raw=primary.get("application_review_status_raw"),
            stage=primary.get("application_review_stage"),
        )
        status_fields = {"application_status", "decision_status", "apply_state"}
        fill_fields = (
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
            "posted_observed_at",
            "inferred_posted_date",
            "employment_type",
            "match_label",
            "apply_state",
            "site_job_id",
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
            "application_status_raw",
            "last_apply_error",
            "decision_reason_type",
            "application_review_status",
            "application_review_status_raw",
            "application_review_stage",
            "application_review_url",
            "application_review_batch_id",
            "application_review_checked_at",
            "previous_application_review_status",
            "previous_application_review_status_raw",
            "previous_application_review_stage",
            "history_source",
            "application_origin",
            "features_ref",
        )
        for field in fill_fields:
            if primary_non_terminal_review and field in status_fields:
                continue
            if str(merged.get(field) or "").strip():
                continue
            value = secondary.get(field)
            if value not in (None, ""):
                merged[field] = value

        first_seen = cls._merge_earliest(primary.get("first_seen_at"), secondary.get("first_seen_at"))
        if first_seen:
            merged["first_seen_at"] = first_seen
        for field in (
            "last_seen_at",
            "ts",
            "posted_observed_at",
            "application_review_checked_at",
            "application_updated_at",
            "decision_updated_at",
        ):
            latest = cls._merge_latest(primary.get(field), secondary.get(field))
            if latest:
                merged[field] = latest

        try:
            merged["seen_count"] = max(int(primary.get("seen_count") or 0), int(secondary.get("seen_count") or 0))
        except Exception:
            pass
        for field in ("observed_posted_age_days",):
            if merged.get(field) in (None, "") and secondary.get(field) not in (None, ""):
                try:
                    merged[field] = int(float(secondary.get(field)))
                except Exception:
                    pass
        for field in ("observed_posted_age_is_lower_bound", "fit_apply", "is_active", "application_review_status_changed"):
            if field not in merged and field in secondary:
                merged[field] = secondary[field]
        if isinstance(primary.get("context_versions"), dict):
            merged["context_versions"] = dict(primary["context_versions"])
        elif str(merged.get("application_status") or "").strip().lower() == "filtered_out" and isinstance(
            secondary.get("context_versions"), dict
        ):
            merged["context_versions"] = dict(secondary["context_versions"])
        if (
            not str(merged.get("decision_context_hash") or "").strip()
            and str(merged.get("application_status") or "").strip().lower() == "filtered_out"
        ):
            value = str(secondary.get("decision_context_hash") or "").strip()
            if value:
                merged["decision_context_hash"] = value
        return cls._normalize_history_row_for_write(merged)

    def _compact_history_jobs_for_write(self, site_id: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        compacted: list[dict[str, Any]] = []
        key_to_index: dict[str, int] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            normalized = self._normalize_history_row_for_write(row)
            strong_keys = self._history_strong_merge_keys(site_id, normalized)
            target_idx: int | None = None
            for key in strong_keys:
                if key in key_to_index:
                    target_idx = key_to_index[key]
                    break
            if target_idx is None:
                target_idx = len(compacted)
                compacted.append(normalized)
            else:
                primary, secondary = self._history_primary_row(compacted[target_idx], normalized)
                compacted[target_idx] = self._merge_duplicate_history_rows(primary, secondary)
            for key in self._history_strong_merge_keys(site_id, compacted[target_idx]):
                key_to_index[key] = target_idx
        return compacted

    @classmethod
    def _is_runtime_stop_apply_failed(cls, *, status: str, error_text: str) -> bool:
        if str(status or "").strip().lower() != "apply_failed":
            return False
        lowered = str(error_text or "").strip().lower()
        return bool(lowered) and any(marker in lowered for marker in cls.RUNTIME_STOP_ERROR_MARKERS)

    def _write_history_jobs(self, site_id: str, rows: list[dict[str, Any]]) -> None:
        path = self._history_jobs_path(site_id)
        ensure_dir(path.parent)
        compacted_rows = self._compact_history_jobs_for_write(site_id, rows)
        path.write_text(
            json.dumps(
                compacted_rows,
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
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
                    (root / "jobs" / "catalog.jsonl").write_text("", encoding="utf-8")
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
        skill = self._migrate_legacy_site_skill(site_id)
        return skill.exists() and bool(skill.read_text(encoding="utf-8").strip())

    def ensure_skill_template(self, site_id: str) -> tuple[Path, bool]:
        skill = self._migrate_legacy_site_skill(site_id)
        if skill.exists():
            return skill, False
        ensure_dir(skill.parent)
        if not skill.exists():
            skill.write_text(self._default_site_skill_text(site_id), encoding="utf-8")
        return skill, True

    def _normalize_url(self, url: str, *, identity_policy: dict[str, Any] | None = None) -> str:
        raw = (url or "").strip()
        if not raw:
            return ""
        try:
            parsed = urlparse(raw)
        except Exception:
            return raw
        if not parsed.scheme or not parsed.netloc:
            return raw
        return normalize_identity_url(raw, identity_policy)

    @staticmethod
    def _coerce_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"", "0", "false", "no", "off", "none", "null"}:
            return False
        return True

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

    @staticmethod
    def _normalize_review_stage(value: object) -> str:
        text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
        return text.strip("_")

    @staticmethod
    def _normalize_review_compare(value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip()).lower()

    @classmethod
    def _extract_workday_job_numbers(cls, *values: object) -> list[str]:
        numbers: list[str] = []
        seen: set[str] = set()
        for raw in values:
            text = str(raw or "")
            if not text:
                continue
            for match in cls.WORKDAY_JOB_NUMBER_RE.findall(text):
                value = cls._normalize_job_text(match)
                if value and value not in seen:
                    seen.add(value)
                    numbers.append(value)
        return numbers

    @classmethod
    def _site_job_id_aliases(cls, value: object) -> list[str]:
        text = cls._normalize_job_text(str(value or ""))
        if not text:
            return []
        aliases = [text]
        for match in cls.YEAR_PREFIXED_JOB_ID_RE.finditer(text):
            suffix = cls._normalize_job_text(match.group(1))
            if suffix and suffix not in aliases:
                aliases.append(suffix)
        return aliases

    @classmethod
    def _is_site_job_id_like(cls, value: object) -> bool:
        text = cls._normalize_job_text(str(value or ""))
        return bool(text and cls.SITE_JOB_ID_LIKE_RE.fullmatch(text))

    def _infer_site_job_id_from_url(self, url: object, *, identity_policy: dict[str, Any] | None = None) -> str:
        return infer_site_job_id_from_url(url, identity_policy)

    def _is_application_review_non_job_url(self, url: object) -> bool:
        normalized = self._normalize_url(str(url or ""))
        if not normalized:
            return False
        try:
            parsed = urlparse(normalized)
        except Exception:
            return False
        query = parsed.query.lower()
        if "bga=true" in query:
            return True
        segments = {segment.strip().lower() for segment in parsed.path.split("/") if segment.strip()}
        return bool(segments & self.APPLICATION_REVIEW_NON_JOB_PATH_SEGMENTS)

    def _is_real_job_posting_url(self, url: object) -> bool:
        normalized = self._normalize_url(str(url or ""))
        if not normalized or self._is_application_review_non_job_url(normalized):
            return False
        try:
            parsed = urlparse(normalized)
        except Exception:
            return False
        path = parsed.path.lower()
        return bool(re.search(r"/(?:careers/)?job[s]?/", path)) or bool(self._infer_site_job_id_from_url(normalized))

    def _build_application_review_history_row(
        self,
        *,
        site_id: str,
        review_row: dict[str, Any],
        session_id: str,
        turn_id: str,
        batch_id: str,
        checked_at: str,
    ) -> dict[str, Any] | None:
        title = str(review_row.get("title") or "").strip()
        raw_url = self._normalize_url(str(review_row.get("url") or ""))
        site_job_id = str(review_row.get("site_job_id") or review_row.get("source_job_id") or "").strip()
        review_status = str(review_row.get("application_review_status") or "").strip()
        review_raw = str(review_row.get("application_review_status_raw") or "").strip()
        review_stage = str(review_row.get("application_review_stage") or "").strip()
        application_status = self._application_status_from_review(
            status=review_status,
            raw=review_raw,
            stage=review_stage,
        )
        job_url = raw_url if self._is_real_job_posting_url(raw_url) else ""
        review_url = raw_url if raw_url else ""
        if not site_job_id and not job_url:
            return None

        row: dict[str, Any] = {
            "ts": checked_at,
            "batch_id": str(batch_id or ""),
            "session_id": session_id,
            "turn_id": turn_id,
            "site_id": site_id,
            "employer": site_id,
            "title": title,
            "history_source": "application_status_review",
            "application_origin": "external_or_prior",
            "jd_sync_status": "missing",
            "first_seen_at": checked_at,
            "last_seen_at": checked_at,
            "seen_count": 1,
            "features_ref": "",
        }
        if application_status:
            row["application_status"] = application_status
            apply_state = self._history_apply_state_for_application_status(application_status)
            if apply_state:
                row["apply_state"] = apply_state
            if application_status == "resumable":
                row["decision_status"] = "recommended_apply"
        if batch_id:
            row["last_seen_batch_id"] = str(batch_id)
        if site_job_id:
            row["site_job_id"] = site_job_id
        if job_url:
            row["url"] = job_url
        if review_url:
            row["application_review_url"] = review_url

        job_id, canonical_job_id = self._history_ids(site_id, row)
        row["job_id"] = job_id
        row["canonical_job_id"] = canonical_job_id
        return row

    def _job_source_refs(self, row: dict[str, Any], *, identity_policy: dict[str, Any] | None = None) -> list[str]:
        if not isinstance(row, dict):
            return []
        refs: list[str] = []
        seen: set[str] = set()

        def add_ref(prefix: str, value: object) -> None:
            normalized = self._normalize_job_text(str(value or ""))
            if not normalized:
                return
            ref = f"{prefix}:{normalized}"
            if ref not in seen:
                seen.add(ref)
                refs.append(ref)

        explicit_fields = (
            "source_job_ref",
            "source_job_id",
            "site_job_id",
            "job_number",
            "job_number_raw",
            "requisition_id",
            "requisition_number",
            "pid",
        )
        for field in explicit_fields:
            for alias in self._site_job_id_aliases(row.get(field)):
                add_ref(field, alias)

        url = self._normalize_url(str(row.get("url") or ""), identity_policy=identity_policy)
        inferred_url_job_id = infer_site_job_id_from_url(url, identity_policy)
        for alias in self._site_job_id_aliases(inferred_url_job_id):
            add_ref("url-job", alias)
        if url:
            try:
                parsed = urlparse(url)
            except Exception:
                parsed = None
            if parsed is not None:
                query = {k.lower(): v for k, v in parse_qsl(parsed.query, keep_blank_values=False)}
                for field in ("pid", "jobid", "job_id", "jobnumber", "job_number", "requisitionid", "reqid"):
                    for alias in self._site_job_id_aliases(query.get(field, "")):
                        add_ref(field, alias)
                match = re.search(r"/job[s]?/([^/?#]+)", parsed.path, flags=re.IGNORECASE)
                if match and self._is_site_job_id_like(match.group(1)):
                    for alias in self._site_job_id_aliases(match.group(1)):
                        add_ref("path-job", alias)
                path_parts = [part for part in parsed.path.split("/") if part]
                skip_path_tail = bool(parsed.fragment and inferred_url_job_id)
                if path_parts and not skip_path_tail and self._is_site_job_id_like(path_parts[-1]):
                    for alias in self._site_job_id_aliases(path_parts[-1]):
                        add_ref("path-tail", alias)

        job_number_fields = (
            *explicit_fields,
            "url",
            "card_text",
            "match_label",
        )
        for value in self._extract_workday_job_numbers(*(row.get(field) for field in job_number_fields), url):
            add_ref("workday_job_number", value)
        return refs

    def _job_source_ref(self, row: dict[str, Any]) -> str:
        refs = self._job_source_refs(row)
        return refs[0] if refs else ""

    def _history_match_keys(self, site_id: str, row: dict[str, Any]) -> list[str]:
        if not isinstance(row, dict):
            return []
        site = safe_file_stem(site_id)
        keys: list[str] = []
        identity_policy = self._job_identity_policy(site_id)

        url = self._normalize_url(str(row.get("url") or ""), identity_policy=identity_policy)
        if url:
            keys.append(f"url|{site}|{url}")

        strong_source_value_prefixes = {
            "source_job_ref",
            "source_job_id",
            "site_job_id",
            "job_number",
            "job_number_raw",
            "requisition_id",
            "requisition_number",
            "pid",
            "jobid",
            "job_id",
            "jobnumber",
            "requisitionid",
            "reqid",
            "workday_job_number",
            "url-job",
            "path-job",
            "path-tail",
        }
        for source_ref in self._job_source_refs(row, identity_policy=identity_policy):
            keys.append(f"source|{site}|{source_ref}")
            source_prefix, _, source_value = source_ref.partition(":")
            if source_value and source_prefix in strong_source_value_prefixes:
                keys.append(f"source-value|{site}|{source_value}")

        title = self._normalize_job_text(str(row.get("title") or ""))
        location = self._normalize_job_text(str(row.get("location") or ""))
        posted = self._normalize_job_text(str(row.get("posted_label") or ""))
        if title and location and posted:
            keys.append(f"fallback|{site}|{title}|{location}|{posted}")

        deduped: list[str] = []
        seen: set[str] = set()
        for key in keys:
            if key and key not in seen:
                seen.add(key)
                deduped.append(key)
        return deduped

    def job_identity_keys(self, site_id: str, row: dict[str, Any]) -> list[str]:
        """Return history-compatible keys ordered for run/apply-plan dedupe.

        History matching keeps URL keys because they are useful fallback evidence.
        Apply planning needs stronger source IDs first so URL query differences do
        not split the same requisition into one terminal row and one open row.
        """
        candidate = dict(row)
        if not str(candidate.get("site_job_id") or "").strip():
            inferred = self._infer_site_job_id_from_url(
                candidate.get("url") or "",
                identity_policy=self._job_identity_policy(site_id),
            )
            if inferred:
                candidate["site_job_id"] = inferred
        keys = self._history_match_keys(site_id, candidate)
        strong_prefixes = ("source-value|", "source|")
        strong = [key for key in keys if key.startswith(strong_prefixes)]
        rest = [key for key in keys if not key.startswith(strong_prefixes)]
        return strong + rest

    def _history_identity_seed(self, site_id: str, row: dict[str, Any]) -> str:
        keys = self._history_match_keys(site_id, row)
        if keys:
            return keys[0]
        site = safe_file_stem(site_id)
        title = self._normalize_job_text(str(row.get("title") or ""))
        location = self._normalize_job_text(str(row.get("location") or ""))
        posted = self._normalize_job_text(str(row.get("posted_label") or ""))
        return f"opaque|{site}|{title}|{location}|{posted}"

    def _history_ids(self, site_id: str, row: dict[str, Any]) -> tuple[str, str]:
        seed = self._history_identity_seed(site_id, row)
        canonical_job_id = "cj_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
        job_id = "job_" + hashlib.sha1(f"{site_id}|{seed}".encode("utf-8")).hexdigest()[:16]
        return job_id, canonical_job_id

    @classmethod
    def _merge_job_row(cls, base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        incoming = normalize_posted_fields(dict(incoming))
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
            "posted_observed_at",
            "inferred_posted_date",
            "employment_type",
            "match_label",
            "apply_state",
            "site_job_id",
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
            "application_status_raw",
            "last_apply_error",
            "decision_reason_type",
            "decision_context_hash",
            "block_reason_type",
            "failure_pattern",
            "loop_control_action",
            "recommended_target",
            "evidence",
            "refinement_hint",
        ):
            value = str(incoming.get(field) or "").strip()
            if value:
                merged[field] = value
        if isinstance(incoming.get("context_versions"), dict):
            merged["context_versions"] = dict(incoming["context_versions"])
        if incoming.get("observed_posted_age_days") not in (None, ""):
            try:
                merged["observed_posted_age_days"] = int(float(incoming.get("observed_posted_age_days")))
            except Exception:
                pass
        if incoming.get("observed_posted_age_is_lower_bound") not in (None, ""):
            merged["observed_posted_age_is_lower_bound"] = cls._coerce_bool(incoming.get("observed_posted_age_is_lower_bound"))
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
        incoming = dict(incoming)
        site_key = str(incoming.get("site_id") or root.name)
        skill_policies = load_job_skill_policies(self.project_root, site_key)
        apply_candidate_policy = skill_policies.get("apply_candidate_policy", {})
        if not str(incoming.get("site_job_id") or "").strip():
            inferred_site_job_id = self._infer_site_job_id_from_url(
                incoming.get("url") or base.get("url") or "",
                identity_policy=self._job_identity_policy(site_key),
            )
            if inferred_site_job_id:
                incoming["site_job_id"] = inferred_site_job_id
        incoming = self._normalize_filtered_out_decision(incoming, apply_candidate_policy=apply_candidate_policy)
        incoming_application_status = str(incoming.get("application_status") or "").strip().lower()
        incoming_decision_status = str(incoming.get("decision_status") or "").strip().lower()
        if (
            (incoming_application_status == "filtered_out" or incoming_decision_status == "filtered_out")
            and not str(incoming.get("decision_context_hash") or "").strip()
        ):
            context_versions = self.decision_context_versions(site_key)
            incoming["context_versions"] = context_versions
            incoming["decision_context_hash"] = context_hash(context_versions)
        elif (
            (incoming_application_status == "filtered_out" or incoming_decision_status == "filtered_out")
            and not isinstance(incoming.get("context_versions"), dict)
        ):
            incoming["context_versions"] = self.decision_context_versions(str(incoming.get("site_id") or root.name))

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
                merged[field] = self._coerce_bool(incoming.get(field))
        if isinstance(incoming.get("context_versions"), dict):
            merged["context_versions"] = dict(incoming["context_versions"])

        description = str(incoming.get("description") or "").strip()
        if description:
            merged["description_ref"] = self._persist_description_ref(
                root=root,
                job_id=job_id,
                description=description,
            )
        return self._normalize_filtered_out_decision(merged, apply_candidate_policy=apply_candidate_policy)

    def _history_enrichment_reasons(self, row: dict[str, Any]) -> list[str]:
        if not isinstance(row, dict):
            return []
        reasons: list[str] = []

        def add(reason: str) -> None:
            if reason and reason not in reasons:
                reasons.append(reason)

        history_source = self._normalize_job_text(str(row.get("history_source") or ""))
        application_origin = self._normalize_job_text(str(row.get("application_origin") or ""))
        jd_sync_status = self._normalize_job_text(str(row.get("jd_sync_status") or ""))
        url_quality = self._normalize_job_text(str(row.get("url_quality") or ""))
        description_ref = str(row.get("description_ref") or "").strip()
        url = self._normalize_url(str(row.get("url") or ""))
        application_review_url = self._normalize_url(str(row.get("application_review_url") or ""))

        if jd_sync_status in {"missing", "partial"}:
            add(f"{jd_sync_status}_jd")
        if (
            not description_ref
            and (
                history_source == "application_status_review"
                or application_origin == "external_or_prior"
                or jd_sync_status in {"missing", "partial"}
            )
        ):
            add("missing_description")
        if not url:
            add("missing_real_job_url")
        elif url_quality == "dashboard_or_non_job" or self._is_application_review_non_job_url(url):
            add("dashboard_url_only")
        if history_source == "application_status_review" and not description_ref:
            add("review_only_history")
        if application_review_url and not url:
            add("review_url_only")
        return reasons

    def classify_history_matches(self, site_id: str, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        history_rows = self._load_history_jobs(site_id)
        by_job_id, by_canonical_job_id, by_match_key = self._history_resolution_indexes(site_id, history_rows)
        classifications: list[dict[str, Any]] = []
        for job in jobs:
            if not isinstance(job, dict):
                classifications.append(
                    {
                        "history_match_status": "new",
                        "matched_job_id": "",
                        "enrichment_reasons": [],
                    }
                )
                continue
            match_idx = self._resolve_history_row_index(
                site_id,
                job,
                by_job_id=by_job_id,
                by_canonical_job_id=by_canonical_job_id,
                by_match_key=by_match_key,
            )
            if match_idx is None:
                classifications.append(
                    {
                        "history_match_status": "new",
                        "matched_job_id": "",
                        "enrichment_reasons": [],
                    }
                )
                continue
            matched = history_rows[match_idx]
            reasons = self._history_enrichment_reasons(matched)
            classifications.append(
                {
                    "history_match_status": "existing_needs_enrichment" if reasons else "existing_complete",
                    "matched_job_id": str(matched.get("job_id") or ""),
                    "matched_canonical_job_id": str(matched.get("canonical_job_id") or ""),
                    "matched_title": str(matched.get("title") or ""),
                    "matched_url": str(matched.get("url") or ""),
                    "decision_status": str(matched.get("decision_status") or ""),
                    "apply_state": str(matched.get("apply_state") or ""),
                    "application_status": str(matched.get("application_status") or ""),
                    "application_status_raw": str(matched.get("application_status_raw") or ""),
                    "application_review_status": str(matched.get("application_review_status") or ""),
                    "application_review_status_raw": str(matched.get("application_review_status_raw") or ""),
                    "enrichment_reasons": reasons,
                }
            )
        return classifications

    def preview_history_new_flags(self, site_id: str, jobs: list[dict[str, Any]]) -> list[bool]:
        return [row.get("history_match_status") == "new" for row in self.classify_history_matches(site_id, jobs)]

    def _history_resolution_indexes(
        self,
        site_id: str,
        rows: list[dict[str, Any]],
    ) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
        by_job_id: dict[str, int] = {}
        by_canonical_job_id: dict[str, int] = {}
        by_match_key: dict[str, int] = {}
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            job_id = str(row.get("job_id") or "").strip()
            canonical_job_id = str(row.get("canonical_job_id") or "").strip()
            if job_id:
                by_job_id.setdefault(job_id, idx)
            if canonical_job_id:
                by_canonical_job_id.setdefault(canonical_job_id, idx)
            for key in self._history_match_keys(site_id, row):
                by_match_key.setdefault(key, idx)
        return by_job_id, by_canonical_job_id, by_match_key

    def _resolve_history_row_index(
        self,
        site_id: str,
        row: dict[str, Any],
        *,
        by_job_id: dict[str, int],
        by_canonical_job_id: dict[str, int],
        by_match_key: dict[str, int],
    ) -> int | None:
        if not isinstance(row, dict):
            return None
        job_id = str(row.get("job_id") or "").strip()
        if job_id and job_id in by_job_id:
            return by_job_id[job_id]
        canonical_job_id = str(row.get("canonical_job_id") or "").strip()
        if canonical_job_id and canonical_job_id in by_canonical_job_id:
            return by_canonical_job_id[canonical_job_id]
        for key in self._history_match_keys(site_id, row):
            if key in by_match_key:
                return by_match_key[key]
        return None

    def match_history_rows(self, site_id: str, jobs: list[dict[str, Any]]) -> list[dict[str, Any] | None]:
        rows = self._load_history_jobs(site_id)
        by_job_id, by_canonical_job_id, by_match_key = self._history_resolution_indexes(site_id, rows)
        matches: list[dict[str, Any] | None] = []
        for job in jobs:
            match_idx = self._resolve_history_row_index(
                site_id,
                job if isinstance(job, dict) else {},
                by_job_id=by_job_id,
                by_canonical_job_id=by_canonical_job_id,
                by_match_key=by_match_key,
            )
            if match_idx is None:
                matches.append(None)
                continue
            matches.append(dict(rows[match_idx]))
        return matches

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

    @classmethod
    def _needs_decision_metadata_normalization(
        cls,
        row: dict[str, Any],
        *,
        apply_candidate_policy: dict[str, Any] | None = None,
    ) -> bool:
        if not isinstance(row, dict):
            return False
        application_status = str(row.get("application_status") or "").strip().lower()
        decision_status = str(row.get("decision_status") or "").strip().lower()
        apply_state = str(row.get("apply_state") or "").strip().lower()
        if cls._indicates_closed_or_unavailable(row) and (
            application_status != "closed" or apply_state != "terminal_closed"
        ):
            return True
        if application_status == "submitted":
            if apply_state != "terminal_submitted" or decision_status != "recommended_apply":
                return True
            if cls._has_filtered_out_conflict_text(row):
                return True
            if cls._normalize_decision_reason_type(row.get("decision_reason_type")) in {
                "time",
                "cv",
                "matching_policy",
                "hard_excluded",
                "closed",
            }:
                return True
        if application_status != "filtered_out" and decision_status != "filtered_out":
            return False
        if application_status != "filtered_out" or apply_state != "terminal_filtered_out":
            return True
        reason_type = cls._normalize_decision_reason_type(row.get("decision_reason_type"))
        if not reason_type:
            return True
        if reason_type == "hard_excluded" and cls._has_mixed_fulltime_internship_text(row):
            return True
        if (
            reason_type == "time"
            and not cls._posted_window_exclusion(row, apply_candidate_policy)
            and cls._infer_decision_reason_type(
                row,
                prefer_existing=False,
                apply_candidate_policy=apply_candidate_policy,
            )
            != "time"
        ):
            return True
        if not isinstance(row.get("context_versions"), dict):
            return True
        if not str(row.get("decision_context_hash") or "").strip():
            return True
        return False

    def history_normalization_candidates(self, site_id: str) -> list[dict[str, Any]]:
        rows = self._load_history_jobs(site_id)
        skill_policies = load_job_skill_policies(self.project_root, site_id)
        apply_candidate_policy = skill_policies.get("apply_candidate_policy", {})
        candidates: list[dict[str, Any]] = []
        for row in rows:
            if not self._needs_decision_metadata_normalization(row, apply_candidate_policy=apply_candidate_policy):
                continue
            candidates.append(
                {
                    "job_id": str(row.get("job_id") or ""),
                    "canonical_job_id": str(row.get("canonical_job_id") or ""),
                    "site_job_id": str(row.get("site_job_id") or ""),
                    "title": str(row.get("title") or ""),
                    "url": str(row.get("url") or ""),
                    "application_status": str(row.get("application_status") or ""),
                    "decision_status": str(row.get("decision_status") or ""),
                    "decision_reason_type": str(row.get("decision_reason_type") or ""),
                    "decision_context_hash": str(row.get("decision_context_hash") or ""),
                }
            )
        return candidates

    def normalize_history_decision_metadata(self, site_id: str, *, max_rows: int = 10) -> dict[str, Any]:
        rows = self._load_history_jobs(site_id)
        skill_policies = load_job_skill_policies(self.project_root, site_id)
        apply_candidate_policy = skill_policies.get("apply_candidate_policy", {})
        candidate_indexes = [
            idx
            for idx, row in enumerate(rows)
            if self._needs_decision_metadata_normalization(row, apply_candidate_policy=apply_candidate_policy)
        ]
        if not candidate_indexes:
            return {"status": "none", "count": 0, "updated": 0}
        if len(candidate_indexes) > max_rows:
            return {"status": "needs_review", "count": len(candidate_indexes), "updated": 0}

        updated = 0
        for idx in candidate_indexes:
            row = dict(rows[idx])
            row = self._normalize_filtered_out_decision(row, apply_candidate_policy=apply_candidate_policy)
            if str(row.get("application_status") or "").strip().lower() == "filtered_out" or str(
                row.get("decision_status") or ""
            ).strip().lower() == "filtered_out":
                reason_type = self._infer_decision_reason_type(
                    row,
                    apply_candidate_policy=apply_candidate_policy,
                )
                if reason_type == "time":
                    inferred_without_existing = self._infer_decision_reason_type(
                        row,
                        prefer_existing=False,
                        apply_candidate_policy=apply_candidate_policy,
                    )
                    if inferred_without_existing != "time" and not self._posted_window_exclusion(
                        row,
                        apply_candidate_policy,
                    ):
                        reason_type = inferred_without_existing
                if reason_type:
                    row["decision_reason_type"] = reason_type
            if not isinstance(row.get("context_versions"), dict):
                legacy_context = str(row.get("decision_context_hash") or "").strip()
                row["context_versions"] = (
                    {"legacy_decision_context_hash": legacy_context}
                    if legacy_context
                    else {"legacy_context": "unknown"}
                )
            if not str(row.get("decision_context_hash") or "").strip():
                row["decision_context_hash"] = context_hash(row["context_versions"])
            posted_age = self._infer_posted_age(row)
            posted_age_days = posted_age.get("days")
            if posted_age_days is not None and row.get("observed_posted_age_days") in (None, ""):
                row["observed_posted_age_days"] = posted_age_days
            if posted_age.get("is_lower_bound") and row.get("observed_posted_age_is_lower_bound") in (None, ""):
                row["observed_posted_age_is_lower_bound"] = True
            rows[idx] = row
            updated += 1
        self._write_history_jobs(site_id, rows)
        return {"status": "updated", "count": len(candidate_indexes), "updated": updated}

    @classmethod
    def _context_changed_for_decision_reason(
        cls,
        *,
        reason_type: str,
        current_context_versions: dict[str, Any],
        history_context_versions: dict[str, Any],
        current_decision_context_hash: str,
        history_decision_context_hash: str,
    ) -> bool:
        if not history_context_versions:
            return bool(
                current_decision_context_hash
                and history_decision_context_hash
                and current_decision_context_hash != history_decision_context_hash
            )
        if "legacy_context" in history_context_versions or "legacy_decision_context_hash" in history_context_versions:
            return True
        normalized_reason = cls._normalize_decision_reason_type(reason_type) or "unknown"
        keys_by_reason = {
            "time": ("site_policy_hash", "apply_candidate_policy_hash"),
            "hard_excluded": ("site_policy_hash", "project_matching_policy_hash", "site_matching_policy_hash"),
            "cv": ("cv_hash", "profile_hash", "project_matching_policy_hash", "site_matching_policy_hash"),
            "matching_policy": ("cv_hash", "profile_hash", "project_matching_policy_hash", "site_matching_policy_hash"),
        }
        keys = keys_by_reason.get(normalized_reason)
        if not keys:
            return bool(
                current_decision_context_hash
                and history_decision_context_hash
                and current_decision_context_hash != history_decision_context_hash
            )
        return any(
            str(current_context_versions.get(key) or "") != str(history_context_versions.get(key) or "")
            for key in keys
        )

    def apply_list_history_candidates(
        self,
        site_id: str,
        *,
        current_context_versions: dict[str, Any],
        current_decision_context_hash: str,
        apply_candidate_policy: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        rows = self._load_history_jobs(site_id)
        posted_policy = normalize_posted_window_policy(apply_candidate_policy)
        candidates: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            row = normalize_posted_fields(dict(row))
            application_status = str(row.get("application_status") or "").strip().lower()
            decision_status = str(row.get("decision_status") or "").strip().lower()
            apply_state = str(row.get("apply_state") or "").strip().lower()
            if application_status in {"already_applied", "submitted", "rejected", "closed", "withdrawn"}:
                continue
            include = False
            reason = ""
            if application_status == "resumable" or apply_state == "resumable_application":
                include = True
                reason = "resume unsubmitted application from application review"
            elif application_status in {"apply_failed", "blocked"}:
                include = True
                reason = f"retry previous {application_status}"
            elif application_status == "filtered_out" or decision_status == "filtered_out":
                reason_type = self._normalize_decision_reason_type(row.get("decision_reason_type")) or "unknown"
                include = self._context_changed_for_decision_reason(
                    reason_type=reason_type,
                    current_context_versions=current_context_versions,
                    history_context_versions=row.get("context_versions") if isinstance(row.get("context_versions"), dict) else {},
                    current_decision_context_hash=current_decision_context_hash,
                    history_decision_context_hash=str(row.get("decision_context_hash") or ""),
                )
                if include:
                    reason = f"history filtered_out requeued because {reason_type} context changed"
            elif not application_status and not decision_status and apply_state in {"", "not_applied"}:
                if not self._posted_window_exclusion(row, posted_policy):
                    include = True
                    reason = "history unprocessed within apply candidate policy"
            if not include:
                continue
            candidate = dict(row)
            for key in (
                "application_status",
                "decision_status",
                "apply_state",
                "last_apply_error",
                "decision_context_hash",
                "context_versions",
            ):
                candidate.pop(key, None)
            candidate["requeued_from_history_job_id"] = str(row.get("job_id") or "")
            candidate["requeue_reason"] = reason
            candidate["site_id"] = site_id
            candidates.append(candidate)
        return candidates

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
        identity_policy = self._job_identity_policy(site_id)
        ensure_dir(root / "jobs" / "runs")
        ensure_dir(root / "jobs" / "descriptions")
        run_store = JSONLStore(self._job_run_path(site_id, batch_id))
        rows = run_store.read_all()
        def run_dedupe_keys(row: dict[str, Any]) -> list[str]:
            keys: list[str] = []
            seen: set[str] = set()

            def add(key: str) -> None:
                if key and key not in seen:
                    seen.add(key)
                    keys.append(key)

            url = self._normalize_url(str(row.get("url") or ""), identity_policy=identity_policy)
            site_job_id = str(row.get("site_job_id") or row.get("source_job_id") or "").strip()
            if not site_job_id:
                site_job_id = self._infer_site_job_id_from_url(url, identity_policy=identity_policy)
            if site_job_id:
                for alias in self._site_job_id_aliases(site_job_id):
                    add(f"site_job_id:{alias}")
            if url:
                add(f"url:{url}")
            return keys

        run_index: dict[str, int] = {}
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            for key in run_dedupe_keys(row):
                run_index.setdefault(key, idx)

        appended_rows: list[dict[str, Any]] = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            title = str(job.get("title") or "").strip()
            url = self._normalize_url(str(job.get("url") or ""), identity_policy=identity_policy)
            employer = str(job.get("employer") or job.get("company") or root.name)
            if not title and not url:
                continue
            site_job_id = str(job.get("site_job_id") or job.get("source_job_id") or "").strip()
            if not site_job_id:
                site_job_id = self._infer_site_job_id_from_url(url, identity_policy=identity_policy)
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
                "site_job_id": site_job_id,
                "description_ref": description_ref,
            }
            snapshot = normalize_posted_fields(snapshot, observed_at=now)
            dedupe_keys = run_dedupe_keys(snapshot)
            row_index = next((run_index[key] for key in dedupe_keys if key in run_index), None)
            if row_index is not None:
                merged = self._merge_run_job_row(
                    base=rows[row_index],
                    incoming=snapshot,
                    root=root,
                    job_id=job_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    batch_id=batch_id,
                )
                rows[row_index] = merged
                for key in run_dedupe_keys(merged):
                    run_index.setdefault(key, row_index)
                appended_rows.append(merged)
            else:
                row_index = len(rows)
                for key in dedupe_keys:
                    run_index.setdefault(key, row_index)
                rows.append(snapshot)
                appended_rows.append(snapshot)

        run_store.write_all([row for row in rows if isinstance(row, dict)])
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
        identity_policy = self._job_identity_policy(site_id)
        run_store = JSONLStore(self._job_run_path(site_id, batch_id))
        rows = [row for row in run_store.read_all() if isinstance(row, dict)]

        def run_update_keys(row: dict[str, Any]) -> list[str]:
            candidate = dict(row)
            if not str(candidate.get("site_job_id") or "").strip():
                inferred = self._infer_site_job_id_from_url(
                    candidate.get("url") or "",
                    identity_policy=identity_policy,
                )
                if inferred:
                    candidate["site_job_id"] = inferred
            keys: list[str] = []
            job_id = str(candidate.get("job_id") or "").strip()
            if job_id:
                keys.append(f"job_id|{site_id}|{job_id}")
            keys.extend(self.job_identity_keys(site_id, candidate))
            deduped: list[str] = []
            seen: set[str] = set()
            for key in keys:
                if key and key not in seen:
                    seen.add(key)
                    deduped.append(key)
            return deduped

        index: dict[str, int] = {}
        for idx, row in enumerate(rows):
            for key in run_update_keys(row):
                index.setdefault(key, idx)

        updated_rows: list[dict[str, Any]] = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            job_id = str(job.get("job_id") or "").strip()
            match_keys = run_update_keys(job)
            row_index = next((index[key] for key in match_keys if key in index), None)
            if not job_id and row_index is None:
                continue
            target_job_id = str(rows[row_index].get("job_id") or "") if row_index is not None else job_id
            base = rows[row_index] if row_index is not None else {"job_id": target_job_id}
            merged = self._merge_run_job_row(
                base=base,
                incoming=job,
                root=root,
                job_id=target_job_id,
                session_id=session_id,
                turn_id=turn_id,
                batch_id=str(batch_id or ""),
            )
            if row_index is not None:
                rows[row_index] = merged
            else:
                row_index = len(rows)
                rows.append(merged)
            for key in run_update_keys(merged):
                index.setdefault(key, row_index)
            updated_rows.append(merged)

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
        batch_key = str(batch_id or "").strip()

        def merge_seen(current: dict[str, Any], row: dict[str, Any], now: str) -> dict[str, Any]:
            already_seen_in_batch = bool(batch_key and str(current.get("last_seen_batch_id") or "") == batch_key)
            merged = self._merge_job_row(current, row)
            merged["last_seen_at"] = now
            if not already_seen_in_batch:
                merged["seen_count"] = int(merged.get("seen_count") or 0) + 1
            if batch_key:
                merged["last_seen_batch_id"] = batch_key
            return merged

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
                    if batch_key:
                        current["last_seen_batch_id"] = batch_key
                    current["is_active"] = True
                    current["features_ref"] = str(current.get("features_ref") or "")
                    history_index[job_id] = current
                else:
                    current = merge_seen(current, row, now)
                    current["job_id"] = job_id
                    current["canonical_job_id"] = canonical_job_id
                    current["is_active"] = True
                    history_index[job_id] = current
            else:
                job_id = target
                current = history_index.get(job_id)
            if current is None:
                continue
            if not target.startswith("new:"):
                current = merge_seen(current, row, now)
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
        by_job_id, by_canonical_job_id, by_match_key = self._history_resolution_indexes(site_id, rows)
        changed = False
        for job in jobs:
            if not isinstance(job, dict):
                continue
            match_idx = self._resolve_history_row_index(
                site_id,
                job,
                by_job_id=by_job_id,
                by_canonical_job_id=by_canonical_job_id,
                by_match_key=by_match_key,
            )
            if match_idx is None:
                continue
            current = rows[match_idx]
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
            decision_context_hash = str(job.get("decision_context_hash") or "").strip()
            if decision_context_hash:
                current["decision_context_hash"] = decision_context_hash
            if isinstance(job.get("context_versions"), dict):
                current["context_versions"] = dict(job["context_versions"])
            reason_type = self._normalize_decision_reason_type(job.get("decision_reason_type"))
            if reason_type:
                current["decision_reason_type"] = reason_type
            elif current["decision_status"] == "filtered_out" and not str(current.get("decision_reason_type") or "").strip():
                current["decision_reason_type"] = self._infer_decision_reason_type(current)
            posted_payload = normalize_posted_fields(dict(job))
            for field in (
                "posted_at",
                "posted_label",
                "posted_observed_at",
                "inferred_posted_date",
                "observed_posted_age_days",
                "observed_posted_age_is_lower_bound",
            ):
                if posted_payload.get(field) not in (None, ""):
                    current[field] = posted_payload[field]
            current["decision_updated_at"] = now_iso()
            changed = True
        if changed:
            self._write_history_jobs(site_id, rows)

    def update_job_application_outcomes(self, site_id: str, applications: list[dict[str, Any]]) -> None:
        rows = self._load_history_jobs(site_id)
        by_job_id, by_canonical_job_id, by_match_key = self._history_resolution_indexes(site_id, rows)
        changed = False
        for app in applications:
            if not isinstance(app, dict):
                continue
            match_idx = self._resolve_history_row_index(
                site_id,
                app,
                by_job_id=by_job_id,
                by_canonical_job_id=by_canonical_job_id,
                by_match_key=by_match_key,
            )
            if match_idx is None:
                continue
            current = rows[match_idx]
            status = str(app.get("status") or ("submitted" if app.get("submitted") else "apply_failed"))
            error_text = str(app.get("detail", {}).get("error") if isinstance(app.get("detail"), dict) else "")
            current_status = str(current.get("application_status") or "").strip().lower()
            current_decision_status = str(current.get("decision_status") or "").strip().lower()
            if (
                (current_status in self.DURABLE_APPLICATION_STATUSES or current_decision_status == "filtered_out")
                and self._is_runtime_stop_apply_failed(status=status, error_text=error_text)
            ):
                current["last_runtime_apply_error"] = error_text
                changed = True
                continue
            current["application_status"] = status
            if str(status or "").strip().lower() == "filtered_out":
                current["decision_status"] = "filtered_out"
            decision_context_hash = str(app.get("decision_context_hash") or "").strip()
            if decision_context_hash:
                current["decision_context_hash"] = decision_context_hash
            if isinstance(app.get("context_versions"), dict):
                current["context_versions"] = dict(app["context_versions"])
            reason_type = self._normalize_decision_reason_type(app.get("decision_reason_type"))
            if reason_type:
                current["decision_reason_type"] = reason_type
            elif str(status or "").strip().lower() == "filtered_out" and not str(current.get("decision_reason_type") or "").strip():
                current["decision_reason_type"] = self._infer_decision_reason_type(current)
            posted_payload = normalize_posted_fields(dict(app))
            for field in (
                "posted_at",
                "posted_label",
                "posted_observed_at",
                "inferred_posted_date",
                "observed_posted_age_days",
                "observed_posted_age_is_lower_bound",
            ):
                if posted_payload.get(field) not in (None, ""):
                    current[field] = posted_payload[field]
            apply_state = self._history_apply_state_for_application_status(status)
            if apply_state:
                current["apply_state"] = apply_state
            current["application_updated_at"] = now_iso()
            current["last_apply_error"] = error_text
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

    def append_application_reviews(
        self,
        site_id: str,
        reviews: list[dict[str, Any]],
        session_id: str,
        turn_id: str,
        batch_id: str = "",
    ) -> dict[str, Any]:
        store = JSONLStore(self.site_dir(site_id) / "applications" / "reviews" / f"{today_str()}.jsonl")
        history_rows = self._load_history_jobs(site_id)
        by_job_id, by_canonical_job_id, by_match_key = self._history_resolution_indexes(site_id, history_rows)
        checked_at = now_iso()
        recorded_count = 0
        matched_count = 0
        unmatched_count = 0
        created_history_count = 0
        matched_job_ids: list[str] = []
        changed = False

        for raw in reviews:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip()
            url = self._normalize_url(str(raw.get("url") or ""))
            site_job_id = str(raw.get("site_job_id") or raw.get("source_job_id") or "").strip()
            status = str(raw.get("application_review_status") or "").strip().lower()
            status_raw = re.sub(r"\s+", " ", str(raw.get("application_review_status_raw") or status).strip())
            stage = self._normalize_review_stage(raw.get("application_review_stage"))
            review_application_status = self._application_status_from_review(
                status=status,
                raw=status_raw,
                stage=stage,
            )
            review_is_non_terminal = self._review_indicates_non_terminal_application(
                status=status,
                raw=status_raw,
                stage=stage,
            )
            if review_is_non_terminal:
                status = "resumable"
            if not title and not url and not site_job_id:
                continue

            review_row = {
                "ts": checked_at,
                "batch_id": str(batch_id or ""),
                "session_id": session_id,
                "turn_id": turn_id,
                "checked_at": checked_at,
                "title": title,
                "url": url,
                "site_job_id": site_job_id,
                "application_review_status": status,
                "application_review_status_raw": status_raw,
                "application_review_stage": stage,
            }
            match_idx = self._resolve_history_row_index(
                site_id,
                review_row,
                by_job_id=by_job_id,
                by_canonical_job_id=by_canonical_job_id,
                by_match_key=by_match_key,
            )
            matched_job_id = ""
            if match_idx is None:
                created = self._build_application_review_history_row(
                    site_id=site_id,
                    review_row=review_row,
                    session_id=session_id,
                    turn_id=turn_id,
                    batch_id=str(batch_id or ""),
                    checked_at=checked_at,
                )
                if created is not None:
                    history_rows.append(created)
                    match_idx = len(history_rows) - 1
                    by_job_id, by_canonical_job_id, by_match_key = self._history_resolution_indexes(
                        site_id, history_rows
                    )
                    created_history_count += 1
                    changed = True
            if match_idx is not None:
                current = history_rows[match_idx]
                matched_job_id = str(current.get("job_id") or "").strip()
                previous_status = str(current.get("application_review_status") or "").strip().lower()
                previous_raw = re.sub(r"\s+", " ", str(current.get("application_review_status_raw") or "").strip())
                previous_stage = self._normalize_review_stage(current.get("application_review_stage"))
                status_changed = bool(
                    (previous_status and status and previous_status != status)
                    or (
                        previous_raw
                        and status_raw
                        and self._normalize_review_compare(previous_raw) != self._normalize_review_compare(status_raw)
                    )
                    or (previous_stage and stage and previous_stage != stage)
                )
                review_row["previous_application_review_status"] = previous_status
                review_row["previous_application_review_status_raw"] = previous_raw
                review_row["previous_application_review_stage"] = previous_stage
                review_row["application_review_status_changed"] = status_changed
                if site_job_id:
                    current["site_job_id"] = site_job_id
                current["application_review_status"] = status
                current["application_review_status_raw"] = status_raw
                current["application_review_stage"] = stage
                current["previous_application_review_status"] = previous_status
                current["previous_application_review_status_raw"] = previous_raw
                current["previous_application_review_stage"] = previous_stage
                current["application_review_status_changed"] = status_changed
                current["application_review_checked_at"] = checked_at
                current["application_review_batch_id"] = str(batch_id or "")
                if review_application_status:
                    current["application_status"] = review_application_status
                    apply_state = self._history_apply_state_for_application_status(review_application_status)
                    if apply_state:
                        current["apply_state"] = apply_state
                    if review_application_status == "resumable":
                        current["decision_status"] = "recommended_apply"
                        current.pop("decision_reason_type", None)
                        current.pop("decision_context_hash", None)
                elif review_is_non_terminal:
                    if str(current.get("application_status") or "").strip().lower() in {"already_applied", "submitted"}:
                        current["application_status"] = ""
                    if str(current.get("decision_status") or "").strip().lower() == "already_applied":
                        current["decision_status"] = ""
                    if str(current.get("apply_state") or "").strip().lower() in {
                        "terminal_already_applied",
                        "terminal_submitted",
                    }:
                        current["apply_state"] = ""
                if url:
                    current["application_review_url"] = url
                matched_count += 1
                if matched_job_id:
                    matched_job_ids.append(matched_job_id)
                changed = True
            else:
                unmatched_count += 1

            store.append({**review_row, "matched_job_id": matched_job_id})
            recorded_count += 1

        if changed:
            self._write_history_jobs(site_id, history_rows)
        return {
            "recorded_count": recorded_count,
            "matched_count": matched_count,
            "unmatched_count": unmatched_count,
            "created_history_count": created_history_count,
            "matched_job_ids": matched_job_ids,
        }

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
