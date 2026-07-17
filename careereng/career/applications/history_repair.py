"""Dry-run and safe repair helpers for application history data."""

from __future__ import annotations

from collections import Counter, defaultdict
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from careereng.career.applications.summary import (
    DEFAULT_SUMMARY_SINCE,
    _collapse_text,
    _load_review_rows,
    _parse_date,
    _registered_or_existing_site_keys,
    _row_in_scope,
)
from careereng.career.applications.site_store import SiteStore
from careereng.platform.persistence import JSONLStore
from careereng.utils import ensure_dir, now_iso, safe_file_stem, write_json


HISTORY_REPAIR_PLAN_RELATIVE_PATH = Path("application_summary") / "history_repair_plan.json"


def _history_ref(row: dict[str, Any], site_key: str) -> dict[str, Any]:
    return {
        "site_key": site_key,
        "job_id": _collapse_text(row.get("job_id")),
        "title": _collapse_text(row.get("title")),
        "site_job_id": _collapse_text(row.get("site_job_id")),
        "url": _collapse_text(row.get("url")),
        "application_status": _collapse_text(row.get("application_status")),
        "application_review_status": _collapse_text(row.get("application_review_status")),
        "application_review_status_raw": _collapse_text(row.get("application_review_status_raw")),
        "application_review_stage": _collapse_text(row.get("application_review_stage")),
    }


def _review_ref(row: dict[str, Any], site_key: str) -> dict[str, Any]:
    return {
        "site_key": site_key,
        "title": _collapse_text(row.get("title")),
        "site_job_id": _collapse_text(row.get("site_job_id") or row.get("source_job_id")),
        "url": _collapse_text(row.get("url")),
        "matched_job_id": _collapse_text(row.get("matched_job_id")),
        "application_review_status": _collapse_text(row.get("application_review_status")),
        "application_review_status_raw": _collapse_text(row.get("application_review_status_raw")),
        "application_review_stage": _collapse_text(row.get("application_review_stage")),
        "checked_at": _collapse_text(row.get("checked_at") or row.get("ts")),
        "batch_id": _collapse_text(row.get("batch_id")),
    }


def _review_candidate(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": _collapse_text(row.get("title")),
        "url": _collapse_text(row.get("url")),
        "site_job_id": _collapse_text(row.get("site_job_id") or row.get("source_job_id")),
    }


def _is_dashboard_like_url(url: str) -> bool:
    text = _collapse_text(url)
    if not text:
        return False
    try:
        parsed = urlparse(text)
    except Exception:
        parsed = None
    target = f"{parsed.netloc if parsed else ''} {parsed.path if parsed else text} {parsed.query if parsed else ''}".lower()
    markers = (
        "dashboard",
        "candidate-home",
        "candidate_home",
        "candidatehome",
        "my-applications",
        "my_applications",
        "application-center",
        "action-center",
        "profile",
        "bga=true",
    )
    return any(marker in target for marker in markers)


def _checked_at(row: dict[str, Any]) -> str:
    return _collapse_text(row.get("checked_at") or row.get("ts") or row.get("application_review_checked_at"))


def _latest_review(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    return sorted(rows, key=_checked_at)[-1]


def _single_candidate(values: list[str]) -> str:
    unique = sorted({_collapse_text(value) for value in values if _collapse_text(value)})
    if len(unique) <= 1:
        return unique[0] if unique else ""
    suffixes = {_site_job_id_suffix(value) for value in unique}
    if len(suffixes) == 1:
        return sorted(unique, key=lambda value: (len(value), value), reverse=True)[0]
    return ""


def _site_job_id_suffix(value: str) -> str:
    text = _collapse_text(value).lower()
    match = re.fullmatch(r"(?:19|20)\d{2}[-_](\d+)", text)
    return match.group(1) if match else text


def _site_job_id_from_url(site_store: SiteStore, url: str) -> str:
    infer = getattr(site_store, "_infer_site_job_id_from_url", None)
    if callable(infer):
        try:
            return _collapse_text(infer(url))
        except Exception:
            return ""
    return ""


def _review_matches_by_job_id(
    *,
    site_store: SiteStore,
    site_key: str,
    history_rows: list[dict[str, Any]],
    review_rows: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], list[tuple[dict[str, Any], dict[str, Any]]]]:
    history_by_job_id = {
        _collapse_text(row.get("job_id")): row
        for row in history_rows
        if isinstance(row, dict) and _collapse_text(row.get("job_id"))
    }
    candidates = [_review_candidate(row) for row in review_rows]
    try:
        current_matches = site_store.match_history_rows(site_key, candidates) if candidates else []
    except Exception:
        current_matches = [None] * len(candidates)
    by_job_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rematchable_unmatched: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for review_row, current_match in zip(review_rows, current_matches):
        if not isinstance(review_row, dict):
            continue
        matched_job_id = _collapse_text(review_row.get("matched_job_id"))
        matched_history = history_by_job_id.get(matched_job_id) if matched_job_id else None
        current_history = current_match if isinstance(current_match, dict) else None
        history_row = matched_history or current_history
        if not isinstance(history_row, dict):
            continue
        job_id = _collapse_text(history_row.get("job_id"))
        if not job_id:
            continue
        by_job_id[job_id].append(review_row)
        if not matched_job_id and current_history is not None:
            rematchable_unmatched.append((review_row, current_history))
    return by_job_id, rematchable_unmatched


def _review_paths(workspace: Path, site_key: str) -> list[Path]:
    review_dir = workspace / "sites" / safe_file_stem(site_key) / "applications" / "reviews"
    if not review_dir.exists():
        return []
    return sorted(review_dir.glob("*.jsonl"))


def _repair_review_log_matches(
    *,
    workspace: Path,
    site_store: SiteStore,
    site_key: str,
    since_date: Any,
    apply: bool,
) -> tuple[list[dict[str, Any]], int]:
    repairs: list[dict[str, Any]] = []
    applied_count = 0
    for path in _review_paths(workspace, site_key):
        store = JSONLStore(path)
        rows = store.read_all()
        candidates: list[dict[str, Any]] = []
        indexes: list[int] = []
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            if _collapse_text(row.get("matched_job_id")):
                continue
            if not _row_in_scope(row, since_date):
                continue
            candidates.append(_review_candidate(row))
            indexes.append(idx)
        if not candidates:
            continue
        try:
            matches = site_store.match_history_rows(site_key, candidates)
        except Exception:
            matches = [None] * len(candidates)
        changed = False
        for idx, row_index in enumerate(indexes):
            match = matches[idx] if idx < len(matches) else None
            if not isinstance(match, dict):
                continue
            matched_job_id = _collapse_text(match.get("job_id"))
            if not matched_job_id:
                continue
            row = rows[row_index]
            repairs.append(
                {
                    "repairable": True,
                    "site_key": site_key,
                    "path": str(path),
                    "line_index": row_index,
                    "new_matched_job_id": matched_job_id,
                    "review": _review_ref(row, site_key),
                    "history": _history_ref(match, site_key),
                }
            )
            if apply and not _collapse_text(row.get("matched_job_id")):
                row["matched_job_id"] = matched_job_id
                row["review_log_repaired_at"] = now_iso()
                applied_count += 1
                changed = True
        if apply and changed:
            store.write_all(rows)
    return repairs, applied_count


def inspect_history_repairs(
    *,
    workspace: Path | str,
    project_root: Path | str | None = None,
    since: str | None = DEFAULT_SUMMARY_SINCE,
    apply: bool = False,
) -> dict[str, Any]:
    workspace_path = Path(workspace)
    root = Path(project_root) if project_root is not None else workspace_path.parent
    site_store = SiteStore(workspace_path, project_root=root)
    since_value = _collapse_text(since)
    since_date = _parse_date(since_value) if since_value else None
    generated_at = now_iso()

    categories: dict[str, list[dict[str, Any]]] = {
        "rematchable_unmatched_reviews": [],
        "missing_site_job_id": [],
        "missing_review_details": [],
        "dashboard_url_anomalies": [],
        "duplicate_strong_keys": [],
        "status_conflicts": [],
    }
    applied: Counter[str] = Counter()
    scanned_history_rows = 0
    scanned_review_rows = 0

    for site_key in _registered_or_existing_site_keys(site_store, workspace_path):
        history_rows = site_store.list_jobs(site_key)
        review_rows = _load_review_rows(workspace_path, site_key)
        scoped_history_rows = [row for row in history_rows if isinstance(row, dict) and _row_in_scope(row, since_date)]
        scoped_review_rows = [row for row in review_rows if isinstance(row, dict) and _row_in_scope(row, since_date)]
        scanned_history_rows += len(scoped_history_rows)
        scanned_review_rows += len(scoped_review_rows)

        review_log_repairs, review_log_applied = _repair_review_log_matches(
            workspace=workspace_path,
            site_store=site_store,
            site_key=site_key,
            since_date=since_date,
            apply=apply,
        )
        categories["rematchable_unmatched_reviews"].extend(review_log_repairs)
        if review_log_applied:
            applied["rematchable_unmatched_reviews"] += review_log_applied
            review_rows = _load_review_rows(workspace_path, site_key)
            scoped_review_rows = [row for row in review_rows if isinstance(row, dict) and _row_in_scope(row, since_date)]

        reviews_by_job_id, rematchable = _review_matches_by_job_id(
            site_store=site_store,
            site_key=site_key,
            history_rows=history_rows,
            review_rows=scoped_review_rows,
        )

        changed = False
        rows_by_job_id = {
            _collapse_text(row.get("job_id")): row
            for row in history_rows
            if isinstance(row, dict) and _collapse_text(row.get("job_id"))
        }
        for row in scoped_history_rows:
            job_id = _collapse_text(row.get("job_id"))
            if not job_id:
                continue
            matching_reviews = reviews_by_job_id.get(job_id, [])

            if not _collapse_text(row.get("site_job_id")):
                candidates = [_site_job_id_from_url(site_store, _collapse_text(row.get("url")))]
                candidates.extend(_collapse_text(review.get("site_job_id") or review.get("source_job_id")) for review in matching_reviews)
                site_job_id = _single_candidate(candidates)
                if site_job_id:
                    categories["missing_site_job_id"].append(
                        {
                            "repairable": True,
                            "site_key": site_key,
                            "job_id": job_id,
                            "new_site_job_id": site_job_id,
                            "history": _history_ref(row, site_key),
                        }
                    )
                    if apply:
                        target = rows_by_job_id.get(job_id)
                        if target is not None and not _collapse_text(target.get("site_job_id")):
                            target["site_job_id"] = site_job_id
                            target["history_repaired_at"] = generated_at
                            applied["missing_site_job_id"] += 1
                            changed = True

            review_status = _collapse_text(row.get("application_review_status"))
            missing_raw = bool(review_status and not _collapse_text(row.get("application_review_status_raw")))
            missing_stage = bool(review_status and not _collapse_text(row.get("application_review_stage")))
            if missing_raw or missing_stage:
                latest = _latest_review(matching_reviews)
                raw_value = _collapse_text(latest.get("application_review_status_raw")) if latest else ""
                stage_value = _collapse_text(latest.get("application_review_stage")) if latest else ""
                if missing_stage and not stage_value and review_status.lower() == "rejected":
                    stage_value = "rejected"
                patch: dict[str, str] = {}
                if missing_raw and raw_value:
                    patch["application_review_status_raw"] = raw_value
                if missing_stage and stage_value:
                    patch["application_review_stage"] = stage_value
                if patch:
                    categories["missing_review_details"].append(
                        {
                            "repairable": True,
                            "site_key": site_key,
                            "job_id": job_id,
                            "patch": patch,
                            "history": _history_ref(row, site_key),
                        }
                    )
                    if apply:
                        target = rows_by_job_id.get(job_id)
                        if target is not None:
                            updated = False
                            for key, value in patch.items():
                                if not _collapse_text(target.get(key)):
                                    target[key] = value
                                    updated = True
                            if updated:
                                target["history_repaired_at"] = generated_at
                                applied["missing_review_details"] += 1
                                changed = True

            url = _collapse_text(row.get("url"))
            if url and _is_dashboard_like_url(url):
                already_marked = _collapse_text(row.get("url_quality")) == "dashboard_or_non_job"
                categories["dashboard_url_anomalies"].append(
                    {
                        "repairable": not already_marked,
                        "site_key": site_key,
                        "job_id": job_id,
                        "url": url,
                        "history": _history_ref(row, site_key),
                    }
                )
                if apply and not already_marked:
                    target = rows_by_job_id.get(job_id)
                    if target is not None:
                        target["url_quality"] = "dashboard_or_non_job"
                        target["url_quality_reason"] = "URL appears to be an application dashboard/profile URL, not a canonical job posting URL."
                        target["url_quality_checked_at"] = generated_at
                        applied["dashboard_url_anomalies"] += 1
                        changed = True

            application_status = _collapse_text(row.get("application_status")).lower()
            review_status_lower = review_status.lower()
            if application_status == "apply_failed" and review_status_lower in {"active", "rejected", "inactive", "closed", "withdrawn"}:
                categories["status_conflicts"].append(
                    {
                        "repairable": False,
                        "reason": "application_status records apply action outcome; application_review_status records website-visible lifecycle state",
                        "history": _history_ref(row, site_key),
                    }
                )

        strong_keys: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in scoped_history_rows:
            site_job_id = _collapse_text(row.get("site_job_id")).lower()
            url = _collapse_text(row.get("url")).lower()
            if site_job_id:
                strong_keys[f"site_job_id:{site_job_id}"].append(row)
            elif url and not _is_dashboard_like_url(url):
                strong_keys[f"url:{url}"].append(row)
        for key, rows in strong_keys.items():
            unique_job_ids = sorted({_collapse_text(row.get("job_id")) for row in rows if _collapse_text(row.get("job_id"))})
            if len(unique_job_ids) > 1:
                categories["duplicate_strong_keys"].append(
                    {
                        "repairable": False,
                        "site_key": site_key,
                        "strong_key": key,
                        "job_ids": unique_job_ids,
                        "histories": [_history_ref(row, site_key) for row in rows],
                    }
                )

        if apply and changed:
            writer = getattr(site_store, "_write_history_jobs")
            writer(site_key, history_rows)

    category_counts = {key: len(value) for key, value in categories.items()}
    safe_repairable_count = sum(
        1
        for key in ("rematchable_unmatched_reviews", "missing_site_job_id", "missing_review_details", "dashboard_url_anomalies")
        for item in categories[key]
        if bool(item.get("repairable"))
    )
    return {
        "generated_at": generated_at,
        "mode": "apply" if apply else "dry_run",
        "source": {
            "filters": {
                "since": since_date.isoformat() if since_date is not None else "",
                "all_time": since_date is None,
            },
            "history_row_count": scanned_history_rows,
            "review_record_count": scanned_review_rows,
        },
        "totals": {
            "issue_count": sum(category_counts.values()),
            "safe_repairable_count": safe_repairable_count,
            "applied_count": sum(applied.values()),
        },
        "category_counts": category_counts,
        "applied_counts": dict(applied),
        "categories": categories,
    }


def save_history_repair_plan(plan: dict[str, Any], *, workspace: Path | str) -> Path:
    workspace_path = Path(workspace)
    path = ensure_dir(workspace_path / HISTORY_REPAIR_PLAN_RELATIVE_PATH.parent) / HISTORY_REPAIR_PLAN_RELATIVE_PATH.name
    write_json(path, plan)
    return path
