"""Build a machine-readable application lifecycle summary."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import re
from pathlib import Path
from typing import Any

from careereng.career.applications.job_store import JobStore
from careereng.platform.persistence import JSONLStore
from careereng.career.applications.site_store import SiteStore
from careereng.platform.reporting import ReportArtifactStore
from careereng.utils import ensure_dir, now_iso, safe_file_stem


APPLICATION_SUMMARY_RELATIVE_PATH = Path("application_summary") / "application_summary.json"
DEFAULT_SUMMARY_SINCE = "2026-04-01"
KNOWN_PIPELINE_STAGES = {"received", "resume_review", "in_process", "assessment", "interview", "offer"}
KNOWN_STAGES = {*KNOWN_PIPELINE_STAGES, "rejected", "closed", "unknown"}


def _collapse_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _normalize_status(value: Any) -> str:
    return _collapse_text(value).lower() or ""


def _normalize_stage(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return text or ""


def _stage_for_distribution(row: dict[str, Any]) -> str:
    stage = _normalize_stage(row.get("application_review_stage"))
    if stage:
        return stage
    if _normalize_status(row.get("application_review_status")) == "rejected":
        return "rejected"
    return "unknown"


def _effective_status(row: dict[str, Any]) -> str:
    review_status = _normalize_status(row.get("application_review_status"))
    if review_status:
        return review_status
    application_status = _normalize_status(row.get("application_status"))
    if application_status:
        return application_status
    apply_state = _normalize_status(row.get("apply_state"))
    if apply_state == "terminal_blocked":
        return "blocked"
    if apply_state == "terminal_apply_failed":
        return "apply_failed"
    if apply_state == "terminal_submitted":
        return "submitted"
    if apply_state == "terminal_already_applied":
        return "already_applied"
    return "unknown"


def _parse_date(value: Any) -> date | None:
    text = _collapse_text(value)
    if not text:
        return None
    candidates = [text]
    if len(text) >= 10:
        candidates.append(text[:10])
    for candidate in candidates:
        normalized = candidate.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized).date()
        except ValueError:
            try:
                return datetime.strptime(candidate[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
    return None


def _days_between(start: Any, end: Any) -> int | None:
    start_date = _parse_date(start)
    end_date = _parse_date(end)
    if start_date is None or end_date is None:
        return None
    return max(0, (end_date - start_date).days)


def _date_basis(row: dict[str, Any]) -> tuple[str, str]:
    for field, basis in (
        ("last_submitted_at", "last_submitted_at"),
        ("application_updated_at", "application_updated_at"),
        ("first_seen_at", "first_seen_at"),
    ):
        value = _collapse_text(row.get(field))
        if value:
            return value, basis
    return "", "unknown"


def _scope_date(row: dict[str, Any]) -> tuple[date | None, str]:
    for field in (
        "last_submitted_at",
        "application_review_checked_at",
        "checked_at",
        "ts",
        "application_updated_at",
        "first_seen_at",
        "last_seen_at",
    ):
        value = row.get(field)
        parsed = _parse_date(value)
        if parsed is not None:
            return parsed, field
    return None, "unknown"


def _row_in_scope(row: dict[str, Any], since_date: date | None) -> bool:
    if since_date is None:
        return True
    row_date, _basis = _scope_date(row)
    if row_date is None:
        return True
    return row_date >= since_date


def _source_record(row: dict[str, Any], site_key: str) -> dict[str, Any]:
    return {
        "site_key": site_key,
        "title": _collapse_text(row.get("title")),
        "site_job_id": _collapse_text(row.get("site_job_id") or row.get("source_job_id")),
        "url": _collapse_text(row.get("url") or row.get("application_review_url")),
    }


def _history_record(row: dict[str, Any], site_key: str) -> dict[str, Any]:
    return {
        **_source_record(row, site_key),
        "job_id": _collapse_text(row.get("job_id")),
        "application_status": _normalize_status(row.get("application_status")),
        "application_review_status": _normalize_status(row.get("application_review_status")),
        "application_review_status_raw": _collapse_text(row.get("application_review_status_raw")),
        "application_review_stage": _stage_for_distribution(row),
        "application_review_checked_at": _collapse_text(row.get("application_review_checked_at")),
        "last_submitted_at": _collapse_text(row.get("last_submitted_at")),
        "first_seen_at": _collapse_text(row.get("first_seen_at")),
    }


def _transition_type(row: dict[str, Any]) -> str:
    previous_stage = _normalize_stage(row.get("previous_application_review_stage"))
    current_stage = _normalize_stage(row.get("application_review_stage"))
    previous_status = _normalize_status(row.get("previous_application_review_status"))
    current_status = _normalize_status(row.get("application_review_status"))
    if current_status == "rejected" or current_stage == "rejected":
        if previous_stage in {"received", "resume_review", "in_process", "assessment", "interview"}:
            return f"{previous_stage}_to_rejected"
        if previous_status == "active":
            return "active_to_rejected"
        return "unknown_to_rejected"
    if previous_stage and current_stage and previous_stage != current_stage:
        if previous_stage == "received" and current_stage == "resume_review":
            return "received_to_resume_review"
        return "stage_changed"
    if previous_status and current_status and previous_status != current_status:
        return "status_changed"
    return "status_changed"


def _load_review_rows(workspace: Path, site_key: str) -> list[dict[str, Any]]:
    review_dir = workspace / "sites" / safe_file_stem(site_key) / "applications" / "reviews"
    rows: list[dict[str, Any]] = []
    if not review_dir.exists():
        return rows
    for path in sorted(review_dir.glob("*.jsonl")):
        for row in JSONLStore(path).read_all():
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _registered_or_existing_site_keys(site_store: SiteStore, workspace: Path) -> list[str]:
    keys = {str(row.get("site_key") or "").strip() for row in site_store.list_sites(None)}
    sites_dir = workspace / "sites"
    if sites_dir.exists():
        for path in sites_dir.iterdir():
            if path.is_dir() and path.name != "registry.jsonl":
                keys.add(path.name)
    return sorted(safe_file_stem(key) for key in keys if key)


def _empty_site_summary(site_key: str) -> dict[str, Any]:
    return {
        "site_key": site_key,
        "history_jobs": 0,
        "submitted": 0,
        "already_applied": 0,
        "active": 0,
        "rejected": 0,
        "blocked": 0,
        "apply_failed": 0,
        "unmatched_reviews": 0,
        "legacy_unmatched_reviews": 0,
        "rematched_reviews": 0,
        "status_distribution": {},
        "stage_distribution": {},
    }


def _increment_job_counts(
    *,
    row: dict[str, Any],
    totals: Counter[str],
    site_summary: dict[str, Any],
    status_counter: Counter[str],
    stage_counter: Counter[str],
) -> None:
    totals["history_jobs"] += 1
    site_summary["history_jobs"] += 1

    application_status = _normalize_status(row.get("application_status"))
    review_status = _normalize_status(row.get("application_review_status"))
    effective_status = _effective_status(row)
    stage = _stage_for_distribution(row)
    status_counter[effective_status] += 1
    stage_counter[stage if stage in KNOWN_STAGES else stage] += 1
    site_summary["status_distribution"][effective_status] = int(site_summary["status_distribution"].get(effective_status, 0)) + 1
    site_summary["stage_distribution"][stage] = int(site_summary["stage_distribution"].get(stage, 0)) + 1

    if application_status == "submitted":
        totals["submitted"] += 1
        site_summary["submitted"] += 1
    if application_status == "already_applied":
        totals["already_applied"] += 1
        site_summary["already_applied"] += 1
    if application_status == "blocked" or effective_status == "blocked":
        totals["blocked"] += 1
        site_summary["blocked"] += 1
    if application_status == "apply_failed" or effective_status == "apply_failed":
        totals["apply_failed"] += 1
        site_summary["apply_failed"] += 1
    if review_status == "active":
        totals["active"] += 1
        site_summary["active"] += 1
    if review_status == "rejected":
        totals["rejected"] += 1
        site_summary["rejected"] += 1
    if not review_status:
        totals["unknown_review_status"] += 1


def _build_transition(site_key: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        **_source_record(row, site_key),
        "previous_status": _normalize_status(row.get("previous_application_review_status")),
        "current_status": _normalize_status(row.get("application_review_status")),
        "previous_raw": _collapse_text(row.get("previous_application_review_status_raw")),
        "current_raw": _collapse_text(row.get("application_review_status_raw")),
        "previous_stage": _normalize_stage(row.get("previous_application_review_stage")),
        "current_stage": _stage_for_distribution(row),
        "checked_at": _collapse_text(row.get("checked_at") or row.get("ts")),
        "transition_type": _transition_type(row),
    }


def _build_unmatched_reviews(
    review_rows: list[dict[str, Any]],
    site_key: str,
    site_store: SiteStore,
) -> tuple[list[dict[str, Any]], int, int]:
    def grouped_count(rows: list[dict[str, Any]]) -> int:
        keys: set[tuple[str, str, str, str]] = set()
        for row in rows:
            base = _source_record(row, site_key)
            keys.add((site_key, base["site_job_id"], base["url"], base["title"].lower()))
        return len(keys)

    rematched_count = 0
    unmatched_rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    for row in review_rows:
        if _collapse_text(row.get("matched_job_id")):
            continue
        candidate_rows.append(row)
        candidates.append(
            {
                "title": _collapse_text(row.get("title")),
                "url": _collapse_text(row.get("url")),
                "site_job_id": _collapse_text(row.get("site_job_id") or row.get("source_job_id")),
            }
        )
    legacy_unmatched_count = grouped_count(candidate_rows)
    if candidates:
        try:
            matches = site_store.match_history_rows(site_key, candidates)
        except Exception:
            matches = [None] * len(candidates)
    else:
        matches = []
    for row, match in zip(candidate_rows, matches):
        if isinstance(match, dict) and _collapse_text(match.get("job_id")):
            rematched_count += 1
            continue
        unmatched_rows.append(row)

    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in unmatched_rows:
        base = _source_record(row, site_key)
        key = (
            site_key,
            base["site_job_id"],
            base["url"],
            base["title"].lower(),
        )
        checked_at = _collapse_text(row.get("checked_at") or row.get("ts"))
        current = grouped.get(key)
        if current is None:
            grouped[key] = {
                **base,
                "application_review_status": _normalize_status(row.get("application_review_status")),
                "application_review_status_raw": _collapse_text(row.get("application_review_status_raw")),
                "application_review_stage": _normalize_stage(row.get("application_review_stage")),
                "checked_at": checked_at,
                "batch_id": _collapse_text(row.get("batch_id")),
                "seen_count": 1,
                "latest_seen_at": checked_at,
            }
            continue
        current["seen_count"] = int(current.get("seen_count") or 0) + 1
        if checked_at and checked_at >= str(current.get("latest_seen_at") or ""):
            current.update(
                {
                    "application_review_status": _normalize_status(row.get("application_review_status")),
                    "application_review_status_raw": _collapse_text(row.get("application_review_status_raw")),
                    "application_review_stage": _normalize_stage(row.get("application_review_stage")),
                    "checked_at": checked_at,
                    "batch_id": _collapse_text(row.get("batch_id")),
                    "latest_seen_at": checked_at,
                }
            )
    return (
        sorted(grouped.values(), key=lambda item: (item.get("site_key", ""), item.get("latest_seen_at", ""), item.get("title", ""))),
        rematched_count,
        legacy_unmatched_count,
    )


def _rejection_latency_item(row: dict[str, Any], site_key: str) -> tuple[str, dict[str, Any]]:
    rejected_at = _collapse_text(row.get("application_review_checked_at"))
    submitted_at, basis = _date_basis(row)
    days = _days_between(submitted_at, rejected_at)
    bucket = "unknown"
    if days is not None:
        if days <= 3:
            bucket = "fast_0_3_days"
        elif days <= 7:
            bucket = "within_7_days"
        elif days <= 14:
            bucket = "within_14_days"
        else:
            bucket = "slow_15_plus_days"
    return bucket, {
        **_source_record(row, site_key),
        "days_to_rejection": days,
        "date_basis": basis,
        "submitted_at": submitted_at,
        "rejected_at": rejected_at,
    }


def _days_since_submitted(row: dict[str, Any], generated_at: str) -> int | None:
    submitted_at, _basis = _date_basis(row)
    return _days_between(submitted_at, generated_at)


def _active_pipeline_item(row: dict[str, Any], site_key: str, generated_at: str) -> dict[str, Any]:
    return {
        **_source_record(row, site_key),
        "application_review_status": _normalize_status(row.get("application_review_status")),
        "status_raw": _collapse_text(row.get("application_review_status_raw")),
        "stage": _stage_for_distribution(row),
        "checked_at": _collapse_text(row.get("application_review_checked_at")),
        "days_since_submitted": _days_since_submitted(row, generated_at),
    }


def _signal(
    *,
    signal_type: str,
    row: dict[str, Any],
    site_key: str,
    confidence: float,
    weight: float,
    evidence: str,
) -> dict[str, Any]:
    return {
        "signal_type": signal_type,
        **_source_record(row, site_key),
        "confidence": confidence,
        "weight": weight,
        "evidence": evidence,
    }


def _transition_signal(transition: dict[str, Any]) -> dict[str, Any] | None:
    signal_type = {
        "in_process_to_rejected": "in_process_rejection",
        "resume_review_to_rejected": "resume_review_rejection",
    }.get(str(transition.get("transition_type") or ""))
    if not signal_type:
        return None
    evidence = " -> ".join(
        part
        for part in (
            str(transition.get("previous_raw") or transition.get("previous_stage") or ""),
            str(transition.get("current_raw") or transition.get("current_stage") or ""),
        )
        if part
    )
    return {
        "signal_type": signal_type,
        "site_key": transition.get("site_key"),
        "title": transition.get("title"),
        "site_job_id": transition.get("site_job_id"),
        "url": transition.get("url"),
        "confidence": 0.9,
        "weight": 0.8 if signal_type == "in_process_rejection" else 0.7,
        "evidence": evidence,
    }


def build_application_summary(
    *,
    workspace: Path | str,
    project_root: Path | str | None = None,
    since: str | None = DEFAULT_SUMMARY_SINCE,
) -> dict[str, Any]:
    workspace_path = Path(workspace)
    root = Path(project_root) if project_root is not None else workspace_path.parent
    site_store = SiteStore(workspace_path, project_root=root)
    job_store = JobStore(workspace_path)
    generated_at = now_iso()
    since_value = _collapse_text(since)
    since_date = _parse_date(since_value) if since_value else None

    site_keys = _registered_or_existing_site_keys(site_store, workspace_path)
    totals: Counter[str] = Counter()
    global_status_distribution: Counter[str] = Counter()
    global_stage_distribution: Counter[str] = Counter()
    by_site: list[dict[str, Any]] = []
    lifecycle_transitions: list[dict[str, Any]] = []
    all_unmatched_reviews: list[dict[str, Any]] = []
    active_pipeline: dict[str, list[dict[str, Any]]] = {stage: [] for stage in sorted(KNOWN_PIPELINE_STAGES | {"unknown"})}
    rejection_buckets: dict[str, list[dict[str, Any]]] = {
        "fast_0_3_days": [],
        "within_7_days": [],
        "within_14_days": [],
        "slow_15_plus_days": [],
        "unknown": [],
    }
    signals: list[dict[str, Any]] = []
    history_job_count = 0
    review_record_count = 0

    for site_key in site_keys:
        site_summary = _empty_site_summary(site_key)
        raw_history_rows = site_store.list_jobs(site_key)
        raw_review_rows = _load_review_rows(workspace_path, site_key)
        history_rows = [row for row in raw_history_rows if _row_in_scope(row, since_date)]
        review_rows = [row for row in raw_review_rows if _row_in_scope(row, since_date)]
        history_job_count += len(history_rows)
        review_record_count += len(review_rows)

        for row in history_rows:
            if not isinstance(row, dict):
                continue
            _increment_job_counts(
                row=row,
                totals=totals,
                site_summary=site_summary,
                status_counter=global_status_distribution,
                stage_counter=global_stage_distribution,
            )
            review_status = _normalize_status(row.get("application_review_status"))
            stage = _stage_for_distribution(row)
            if review_status == "rejected":
                bucket, item = _rejection_latency_item(row, site_key)
                rejection_buckets[bucket].append(item)
            if review_status == "active" or stage in KNOWN_PIPELINE_STAGES:
                active_pipeline.setdefault(stage if stage in KNOWN_PIPELINE_STAGES else "unknown", []).append(
                    _active_pipeline_item(row, site_key, generated_at)
                )
                if stage == "resume_review":
                    signals.append(
                        _signal(
                            signal_type="active_resume_review",
                            row=row,
                            site_key=site_key,
                            confidence=0.8,
                            weight=0.4,
                            evidence=_collapse_text(row.get("application_review_status_raw")) or "active resume_review",
                        )
                    )
                if stage == "in_process":
                    signals.append(
                        _signal(
                            signal_type="active_in_process",
                            row=row,
                            site_key=site_key,
                            confidence=0.85,
                            weight=0.6,
                            evidence=_collapse_text(row.get("application_review_status_raw")) or "active in_process",
                        )
                    )
                days_pending = _days_since_submitted(row, generated_at)
                if days_pending is not None and days_pending >= 14:
                    signals.append(
                        _signal(
                            signal_type="long_pending_application",
                            row=row,
                            site_key=site_key,
                            confidence=0.7,
                            weight=0.3,
                            evidence=f"pending for {days_pending} days",
                        )
                    )

        for row in review_rows:
            if bool(row.get("application_review_status_changed")) and _collapse_text(row.get("matched_job_id")):
                transition = _build_transition(site_key, row)
                lifecycle_transitions.append(transition)
                signal = _transition_signal(transition)
                if signal:
                    signals.append(signal)

        unmatched, rematched_count, legacy_unmatched_count = _build_unmatched_reviews(review_rows, site_key, site_store)
        site_summary["unmatched_reviews"] = len(unmatched)
        site_summary["legacy_unmatched_reviews"] = legacy_unmatched_count
        site_summary["rematched_reviews"] = rematched_count
        totals["legacy_unmatched_reviews"] += legacy_unmatched_count
        totals["rematched_reviews"] += rematched_count
        all_unmatched_reviews.extend(unmatched)
        by_site.append(site_summary)

    totals["unmatched_reviews"] = len(all_unmatched_reviews)
    for item in all_unmatched_reviews:
        signals.append(
            {
                "signal_type": "unmatched_review",
                "site_key": item.get("site_key"),
                "title": item.get("title"),
                "site_job_id": item.get("site_job_id"),
                "url": item.get("url"),
                "confidence": 0.7,
                "weight": 0.4,
                "evidence": f"unmatched review seen {item.get('seen_count') or 1} time(s)",
            }
        )

    for item in rejection_buckets["fast_0_3_days"]:
        signals.append(
            {
                "signal_type": "fast_rejection",
                "site_key": item.get("site_key"),
                "title": item.get("title"),
                "site_job_id": item.get("site_job_id"),
                "url": item.get("url"),
                "confidence": 0.75,
                "weight": 0.5,
                "evidence": f"rejected after {item.get('days_to_rejection')} days",
            }
        )

    batches = job_store.list_batches(include_terminal=True)
    return {
        "generated_at": generated_at,
        "source": {
            "history_jobs": history_job_count > 0,
            "application_reviews": review_record_count > 0,
            "batches": bool(batches),
            "filters": {
                "since": since_date.isoformat() if since_date is not None else "",
                "all_time": since_date is None,
            },
            "site_count": len(site_keys),
            "history_job_count": history_job_count,
            "review_record_count": review_record_count,
            "batch_count": len(batches),
        },
        "totals": {
            "history_jobs": int(totals.get("history_jobs") or 0),
            "submitted": int(totals.get("submitted") or 0),
            "already_applied": int(totals.get("already_applied") or 0),
            "active": int(totals.get("active") or 0),
            "rejected": int(totals.get("rejected") or 0),
            "blocked": int(totals.get("blocked") or 0),
            "apply_failed": int(totals.get("apply_failed") or 0),
            "unknown_review_status": int(totals.get("unknown_review_status") or 0),
            "unmatched_reviews": int(totals.get("unmatched_reviews") or 0),
            "legacy_unmatched_reviews": int(totals.get("legacy_unmatched_reviews") or 0),
            "rematched_reviews": int(totals.get("rematched_reviews") or 0),
        },
        "by_site": sorted(by_site, key=lambda item: str(item.get("site_key") or "")),
        "stage_distribution": dict(sorted(global_stage_distribution.items())),
        "status_distribution": dict(sorted(global_status_distribution.items())),
        "lifecycle_transitions": sorted(
            lifecycle_transitions,
            key=lambda item: (str(item.get("checked_at") or ""), str(item.get("site_key") or ""), str(item.get("title") or "")),
        ),
        "rejection_latency": {
            "buckets": {key: len(value) for key, value in rejection_buckets.items()},
            "items": [
                item
                for key in ("fast_0_3_days", "within_7_days", "within_14_days", "slow_15_plus_days", "unknown")
                for item in sorted(rejection_buckets[key], key=lambda row: (str(row.get("site_key") or ""), str(row.get("title") or "")))
            ],
        },
        "active_pipeline": {
            key: sorted(value, key=lambda row: (str(row.get("site_key") or ""), str(row.get("title") or "")))
            for key, value in sorted(active_pipeline.items())
        },
        "unmatched_reviews": all_unmatched_reviews,
        "signals": sorted(
            signals,
            key=lambda item: (
                str(item.get("signal_type") or ""),
                str(item.get("site_key") or ""),
                str(item.get("title") or ""),
                str(item.get("site_job_id") or ""),
            ),
        ),
    }


def save_application_summary(summary: dict[str, Any], *, workspace: Path | str) -> Path:
    workspace_path = Path(workspace)
    path = ensure_dir(workspace_path / APPLICATION_SUMMARY_RELATIVE_PATH.parent) / APPLICATION_SUMMARY_RELATIVE_PATH.name
    ReportArtifactStore(workspace_path).write_json(
        artifact_id="career_application_summary",
        domain="career_applications",
        report_type="application_summary",
        json_path=path,
        payload=summary,
    )
    return path
