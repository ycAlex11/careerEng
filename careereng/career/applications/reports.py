"""History-aware job batch reports."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from careereng.platform.observability import build_metrics_summary
from careereng.platform.reporting import ReportArtifactStore
from careereng.career.applications.job_store import JobStore
from careereng.career.applications.site_store import SiteStore
from careereng.utils import ensure_dir, safe_file_stem, today_str


APPLIED_RELATED_STATUSES = {"submitted", "already_applied"}
TERMINAL_APPLICATION_STATUSES = {"submitted", "already_applied", "apply_failed", "blocked"}
APPLICATION_REVIEW_STATUSES = ("active", "resumable", "inactive", "rejected", "closed", "withdrawn", "unknown", "blocked")
APPLICATION_REVIEW_LABELS = {
    "active": "Active / In Process",
    "resumable": "Not Submitted / Resume Needed",
    "inactive": "Inactive",
    "rejected": "Rejected",
    "closed": "Closed",
    "withdrawn": "Withdrawn",
    "blocked": "Blocked",
    "unknown": "Unknown",
}
REJECTED_TRANSITION_LABELS = {
    "received": "简历初筛后被拒",
    "resume_review": "简历评审后被拒",
    "in_process": "流程中被拒",
    "assessment": "评估阶段后被拒",
    "interview": "面试阶段后被拒",
}


def _collapse_text(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text


def _normalize_status(value: Any) -> str:
    text = _collapse_text(str(value or "")).lower()
    if text.startswith("terminal_"):
        text = text[len("terminal_") :]
    return text


def _job_identity(row: dict[str, Any]) -> str:
    for field in ("job_id", "canonical_job_id", "url"):
        value = str(row.get(field) or "").strip()
        if value:
            return f"{field}:{value}"
    title = _collapse_text(str(row.get("title") or "")).lower()
    location = _collapse_text(str(row.get("location") or "")).lower()
    posted = _collapse_text(str(row.get("posted_label") or row.get("posted_at") or "")).lower()
    return f"opaque:{title}|{location}|{posted}"


def _merge_rows(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in incoming.items():
        if value in (None, ""):
            continue
        merged[key] = value
    return merged


def _unique_run_jobs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered_keys: list[str] = []
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = _job_identity(row)
        if key not in index:
            ordered_keys.append(key)
            index[key] = dict(row)
        else:
            index[key] = _merge_rows(index[key], row)
    return [index[key] for key in ordered_keys]


def _application_status(row: dict[str, Any]) -> str:
    status = _normalize_status(row.get("application_status"))
    if status in TERMINAL_APPLICATION_STATUSES:
        return status
    decision_status = _normalize_status(row.get("decision_status"))
    if decision_status == "already_applied":
        return "already_applied"
    return ""


def _decision_status(row: dict[str, Any]) -> str:
    status = _normalize_status(row.get("decision_status"))
    if status:
        return status
    return ""


def _job_status(row: dict[str, Any]) -> str:
    status = _application_status(row)
    if status:
        return status
    status = _decision_status(row)
    if status:
        return status
    return _normalize_status(row.get("apply_state"))


def _truncate_summary(text: str, *, max_chars: int = 320) -> str:
    collapsed = _collapse_text(text)
    if not collapsed:
        return ""
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[: max_chars - 1].rstrip() + "…"


def _description_summary(workspace: Path, row: dict[str, Any], *, max_chars: int = 320) -> str:
    ref = str(row.get("description_ref") or "").strip()
    if ref:
        path = Path(ref)
        if not path.is_absolute():
            path = workspace / path
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            text = ""
        summary = _truncate_summary(text, max_chars=max_chars)
        if summary:
            return summary
    fallback = (
        str(row.get("match_reason_final") or "")
        or str(row.get("fit_reason") or "")
        or str(row.get("match_reason_initial") or "")
    )
    return _truncate_summary(fallback, max_chars=max_chars)


def _batch_report_date(batch: dict[str, Any]) -> str:
    created_at = str(batch.get("created_at") or "").strip()
    match = re.match(r"^\d{4}-\d{2}-\d{2}", created_at)
    if match:
        return match.group(0)
    return today_str()


def _site_report_paths(workspace: Path, site_key: str, batch_id: str, report_date: str) -> tuple[Path, Path]:
    report_dir = ensure_dir(workspace / "reports" / "jobs" / safe_file_stem(report_date) / "sites" / safe_file_stem(site_key))
    batch_key = safe_file_stem(batch_id)
    return report_dir / f"{batch_key}.json", report_dir / f"{batch_key}.md"


def _batch_report_paths(workspace: Path, batch_id: str, report_date: str) -> tuple[Path, Path]:
    report_dir = ensure_dir(workspace / "reports" / "jobs" / safe_file_stem(report_date))
    batch_key = safe_file_stem(batch_id)
    return report_dir / f"{batch_key}.json", report_dir / f"{batch_key}.md"


def _daily_final_report_paths(workspace: Path, report_date: str) -> tuple[Path, Path]:
    report_dir = ensure_dir(workspace / "reports" / "jobs" / safe_file_stem(report_date))
    return report_dir / "final.json", report_dir / "final.md"


def _write_report_artifact(
    *,
    workspace: Path,
    artifact_id: str,
    report_type: str,
    json_path: Path,
    markdown_path: Path,
    payload: dict[str, Any],
    markdown: str,
    metadata: dict[str, Any],
) -> None:
    """Persist a career report through the shared platform artifact store."""
    ReportArtifactStore(workspace).write_json_markdown(
        artifact_id=artifact_id,
        domain="career_applications",
        report_type=report_type,
        json_path=json_path,
        markdown_path=markdown_path,
        payload=payload,
        markdown=markdown,
        metadata=metadata,
    )


def _report_job_entry(
    workspace: Path,
    row: dict[str, Any],
    *,
    status: str = "",
    current_status: str = "",
    history_status: str = "",
) -> dict[str, Any]:
    return {
        "job_id": str(row.get("job_id") or ""),
        "canonical_job_id": str(row.get("canonical_job_id") or ""),
        "title": str(row.get("title") or ""),
        "url": str(row.get("url") or ""),
        "location": str(row.get("location") or ""),
        "posted": str(row.get("posted_label") or row.get("posted_at") or ""),
        "status": status or _job_status(row),
        "current_status": current_status,
        "history_status": history_status,
        "last_submitted_at": str(row.get("last_submitted_at") or ""),
        "application_updated_at": str(row.get("application_updated_at") or ""),
        "jd_summary": _description_summary(workspace, row),
    }


def _is_new_history_match(history_row: dict[str, Any] | None, *, batch_created_at: str) -> bool:
    if not isinstance(history_row, dict):
        return True
    first_seen_at = str(history_row.get("first_seen_at") or "").strip()
    if not first_seen_at or not batch_created_at:
        return False
    return first_seen_at >= batch_created_at


def _was_previously_applied(history_row: dict[str, Any] | None, *, batch_created_at: str) -> bool:
    if not isinstance(history_row, dict):
        return False
    if _is_new_history_match(history_row, batch_created_at=batch_created_at):
        return False
    return _application_status(history_row) in APPLIED_RELATED_STATUSES


def _has_site_applied_signal(row: dict[str, Any], history_row: dict[str, Any] | None, *, batch_id: str) -> bool:
    if _application_status(row) == "already_applied":
        return True
    if not isinstance(history_row, dict):
        return False
    if str(history_row.get("application_review_batch_id") or "") != batch_id:
        return False
    return bool(_review_status(history_row))


def _new_job_status(row: dict[str, Any]) -> str:
    return _job_status(row) or "not_applied"


def _review_status(row: dict[str, Any]) -> str:
    status = _normalize_status(row.get("application_review_status"))
    return status if status in APPLICATION_REVIEW_STATUSES else ""


def _review_entry(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": str(row.get("job_id") or row.get("matched_job_id") or ""),
        "canonical_job_id": str(row.get("canonical_job_id") or ""),
        "title": str(row.get("title") or ""),
        "url": str(row.get("url") or row.get("application_review_url") or ""),
        "site_job_id": str(row.get("site_job_id") or ""),
        "application_review_status": _review_status(row),
        "application_review_status_raw": str(row.get("application_review_status_raw") or ""),
        "application_review_stage": str(row.get("application_review_stage") or ""),
        "previous_application_review_status": _normalize_status(row.get("previous_application_review_status")),
        "previous_application_review_status_raw": str(row.get("previous_application_review_status_raw") or ""),
        "previous_application_review_stage": str(row.get("previous_application_review_stage") or ""),
        "application_review_status_changed": bool(row.get("application_review_status_changed")),
        "application_review_checked_at": str(row.get("application_review_checked_at") or row.get("checked_at") or ""),
    }


def _read_site_review_rows(workspace: Path, site_key: str, batch_id: str) -> list[dict[str, Any]]:
    reviews_dir = workspace / "sites" / safe_file_stem(site_key) / "applications" / "reviews"
    if not reviews_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(reviews_dir.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            if str(row.get("batch_id") or "") != batch_id:
                continue
            rows.append(row)
    return rows


def _review_summary(
    *,
    site_store: SiteStore,
    workspace: Path,
    site_key: str,
    batch_id: str,
) -> dict[str, Any]:
    history_rows = [
        row
        for row in site_store.list_jobs(site_key)
        if isinstance(row, dict)
        and _review_status(row)
        and str(row.get("application_review_batch_id") or "") == batch_id
    ]
    history_review_jobs = [_review_entry(row) for row in history_rows]
    review_rows = _read_site_review_rows(workspace, site_key, batch_id)
    unmatched_review_records = [
        _review_entry(row)
        for row in review_rows
        if not str(row.get("matched_job_id") or "").strip()
    ]
    review_changes = [
        _review_entry(row)
        for row in review_rows
        if bool(row.get("application_review_status_changed")) and str(row.get("matched_job_id") or "").strip()
    ]
    counts = {status: 0 for status in APPLICATION_REVIEW_STATUSES}
    for row in history_review_jobs:
        status = str(row.get("application_review_status") or "")
        if status in counts:
            counts[status] += 1
    return {
        "reviewed_count": len(history_review_jobs),
        "unmatched_review_count": len(unmatched_review_records),
        "review_status_counts": counts,
        "reviewed_jobs": history_review_jobs,
        "review_changes": review_changes,
        "changed_count": len(review_changes),
        "unmatched_review_records": unmatched_review_records,
    }


def _append_new_job_lines(lines: list[str], jobs: list[dict[str, Any]], *, include_site: bool) -> None:
    if not jobs:
        lines.extend(["", "- 无"])
        return
    for job in jobs:
        title = str(job.get("title") or "Untitled")
        location = str(job.get("location") or "-")
        posted = str(job.get("posted") or "-")
        status = str(job.get("status") or "-")
        summary = str(job.get("jd_summary") or "")
        lines.extend(["", f"#### {title}"])
        if include_site:
            lines.append(f"- Site: {job.get('site_name') or job.get('site_key') or 'site'}")
        lines.extend([f"- Status: {status}", f"- Location: {location}", f"- Posted: {posted}"])
        url = str(job.get("url") or "")
        if url:
            lines.append(f"- URL: {url}")
        if summary:
            lines.append(f"- JD 摘要: {summary}")


def _review_job_line(job: dict[str, Any], *, include_site: bool, include_status_detail: bool = True) -> str:
    title = str(job.get("title") or "Untitled")
    site_job_id = str(job.get("site_job_id") or "").strip()
    if site_job_id:
        title = f"{title} ({site_job_id})"
    if include_site:
        site_name = str(job.get("site_name") or job.get("site_key") or "site")
        title = f"{site_name}: {title}"
    if include_status_detail:
        raw_status = _collapse_text(str(job.get("application_review_status_raw") or ""))
        stage = _collapse_text(str(job.get("application_review_stage") or ""))
        if raw_status and stage and raw_status.lower() != stage.lower():
            title = f"{title} - {raw_status} / {stage}"
        elif raw_status:
            title = f"{title} - {raw_status}"
        elif stage:
            title = f"{title} - {stage}"
    return f"- {title}"


def _changed_pair(previous: Any, current: Any) -> tuple[str, str] | None:
    old = _collapse_text(str(previous or ""))
    new = _collapse_text(str(current or ""))
    if not old or not new:
        return None
    if old.lower() == new.lower():
        return None
    return old, new


def _review_transition_pair(job: dict[str, Any]) -> tuple[str, str] | None:
    for previous_key, current_key in (
        ("previous_application_review_status_raw", "application_review_status_raw"),
        ("previous_application_review_stage", "application_review_stage"),
        ("previous_application_review_status", "application_review_status"),
    ):
        pair = _changed_pair(job.get(previous_key), job.get(current_key))
        if pair is not None:
            return pair
    return None


def _review_change_text(job: dict[str, Any]) -> str:
    pair = _review_transition_pair(job)
    previous_stage = _normalize_status(job.get("previous_application_review_stage"))
    current_status = _review_status(job)
    if current_status == "rejected" and previous_stage in REJECTED_TRANSITION_LABELS:
        label = REJECTED_TRANSITION_LABELS[previous_stage]
        if pair is not None:
            old, new = pair
            return f"{label}: {old} -> {new}"
        return label
    if pair is not None:
        old, new = pair
        return f"Status: {old} -> {new}"
    return "Status changed"


def _append_review_change_lines(lines: list[str], jobs: list[dict[str, Any]], *, include_site: bool) -> None:
    if not jobs:
        lines.extend(["", "- 无"])
        return
    for job in jobs:
        title = _review_job_line(job, include_site=include_site, include_status_detail=False).lstrip("- ")
        lines.append(f"- {title}: {_review_change_text(job)}")


def _append_review_group_lines(lines: list[str], reviewed_jobs: list[dict[str, Any]], *, include_site: bool) -> None:
    if not reviewed_jobs:
        lines.extend(["", "- 无"])
        return
    grouped: dict[str, list[dict[str, Any]]] = {status: [] for status in APPLICATION_REVIEW_STATUSES}
    for job in reviewed_jobs:
        status = str(job.get("application_review_status") or "unknown")
        if status not in grouped:
            status = "unknown"
        grouped[status].append(job)
    for status in APPLICATION_REVIEW_STATUSES:
        jobs = grouped[status]
        if not jobs:
            continue
        label = APPLICATION_REVIEW_LABELS.get(status, status.title())
        lines.extend(["", f"### {label}: {len(jobs)} 个"])
        lines.extend(_review_job_line(job, include_site=include_site) for job in jobs)


def _site_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {report.get('site_name') or report.get('site_key')} Job Report",
        "",
        f"- Batch: `{report.get('batch_id')}`",
        f"- 检索岗位: {report.get('retrieved_count', 0)}",
        f"- 本次新岗位: {report.get('new_jobs_count', 0)}",
        f"- 新岗位已投递: {report.get('new_submitted_count', 0)}",
        f"- 新岗位不符合: {report.get('new_filtered_out_count', 0)}",
        f"- 本次投递: {report.get('submitted_count', 0)}",
        f"- 已投递: {report.get('already_applied_count', 0)}",
        f"- 已检查申请状态: {report.get('application_review', {}).get('reviewed_count', 0)}",
        f"- 申请状态变化: {report.get('application_review', {}).get('changed_count', 0)}",
        f"- 未匹配网站申请记录: {report.get('application_review', {}).get('unmatched_review_count', 0)}",
    ]

    new_submitted_jobs = report.get("new_submitted_jobs") if isinstance(report.get("new_submitted_jobs"), list) else []
    new_unsubmitted_jobs = (
        report.get("new_unsubmitted_jobs") if isinstance(report.get("new_unsubmitted_jobs"), list) else []
    )
    lines.extend(["", "## 新增岗位", "", "### 投递"])
    _append_new_job_lines(lines, new_submitted_jobs, include_site=False)
    lines.extend(["", "### 没投递"])
    _append_new_job_lines(lines, new_unsubmitted_jobs, include_site=False)

    review = report.get("application_review") if isinstance(report.get("application_review"), dict) else {}
    review_changes = review.get("review_changes") if isinstance(review.get("review_changes"), list) else []
    lines.extend(["", "## 申请状态变化"])
    _append_review_change_lines(lines, review_changes, include_site=False)

    lines.extend(["", "## 申请状态检查"])
    reviewed_jobs = review.get("reviewed_jobs") if isinstance(review.get("reviewed_jobs"), list) else []
    _append_review_group_lines(lines, reviewed_jobs, include_site=False)
    return "\n".join(lines)


def _batch_markdown(report: dict[str, Any]) -> str:
    totals = report.get("totals") if isinstance(report.get("totals"), dict) else {}
    lines = [
        "# Job Batch Report",
        "",
        f"- Batch: `{report.get('batch_id')}`",
        f"- 检索岗位: {totals.get('retrieved_count', 0)}",
        f"- 本次新岗位: {totals.get('new_jobs_count', 0)}",
        f"- 新岗位已投递: {totals.get('new_submitted_count', 0)}",
        f"- 新岗位不符合: {totals.get('new_filtered_out_count', 0)}",
        f"- 本次投递: {totals.get('submitted_count', 0)}",
        f"- 已投递: {totals.get('already_applied_count', 0)}",
        f"- 已检查申请状态: {totals.get('application_reviewed_count', 0)}",
        f"- 申请状态变化: {totals.get('application_review_changed_count', 0)}",
        f"- 未匹配网站申请记录: {totals.get('unmatched_review_count', 0)}",
        "",
        "## Sites",
    ]
    site_reports = report.get("sites") if isinstance(report.get("sites"), list) else []
    if not site_reports:
        lines.extend(["", "- 无"])
    for site in site_reports:
        lines.append(
            f"- {site.get('site_name') or site.get('site_key')}: "
            f"检索 {site.get('retrieved_count', 0)}，"
            f"新岗位 {site.get('new_jobs_count', 0)}，"
            f"新投递 {site.get('new_submitted_count', 0)}，"
            f"新过滤 {site.get('new_filtered_out_count', 0)}，"
            f"申请状态检查 {site.get('application_review', {}).get('reviewed_count', 0)}，"
            f"状态变化 {site.get('application_review', {}).get('changed_count', 0)}"
        )

    new_submitted_jobs = report.get("new_submitted_jobs") if isinstance(report.get("new_submitted_jobs"), list) else []
    new_unsubmitted_jobs = (
        report.get("new_unsubmitted_jobs") if isinstance(report.get("new_unsubmitted_jobs"), list) else []
    )
    lines.extend(["", "## 新增岗位", "", "### 投递"])
    _append_new_job_lines(lines, new_submitted_jobs, include_site=True)
    lines.extend(["", "### 没投递"])
    _append_new_job_lines(lines, new_unsubmitted_jobs, include_site=True)

    review_changes = report.get("application_review_changes") if isinstance(report.get("application_review_changes"), list) else []
    lines.extend(["", "## 申请状态变化"])
    _append_review_change_lines(lines, review_changes, include_site=True)

    reviewed_jobs = report.get("reviewed_jobs") if isinstance(report.get("reviewed_jobs"), list) else []
    lines.extend(["", "## 申请状态检查"])
    _append_review_group_lines(lines, reviewed_jobs, include_site=True)
    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    usage = metrics.get("totals") if isinstance(metrics.get("totals"), dict) else {}
    performance = metrics.get("performance") if isinstance(metrics.get("performance"), dict) else {}
    performance_totals = performance.get("totals") if isinstance(performance.get("totals"), dict) else {}
    lines.extend(
        [
            "",
            "## 性能",
            f"- LLM 调用: {usage.get('calls', 0)}；输入 token: {usage.get('input_tokens', 0)}；输出 token: {usage.get('output_tokens', 0)}；未知 token 调用: {usage.get('unknown_token_calls', 0)}",
            f"- 执行事件: {performance_totals.get('events', 0)}；浏览器工具: {performance_totals.get('browser_tool_calls', 0)}；状态工具: {performance_totals.get('state_tool_calls', 0)}；snapshot: {performance_totals.get('snapshot_count', 0)}；技术错误: {performance_totals.get('technical_error_count', 0)}",
        ]
    )
    return "\n".join(lines)


def _daily_final_payload(report: dict[str, Any], *, batch_json_path: Path, batch_markdown_path: Path) -> dict[str, Any]:
    return {
        "kind": "daily_final",
        "report_date": str(report.get("report_date") or ""),
        "latest_batch_id": str(report.get("batch_id") or ""),
        "status": str(report.get("status") or ""),
        "totals": report.get("totals") if isinstance(report.get("totals"), dict) else {},
        "sites": report.get("sites") if isinstance(report.get("sites"), list) else [],
        "new_submitted_jobs": report.get("new_submitted_jobs") if isinstance(report.get("new_submitted_jobs"), list) else [],
        "new_unsubmitted_jobs": (
            report.get("new_unsubmitted_jobs") if isinstance(report.get("new_unsubmitted_jobs"), list) else []
        ),
        "reviewed_jobs": report.get("reviewed_jobs") if isinstance(report.get("reviewed_jobs"), list) else [],
        "application_review_changes": (
            report.get("application_review_changes") if isinstance(report.get("application_review_changes"), list) else []
        ),
        "unmatched_review_records": (
            report.get("unmatched_review_records") if isinstance(report.get("unmatched_review_records"), list) else []
        ),
        "metrics": report.get("metrics") if isinstance(report.get("metrics"), dict) else {},
        "batch_report": {
            "json_path": str(batch_json_path),
            "markdown_path": str(batch_markdown_path),
        },
    }


def _daily_final_markdown(report: dict[str, Any]) -> str:
    totals = report.get("totals") if isinstance(report.get("totals"), dict) else {}
    site_reports = report.get("sites") if isinstance(report.get("sites"), list) else []
    completed_sites = sum(1 for site in site_reports if str(site.get("site_status") or "") == "completed")
    blocked_or_failed_sites = sum(
        1 for site in site_reports if str(site.get("site_status") or "") in {"blocked", "failed", "skipped"}
    )
    lines = [
        "# Final Report",
        "",
        f"- Date: {report.get('report_date') or ''}",
        f"- Latest batch: `{report.get('latest_batch_id')}`",
        f"- Status: {report.get('status') or 'unknown'}",
        f"- Sites planned: {len(site_reports)}",
        f"- Sites completed: {completed_sites}",
        f"- Sites blocked/failed/skipped: {blocked_or_failed_sites}",
        f"- Retrieved jobs: {totals.get('retrieved_count', 0)}",
        f"- New jobs: {totals.get('new_jobs_count', 0)}",
        f"- New submitted: {totals.get('new_submitted_count', 0)}",
        f"- New filtered out: {totals.get('new_filtered_out_count', 0)}",
        f"- Submitted: {totals.get('submitted_count', 0)}",
        f"- Already applied: {totals.get('already_applied_count', 0)}",
        f"- Application reviews: {totals.get('application_reviewed_count', 0)}",
        f"- Application status changes: {totals.get('application_review_changed_count', 0)}",
        f"- Unmatched application reviews: {totals.get('unmatched_review_count', 0)}",
        "",
        "## Sites",
    ]
    if not site_reports:
        lines.extend(["", "- 无"])
    for site in site_reports:
        review = site.get("application_review") if isinstance(site.get("application_review"), dict) else {}
        lines.append(
            f"- {site.get('site_name') or site.get('site_key')}: "
            f"status {site.get('site_status') or '-'}，"
            f"retrieve {site.get('retrieve_status') or '-'}，"
            f"apply {site.get('apply_status') or '-'}，"
            f"检索 {site.get('retrieved_count', 0)}，"
            f"新岗位 {site.get('new_jobs_count', 0)}，"
            f"新投递 {site.get('new_submitted_count', 0)}，"
            f"新过滤 {site.get('new_filtered_out_count', 0)}，"
            f"申请状态检查 {review.get('reviewed_count', 0)}，"
            f"状态变化 {review.get('changed_count', 0)}"
        )

    new_submitted_jobs = report.get("new_submitted_jobs") if isinstance(report.get("new_submitted_jobs"), list) else []
    new_unsubmitted_jobs = (
        report.get("new_unsubmitted_jobs") if isinstance(report.get("new_unsubmitted_jobs"), list) else []
    )
    lines.extend(["", "## 新增岗位", "", "### 投递"])
    _append_new_job_lines(lines, new_submitted_jobs, include_site=True)
    lines.extend(["", "### 没投递"])
    _append_new_job_lines(lines, new_unsubmitted_jobs, include_site=True)

    review_changes = (
        report.get("application_review_changes") if isinstance(report.get("application_review_changes"), list) else []
    )
    lines.extend(["", "## 申请状态变化"])
    _append_review_change_lines(lines, review_changes, include_site=True)

    reviewed_jobs = report.get("reviewed_jobs") if isinstance(report.get("reviewed_jobs"), list) else []
    lines.extend(["", "## 申请状态检查"])
    _append_review_group_lines(lines, reviewed_jobs, include_site=True)
    return "\n".join(lines)


def build_site_report(
    *,
    workspace: Path,
    site_store: SiteStore,
    batch_id: str,
    site_key: str,
    site_row: dict[str, Any],
    report_date: str,
    batch_created_at: str,
) -> dict[str, Any]:
    rows = _unique_run_jobs(site_store.list_run_jobs(site_key, batch_id))
    history_matcher = getattr(site_store, "match_history_rows", None)
    if callable(history_matcher):
        matched_history_rows = history_matcher(site_key, rows)
    else:
        matched_history_rows = [None] * len(rows)

    applied_jobs: list[dict[str, Any]] = []
    previously_applied_jobs: list[dict[str, Any]] = []
    new_submitted_jobs: list[dict[str, Any]] = []
    new_unsubmitted_jobs: list[dict[str, Any]] = []
    submitted_count = 0
    already_applied_count = 0
    new_jobs_count = 0
    new_submitted_count = 0
    new_filtered_out_count = 0

    for idx, row in enumerate(rows):
        history_row = matched_history_rows[idx] if idx < len(matched_history_rows) else None
        application_status = _application_status(row)
        decision_status = _decision_status(row)
        if application_status == "submitted":
            submitted_count += 1
        if application_status == "already_applied":
            already_applied_count += 1
        if application_status in APPLIED_RELATED_STATUSES:
            applied_jobs.append(_report_job_entry(workspace, row, status=application_status))

        is_new = _is_new_history_match(history_row, batch_created_at=batch_created_at)
        is_user_visible_new = is_new and not _has_site_applied_signal(row, history_row, batch_id=batch_id)
        if is_user_visible_new:
            new_jobs_count += 1
            new_entry = _report_job_entry(workspace, row, status=_new_job_status(row))
            if application_status == "submitted":
                new_submitted_count += 1
                new_submitted_jobs.append(new_entry)
            else:
                new_unsubmitted_jobs.append(new_entry)
            if decision_status == "filtered_out":
                new_filtered_out_count += 1

        history_status = _application_status(history_row or {})
        if _was_previously_applied(history_row, batch_created_at=batch_created_at) or application_status == "already_applied":
            source_row = history_row if isinstance(history_row, dict) else row
            previously_applied_jobs.append(
                _report_job_entry(
                    workspace,
                    source_row,
                    status=history_status or application_status or _job_status(source_row),
                    current_status=application_status,
                    history_status=history_status or application_status,
                )
            )

    application_review = _review_summary(
        site_store=site_store,
        workspace=workspace,
        site_key=site_key,
        batch_id=batch_id,
    )
    retrieve = site_row.get("retrieve") if isinstance(site_row.get("retrieve"), dict) else {}
    apply = site_row.get("apply") if isinstance(site_row.get("apply"), dict) else {}
    report = {
        "batch_id": batch_id,
        "report_date": report_date,
        "site_key": site_key,
        "site_name": str(site_row.get("site_name") or site_row.get("canonical_company") or site_key),
        "site_status": str(site_row.get("status") or ""),
        "reason_tag": str(site_row.get("reason_tag") or ""),
        "retrieve_status": str(retrieve.get("status") or ""),
        "apply_status": str(apply.get("status") or ""),
        "apply_attempted": int(apply.get("attempted") or 0),
        "apply_submitted": int(apply.get("submitted") or 0),
        "apply_failed": int(apply.get("failed") or 0),
        "apply_blocked": int(apply.get("blocked") or 0),
        "retrieved_count": len(rows),
        "new_jobs_count": new_jobs_count,
        "new_submitted_count": new_submitted_count,
        "new_filtered_out_count": new_filtered_out_count,
        "submitted_count": submitted_count,
        "already_applied_count": already_applied_count,
        "previously_applied_count": len(previously_applied_jobs),
        "application_review_changed_count": int(application_review.get("changed_count") or 0),
        "application_review": application_review,
        "applied_jobs": applied_jobs,
        "previously_applied_jobs": previously_applied_jobs,
        "new_submitted_jobs": new_submitted_jobs,
        "new_unsubmitted_jobs": new_unsubmitted_jobs,
        "application_review_changes": (
            application_review.get("review_changes") if isinstance(application_review.get("review_changes"), list) else []
        ),
    }
    json_path, md_path = _site_report_paths(workspace, site_key, batch_id, report_date)
    _write_report_artifact(
        workspace=workspace,
        artifact_id=f"career_job_batch:{batch_id}:site:{site_key}",
        report_type="job_site",
        json_path=json_path,
        markdown_path=md_path,
        payload=report,
        markdown=_site_markdown(report),
        metadata={"batch_id": batch_id, "site_key": site_key, "report_date": report_date},
    )
    report["json_path"] = str(json_path)
    report["markdown_path"] = str(md_path)
    return report


def generate_job_batch_report(
    *,
    workspace: Path,
    batch_id: str = "latest",
    project_root: Path | None = None,
) -> dict[str, Any]:
    workspace = Path(workspace)
    job_store = JobStore(workspace)
    if batch_id == "latest":
        batches = job_store.list_batches(include_terminal=True)
        if not batches:
            raise FileNotFoundError("no job batches found")
        batch = batches[0]
        batch_id = str(batch.get("batch_id") or "")
    else:
        batch = job_store.load_batch(batch_id)
    if not batch:
        raise FileNotFoundError(f"job batch not found: {batch_id}")
    batch_id = str(batch.get("batch_id") or batch_id)
    batch_created_at = str(batch.get("created_at") or "")
    report_date = _batch_report_date(batch)
    site_store = SiteStore(workspace, project_root=project_root)
    sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
    site_reports: list[dict[str, Any]] = []
    applied_jobs: list[dict[str, Any]] = []
    previously_applied_jobs: list[dict[str, Any]] = []
    new_submitted_jobs: list[dict[str, Any]] = []
    new_unsubmitted_jobs: list[dict[str, Any]] = []
    totals = {
        "retrieved_count": 0,
        "new_jobs_count": 0,
        "new_submitted_count": 0,
        "new_filtered_out_count": 0,
        "submitted_count": 0,
        "already_applied_count": 0,
        "previously_applied_count": 0,
        "application_reviewed_count": 0,
        "application_review_changed_count": 0,
        "unmatched_review_count": 0,
    }
    for site_key in sorted(sites.keys()):
        site_row = sites.get(site_key)
        if not isinstance(site_row, dict):
            continue
        site_report = build_site_report(
            workspace=workspace,
            site_store=site_store,
            batch_id=batch_id,
            site_key=site_key,
            site_row=site_row,
            report_date=report_date,
            batch_created_at=batch_created_at,
        )
        site_reports.append(site_report)
        for key in totals:
            if key in site_report:
                totals[key] += int(site_report.get(key) or 0)
        application_review = site_report.get("application_review") if isinstance(site_report.get("application_review"), dict) else {}
        totals["application_reviewed_count"] += int(application_review.get("reviewed_count") or 0)
        totals["unmatched_review_count"] += int(application_review.get("unmatched_review_count") or 0)
        for job in site_report.get("applied_jobs") or []:
            if not isinstance(job, dict):
                continue
            applied_jobs.append({"site_key": site_key, "site_name": str(site_report.get("site_name") or site_key), **job})
        for job in site_report.get("previously_applied_jobs") or []:
            if not isinstance(job, dict):
                continue
            previously_applied_jobs.append(
                {"site_key": site_key, "site_name": str(site_report.get("site_name") or site_key), **job}
            )
        for job in site_report.get("new_submitted_jobs") or []:
            if not isinstance(job, dict):
                continue
            new_submitted_jobs.append({"site_key": site_key, "site_name": str(site_report.get("site_name") or site_key), **job})
        for job in site_report.get("new_unsubmitted_jobs") or []:
            if not isinstance(job, dict):
                continue
            new_unsubmitted_jobs.append(
                {"site_key": site_key, "site_name": str(site_report.get("site_name") or site_key), **job}
            )
    reviewed_jobs: list[dict[str, Any]] = []
    application_review_changes: list[dict[str, Any]] = []
    unmatched_review_records: list[dict[str, Any]] = []
    for site_report in site_reports:
        site_key = str(site_report.get("site_key") or "")
        site_name = str(site_report.get("site_name") or site_key)
        application_review = site_report.get("application_review") if isinstance(site_report.get("application_review"), dict) else {}
        for job in application_review.get("reviewed_jobs") or []:
            if isinstance(job, dict):
                reviewed_jobs.append({"site_key": site_key, "site_name": site_name, **job})
        for job in application_review.get("review_changes") or []:
            if isinstance(job, dict):
                application_review_changes.append({"site_key": site_key, "site_name": site_name, **job})
        for row in application_review.get("unmatched_review_records") or []:
            if isinstance(row, dict):
                unmatched_review_records.append({"site_key": site_key, "site_name": site_name, **row})

    report = {
        "batch_id": batch_id,
        "report_date": report_date,
        "status": str(batch.get("status") or ""),
        "totals": totals,
        "sites": site_reports,
        "applied_jobs": applied_jobs,
        "previously_applied_jobs": previously_applied_jobs,
        "new_submitted_jobs": new_submitted_jobs,
        "new_unsubmitted_jobs": new_unsubmitted_jobs,
        "reviewed_jobs": reviewed_jobs,
        "application_review_changes": application_review_changes,
        "unmatched_review_records": unmatched_review_records,
        "metrics": build_metrics_summary(workspace=workspace, batch_id=batch_id),
    }
    json_path, md_path = _batch_report_paths(workspace, batch_id, report_date)
    _write_report_artifact(
        workspace=workspace,
        artifact_id=f"career_job_batch:{batch_id}",
        report_type="job_batch",
        json_path=json_path,
        markdown_path=md_path,
        payload=report,
        markdown=_batch_markdown(report),
        metadata={"batch_id": batch_id, "report_date": report_date},
    )
    final_payload = _daily_final_payload(report, batch_json_path=json_path, batch_markdown_path=md_path)
    final_json_path, final_md_path = _daily_final_report_paths(workspace, report_date)
    _write_report_artifact(
        workspace=workspace,
        artifact_id=f"career_job_daily:{report_date}",
        report_type="job_daily",
        json_path=final_json_path,
        markdown_path=final_md_path,
        payload=final_payload,
        markdown=_daily_final_markdown(final_payload),
        metadata={"batch_id": batch_id, "report_date": report_date},
    )
    report["json_path"] = str(json_path)
    report["markdown_path"] = str(md_path)
    report["final_json_path"] = str(final_json_path)
    report["final_markdown_path"] = str(final_md_path)
    return report
