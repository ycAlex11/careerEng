"""Search snapshot and apply-plan storage for job workflows."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

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
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for idx, row in enumerate(jobs):
            if not isinstance(row, dict):
                continue
            history = history_matches[idx] if idx < len(history_matches) else None
            item = self._plan_item(
                site_key=site_key,
                row=row,
                history=history if isinstance(history, dict) else None,
                decision_context_hash=decision_context_hash,
            )
            items.append(item)
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
                "decision_context_hash": str(item.get("decision_context_hash") or ""),
            }
        if action in {"skip_rejected", "skip_closed", "skip_withdrawn"}:
            status = action.replace("skip_", "")
            return {
                "job_id": job_id,
                "application_status": status,
                "apply_state": f"terminal_{status}",
            }
        return {}

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
        }

    @classmethod
    def _plan_item(
        cls,
        *,
        site_key: str,
        row: dict[str, Any],
        history: dict[str, Any] | None,
        decision_context_hash: str = "",
    ) -> dict[str, Any]:
        history = history if isinstance(history, dict) else {}
        application_status = str(history.get("application_status") or row.get("application_status") or "").strip().lower()
        decision_status = str(history.get("decision_status") or row.get("decision_status") or "").strip().lower()
        apply_state = str(history.get("apply_state") or row.get("apply_state") or "").strip().lower()
        current_context_hash = str(decision_context_hash or "").strip()
        history_context_hash = str(history.get("decision_context_hash") or "").strip()
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
        elif decision_status == "filtered_out" or application_status == "filtered_out":
            if history_context_hash and current_context_hash and history_context_hash != current_context_hash:
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
        }

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
