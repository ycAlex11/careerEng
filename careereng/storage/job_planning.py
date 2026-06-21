"""Search snapshot and apply-plan storage for job workflows."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from careereng.skill_schema import normalize_posted_window_policy
from careereng.storage.job_identity import infer_site_job_id_from_url, primary_job_identity_key
from careereng.storage.posted_time import current_posted_age_observation, normalize_posted_fields
from careereng.utils import ensure_dir, make_id, now_iso, safe_file_stem, write_json


TERMINAL_APPLICATION_STATUSES = {
    "already_applied",
    "submitted",
    "rejected",
    "closed",
    "withdrawn",
}
RETRYABLE_APPLICATION_STATUSES = {
    "apply_failed",
    "blocked",
}
RESUMABLE_APPLICATION_STATUSES = {
    "resumable",
}
RESUMABLE_APPLY_STATES = {
    "resumable_application",
    "draft_application",
    "incomplete_application",
}
TERMINAL_DECISION_STATUSES = {
    "already_applied",
    "filtered_out",
}


class JobPlanningStore:
    """Persist lightweight retrieval snapshots and per-batch apply plans."""

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.jobs_dir = ensure_dir(self.workspace / "jobs")
        self.snapshots_dir = ensure_dir(self.jobs_dir / "search_snapshots")
        self.apply_plans_dir = ensure_dir(self.jobs_dir / "apply_plans")

    def search_fingerprint(self, *, site_key: str, url: str = "", filters_summary: dict[str, Any] | None = None) -> str:
        site = safe_file_stem(site_key or "site")
        normalized_url = self._normalize_search_url(url)
        filters_payload = filters_summary if isinstance(filters_summary, dict) else {}
        seed = json.dumps(
            {
                "site_key": site,
                "url": normalized_url,
                "filters_summary": filters_payload,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
        return f"{site}_{digest}"

    def write_snapshot(
        self,
        *,
        site_key: str,
        batch_id: str,
        jobs: list[dict[str, Any]],
        current_url: str = "",
        retrieval_complete: bool,
        result_count: int | None = None,
        filters_summary: dict[str, Any] | None = None,
        stop_reason: str = "",
    ) -> dict[str, Any]:
        fingerprint = self.search_fingerprint(site_key=site_key, url=current_url, filters_summary=filters_summary)
        ordered_items = [self._snapshot_job_item(site_key=site_key, row=row) for row in jobs if isinstance(row, dict)]
        ordered_items = [item for item in ordered_items if item.get("job_key")]
        snapshot_id = make_id("search_snapshot")
        now = now_iso()
        payload = {
            "snapshot_id": snapshot_id,
            "site_key": str(site_key or ""),
            "search_fingerprint": fingerprint,
            "batch_id": str(batch_id or ""),
            "retrieved_at": now,
            "retrieval_complete": bool(retrieval_complete),
            "result_count": int(result_count if result_count is not None else len(ordered_items)),
            "first_page_job_keys": [str(item.get("job_key") or "") for item in ordered_items[:20]],
            "ordered_job_keys": [str(item.get("job_key") or "") for item in ordered_items],
            "jobs": ordered_items,
            "filters_summary": filters_summary if isinstance(filters_summary, dict) else {},
            "source_run_ref": f"sites/{safe_file_stem(site_key)}/jobs/runs/{safe_file_stem(batch_id)}.jsonl",
            "stop_reason": str(stop_reason or ""),
            "current_url": str(current_url or ""),
        }
        path = self._snapshot_path(site_key=site_key, fingerprint=fingerprint, snapshot_id=snapshot_id)
        write_json(path, payload)
        self._update_snapshot_index(payload, path)
        return {**payload, "path": str(path)}

    def latest_complete_snapshot(self, *, site_key: str, search_fingerprint: str) -> dict[str, Any] | None:
        index = self._read_snapshot_index()
        candidates = [
            row
            for row in index.get("snapshots", [])
            if isinstance(row, dict)
            and str(row.get("site_key") or "") == str(site_key or "")
            and str(row.get("search_fingerprint") or "") == str(search_fingerprint or "")
            and bool(row.get("retrieval_complete"))
        ]
        candidates.sort(key=lambda row: str(row.get("retrieved_at") or ""), reverse=True)
        if not candidates:
            return None
        path = self.workspace / str(candidates[0].get("path") or "")
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def write_apply_plan(
        self,
        *,
        site_key: str,
        batch_id: str,
        jobs: list[dict[str, Any]],
        history_matches: list[dict[str, Any] | None],
        snapshot_id: str = "",
        apply_requested: bool = True,
        decision_context_hash: str = "",
        context_versions: dict[str, str] | None = None,
        apply_candidate_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        current_context_versions = dict(context_versions or {})
        for idx, row in enumerate(jobs):
            if not isinstance(row, dict):
                continue
            history = history_matches[idx] if idx < len(history_matches) else None
            item = self._plan_item(
                site_key=site_key,
                row=row,
                history=history if isinstance(history, dict) else None,
                decision_context_hash=decision_context_hash,
                context_versions=current_context_versions,
                apply_candidate_policy=apply_candidate_policy,
            )
            items.append(item)
            action = str(item.get("action") or "unknown")
            counts[action] = int(counts.get(action) or 0) + 1
        items = self._dedupe_plan_items(site_key=site_key, items=items)
        counts = {}
        for item in items:
            action = str(item.get("action") or "unknown")
            counts[action] = int(counts.get(action) or 0) + 1
        plan_id = make_id("apply_plan")
        payload = {
            "plan_id": plan_id,
            "batch_id": str(batch_id or ""),
            "site_key": str(site_key or ""),
            "snapshot_id": str(snapshot_id or ""),
            "generated_at": now_iso(),
            "apply_requested": bool(apply_requested),
            "decision_context_hash": str(decision_context_hash or ""),
            "context_versions": current_context_versions,
            "apply_candidate_policy": normalize_posted_window_policy(apply_candidate_policy),
            "plan_items": items,
            "counts": counts,
        }
        path = self._apply_plan_path(batch_id=batch_id, site_key=site_key)
        write_json(path, payload)
        return {**payload, "path": str(path)}

    def load_apply_plan(self, *, batch_id: str, site_key: str) -> dict[str, Any]:
        path = self._apply_plan_path(batch_id=batch_id, site_key=site_key)
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return {**payload, "path": str(path)} if isinstance(payload, dict) else {}

    @classmethod
    def terminal_update_for_plan_item(cls, item: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(item, dict):
            return {}
        action = str(item.get("action") or "")
        job_id = str(item.get("job_id") or "")
        if not job_id:
            return {}
        if action == "skip_already_applied":
            return {
                "job_id": job_id,
                "application_status": "already_applied",
                "apply_state": "terminal_already_applied",
                "decision_status": "already_applied",
            }
        if action == "skip_submitted":
            return {
                "job_id": job_id,
                "application_status": "already_applied",
                "apply_state": "terminal_already_applied",
                "decision_status": "already_applied",
            }
        if action == "skip_filtered_out":
            return {
                "job_id": job_id,
                "application_status": "filtered_out",
                "decision_status": "filtered_out",
                "apply_state": "terminal_filtered_out",
                "decision_reason_type": str(item.get("decision_reason_type") or ""),
                "decision_context_hash": str(item.get("decision_context_hash") or ""),
                "context_versions": item.get("context_versions") if isinstance(item.get("context_versions"), dict) else {},
                "observed_posted_age_days": item.get("observed_posted_age_days"),
                "current_posted_age_days": item.get("current_posted_age_days"),
                "observed_posted_age_is_lower_bound": bool(item.get("observed_posted_age_is_lower_bound")),
                "posted_observed_at": str(item.get("posted_observed_at") or ""),
                "inferred_posted_date": str(item.get("inferred_posted_date") or ""),
            }
        if action in {"skip_rejected", "skip_closed", "skip_withdrawn"}:
            status = action.replace("skip_", "")
            return {
                "job_id": job_id,
                "application_status": status,
                "apply_state": f"terminal_{status}",
            }
        return {}

    @classmethod
    def _dedupe_plan_items(cls, *, site_key: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_key: dict[str, dict[str, Any]] = {}
        ordered_keys: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            key = primary_job_identity_key(site_key, item) or str(item.get("job_key") or "")
            if not key:
                continue
            current = by_key.get(key)
            if current is None:
                by_key[key] = dict(item)
                ordered_keys.append(key)
                continue
            by_key[key] = cls._merge_plan_items(current, item)
        return [by_key[key] for key in ordered_keys if key in by_key]

    @classmethod
    def _merge_plan_items(cls, current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        winner, other = (incoming, current) if cls._plan_item_priority(incoming) > cls._plan_item_priority(current) else (current, incoming)
        merged = dict(winner)
        for field, value in other.items():
            if value is None or value == "":
                continue
            if not merged.get(field):
                merged[field] = value
        if not str(merged.get("site_job_id") or "").strip():
            inferred = infer_site_job_id_from_url(merged.get("url") or other.get("url") or "")
            if inferred:
                merged["site_job_id"] = inferred
        return merged

    @staticmethod
    def _plan_item_priority(item: dict[str, Any]) -> int:
        action = str(item.get("action") or "").strip().lower()
        application_status = str(
            item.get("history_application_status") or item.get("application_status") or ""
        ).strip().lower()
        if action in {"skip_submitted", "skip_already_applied", "skip_rejected", "skip_closed", "skip_withdrawn"}:
            return 100
        if application_status in TERMINAL_APPLICATION_STATUSES:
            return 100
        if action in {"resume_application"}:
            return 90
        if action in {"retry_blocked"}:
            return 80
        if action in {"open_for_match_review", "enrich_jd"}:
            return 70
        if action == "skip_filtered_out":
            return 60
        return 50

    @staticmethod
    def _normalize_search_url(url: str) -> str:
        raw = str(url or "").strip()
        if not raw:
            return ""
        try:
            parsed = urlparse(raw)
        except Exception:
            return raw
        if not parsed.scheme or not parsed.netloc:
            return raw
        ignored_keys = {"page", "p", "start", "offset"}
        kept_qs = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=False)
            if key.lower() not in ignored_keys and not key.lower().startswith("utm_")
        ]
        normalized = parsed._replace(query=urlencode(sorted(kept_qs)), fragment="")
        return urlunparse(normalized)

    @classmethod
    def _job_key(cls, *, site_key: str, row: dict[str, Any]) -> str:
        site = safe_file_stem(site_key or str(row.get("site_id") or "site"))
        site_job_id = str(row.get("site_job_id") or row.get("source_job_id") or "").strip()
        if not site_job_id:
            site_job_id = infer_site_job_id_from_url(row.get("url"))
        if site_job_id:
            return f"site_job_id|{site}|{site_job_id.lower()}"
        for field in ("site_job_id", "canonical_job_id", "job_id", "url"):
            value = str(row.get(field) or "").strip()
            if value:
                return f"{field}|{site}|{value}"
        title = cls._norm(str(row.get("title") or ""))
        location = cls._norm(str(row.get("location") or ""))
        posted = cls._norm(str(row.get("posted_label") or row.get("posted_at") or ""))
        return f"fallback|{site}|{title}|{location}|{posted}" if title else ""

    @classmethod
    def _snapshot_job_item(cls, *, site_key: str, row: dict[str, Any]) -> dict[str, Any]:
        row = normalize_posted_fields(dict(row))
        return {
            "job_key": cls._job_key(site_key=site_key, row=row),
            "job_id": str(row.get("job_id") or ""),
            "canonical_job_id": str(row.get("canonical_job_id") or ""),
            "site_job_id": str(row.get("site_job_id") or ""),
            "title": str(row.get("title") or ""),
            "url": str(row.get("url") or ""),
            "location": str(row.get("location") or ""),
            "posted_at": str(row.get("posted_at") or ""),
            "posted_label": str(row.get("posted_label") or ""),
            "posted_observed_at": str(row.get("posted_observed_at") or ""),
            "inferred_posted_date": str(row.get("inferred_posted_date") or ""),
            "observed_posted_age_days": row.get("observed_posted_age_days", ""),
            "observed_posted_age_is_lower_bound": bool(row.get("observed_posted_age_is_lower_bound")),
        }

    @classmethod
    def _plan_item(
        cls,
        *,
        site_key: str,
        row: dict[str, Any],
        history: dict[str, Any] | None,
        decision_context_hash: str = "",
        context_versions: dict[str, str] | None = None,
        apply_candidate_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        history = history if isinstance(history, dict) else {}
        row = normalize_posted_fields(dict(row))
        application_status = str(history.get("application_status") or row.get("application_status") or "").strip().lower()
        decision_status = str(history.get("decision_status") or row.get("decision_status") or "").strip().lower()
        apply_state = str(history.get("apply_state") or row.get("apply_state") or "").strip().lower()
        current_context_hash = str(decision_context_hash or "").strip()
        history_context_hash = str(history.get("decision_context_hash") or "").strip()
        current_context_versions = dict(context_versions or {})
        history_context_versions = history.get("context_versions") if isinstance(history.get("context_versions"), dict) else {}
        decision_reason_type = str(
            history.get("decision_reason_type") or row.get("decision_reason_type") or "unknown"
        ).strip().lower()
        posted_age = cls._posted_age_observation(row)
        posted_policy = normalize_posted_window_policy(apply_candidate_policy)
        posted_exclusion = cls._posted_window_exclusion(posted_age=posted_age, policy=posted_policy)
        action = "open_for_match_review"
        operation_state = "needs_review"
        reason = "no terminal local state"
        if application_status == "already_applied":
            action, operation_state, reason = "skip_already_applied", "terminal_already_applied", "history already_applied"
        elif application_status == "submitted":
            action, operation_state, reason = "skip_submitted", "terminal_submitted", "history submitted"
        elif application_status in {"rejected", "closed", "withdrawn"}:
            action, operation_state, reason = f"skip_{application_status}", f"terminal_{application_status}", f"history {application_status}"
        elif decision_status == "already_applied":
            action, operation_state, reason = "skip_already_applied", "terminal_already_applied", "history decision already_applied"
        elif application_status in RESUMABLE_APPLICATION_STATUSES or apply_state in RESUMABLE_APPLY_STATES:
            action, operation_state, reason = "resume_application", "resume_required", "site shows an unsubmitted resumable application"
        elif posted_exclusion:
            action, operation_state, reason = "skip_filtered_out", "terminal_filtered_out", str(posted_exclusion.get("reason") or "")
            decision_reason_type = "time"
        elif decision_status == "filtered_out" or application_status == "filtered_out":
            context_changed = cls._filtered_out_context_changed(
                decision_reason_type=decision_reason_type,
                current_context_hash=current_context_hash,
                history_context_hash=history_context_hash,
                current_context_versions=current_context_versions,
                history_context_versions=history_context_versions,
            )
            if context_changed:
                action, operation_state, reason = "open_for_match_review", "needs_review", "history filtered_out but decision context changed"
            else:
                action, operation_state, reason = "skip_filtered_out", "terminal_filtered_out", "history filtered_out"
        elif application_status in RETRYABLE_APPLICATION_STATUSES or apply_state.startswith("blocked_"):
            action, operation_state, reason = "retry_blocked", "retry_allowed", "previous apply did not reach durable terminal state"
        elif not str(row.get("url") or "").strip():
            action, operation_state, reason = "enrich_jd", "needs_enrichment", "missing job url"
        return {
            **cls._snapshot_job_item(site_key=site_key, row=row),
            "operation_state": operation_state,
            "action": action,
            "reason": reason,
            "history_job_id": str(history.get("job_id") or ""),
            "history_application_status": application_status,
            "history_decision_status": decision_status,
            "history_apply_state": apply_state,
            "history_decision_context_hash": history_context_hash,
            "decision_context_hash": current_context_hash,
            "decision_reason_type": decision_reason_type,
            "context_versions": current_context_versions,
            "history_context_versions": history_context_versions,
            "observed_posted_age_days": row.get("observed_posted_age_days", ""),
            "current_posted_age_days": posted_age.get("days") if posted_age.get("days") is not None else "",
            "observed_posted_age_is_lower_bound": bool(posted_age.get("is_lower_bound")),
            "posted_observed_at": str(row.get("posted_observed_at") or ""),
            "inferred_posted_date": str(row.get("inferred_posted_date") or ""),
            "apply_candidate_policy": posted_policy,
        }

    @classmethod
    def _posted_age_observation(cls, row: dict[str, Any]) -> dict[str, Any]:
        return current_posted_age_observation(normalize_posted_fields(dict(row)))

    @classmethod
    def _posted_window_exclusion(cls, *, posted_age: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
        window_days = int(policy.get("posted_window_days") or 0)
        if window_days <= 0:
            return {}
        days = posted_age.get("days")
        if days is None:
            if str(policy.get("unknown_posted_age") or "review") == "filtered_out":
                return {
                    "reason": f"posted age is unknown and policy requires a confirmed < {window_days} day window",
                    "window_days": window_days,
                }
            return {}
        try:
            age_days = int(days)
        except Exception:
            return {}
        comparison = str(policy.get("posted_window_comparison") or "strictly_less_than")
        outside = age_days > window_days if comparison == "less_than_or_equal" else age_days >= window_days
        if not outside:
            return {}
        operator = "<=" if comparison == "less_than_or_equal" else "<"
        return {
            "reason": f"posted age {age_days} days is outside apply candidate policy ({operator} {window_days} days)",
            "window_days": window_days,
            "age_days": age_days,
            "is_lower_bound": bool(posted_age.get("is_lower_bound")),
        }

    @classmethod
    def _filtered_out_context_changed(
        cls,
        *,
        decision_reason_type: str,
        current_context_hash: str,
        history_context_hash: str,
        current_context_versions: dict[str, Any],
        history_context_versions: dict[str, Any],
    ) -> bool:
        if not history_context_versions:
            return bool(history_context_hash and current_context_hash and history_context_hash != current_context_hash)
        if "legacy_context" in history_context_versions or "legacy_decision_context_hash" in history_context_versions:
            return True
        reason_type = str(decision_reason_type or "unknown").strip().lower()
        keys_by_reason = {
            "time": ("site_policy_hash", "apply_candidate_policy_hash"),
            "hard_excluded": ("site_policy_hash", "project_matching_policy_hash", "site_matching_policy_hash"),
            "cv": ("cv_hash", "profile_hash", "project_matching_policy_hash", "site_matching_policy_hash"),
            "matching_policy": ("cv_hash", "profile_hash", "project_matching_policy_hash", "site_matching_policy_hash"),
        }
        keys = keys_by_reason.get(reason_type)
        if not keys:
            return bool(history_context_hash and current_context_hash and history_context_hash != current_context_hash)
        return any(
            str(current_context_versions.get(key) or "") != str(history_context_versions.get(key) or "")
            for key in keys
        )

    @staticmethod
    def _norm(value: str) -> str:
        return " ".join(str(value or "").strip().lower().split())

    def _snapshot_path(self, *, site_key: str, fingerprint: str, snapshot_id: str) -> Path:
        return ensure_dir(self.snapshots_dir / safe_file_stem(site_key) / safe_file_stem(fingerprint)) / f"{safe_file_stem(snapshot_id)}.json"

    def _apply_plan_path(self, *, batch_id: str, site_key: str) -> Path:
        return ensure_dir(self.apply_plans_dir / safe_file_stem(batch_id or "adhoc")) / f"{safe_file_stem(site_key or 'site')}.json"

    def _snapshot_index_path(self) -> Path:
        return self.snapshots_dir / "index.json"

    def _read_snapshot_index(self) -> dict[str, Any]:
        path = self._snapshot_index_path()
        if not path.exists():
            return {"version": 1, "snapshots": []}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": 1, "snapshots": []}
        if not isinstance(payload, dict):
            return {"version": 1, "snapshots": []}
        snapshots = payload.get("snapshots")
        if not isinstance(snapshots, list):
            payload["snapshots"] = []
        return payload

    def _update_snapshot_index(self, snapshot: dict[str, Any], path: Path) -> None:
        index = self._read_snapshot_index()
        rel_path = str(path.relative_to(self.workspace))
        row = {
            "snapshot_id": str(snapshot.get("snapshot_id") or ""),
            "site_key": str(snapshot.get("site_key") or ""),
            "search_fingerprint": str(snapshot.get("search_fingerprint") or ""),
            "batch_id": str(snapshot.get("batch_id") or ""),
            "retrieved_at": str(snapshot.get("retrieved_at") or ""),
            "retrieval_complete": bool(snapshot.get("retrieval_complete")),
            "result_count": int(snapshot.get("result_count") or 0),
            "path": rel_path,
        }
        snapshots = [item for item in index.get("snapshots", []) if isinstance(item, dict)]
        snapshots = [item for item in snapshots if str(item.get("snapshot_id") or "") != row["snapshot_id"]]
        snapshots.append(row)
        snapshots.sort(key=lambda item: str(item.get("retrieved_at") or ""), reverse=True)
        index["version"] = 1
        index["snapshots"] = snapshots[:200]
        index["updated_at"] = now_iso()
        write_json(self._snapshot_index_path(), index)
