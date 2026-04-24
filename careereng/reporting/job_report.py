"""Simple job batch reports."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from careereng.storage.job_store import JobStore
from careereng.storage.site_store import SiteStore
from careereng.utils import ensure_dir, safe_file_stem, today_str


APPLIED_RELATED_STATUSES = {"submitted", "already_applied"}


def _collapse_text(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
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


def _job_status(row: dict[str, Any]) -> str:
    for field in ("application_status", "decision_status", "apply_state"):
        value = str(row.get(field) or "").strip().lower()
        if value:
            return value
    return ""


def _description_summary(workspace: Path, row: dict[str, Any], *, max_chars: int = 320) -> str:
    ref = str(row.get("description_ref") or "").strip()
    if not ref:
        return ""
    path = Path(ref)
    if not path.is_absolute():
        path = workspace / path
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    text = _collapse_text(text)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_markdown(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _site_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {report.get('site_name') or report.get('site_key')} Job Report",
        "",
        f"- Batch: `{report.get('batch_id')}`",
        f"- 检索岗位: {report.get('retrieved_count', 0)}",
        f"- 本次投递: {report.get('submitted_count', 0)}",
        f"- 已投递: {report.get('already_applied_count', 0)}",
        "",
        "## 投递相关岗位",
    ]
    applied_jobs = report.get("applied_jobs") if isinstance(report.get("applied_jobs"), list) else []
    if not applied_jobs:
        lines.append("")
        lines.append("- 无")
        return "\n".join(lines)
    for job in applied_jobs:
        title = str(job.get("title") or "Untitled")
        location = str(job.get("location") or "-")
        posted = str(job.get("posted") or "-")
        status = str(job.get("status") or "-")
        url = str(job.get("url") or "")
        summary = str(job.get("jd_summary") or "")
        lines.extend(
            [
                "",
                f"### {title}",
                f"- Status: {status}",
                f"- Location: {location}",
                f"- Posted: {posted}",
            ]
        )
        if url:
            lines.append(f"- URL: {url}")
        if summary:
            lines.append(f"- JD 摘要: {summary}")
    return "\n".join(lines)


def _batch_markdown(report: dict[str, Any]) -> str:
    totals = report.get("totals") if isinstance(report.get("totals"), dict) else {}
    lines = [
        "# Job Batch Report",
        "",
        f"- Batch: `{report.get('batch_id')}`",
        f"- 检索岗位: {totals.get('retrieved_count', 0)}",
        f"- 本次投递: {totals.get('submitted_count', 0)}",
        f"- 已投递: {totals.get('already_applied_count', 0)}",
        "",
        "## Sites",
    ]
    site_reports = report.get("sites") if isinstance(report.get("sites"), list) else []
    if not site_reports:
        lines.append("")
        lines.append("- 无")
    for site in site_reports:
        lines.append(
            f"- {site.get('site_name') or site.get('site_key')}: "
            f"检索 {site.get('retrieved_count', 0)}，"
            f"本次投递 {site.get('submitted_count', 0)}，"
            f"已投递 {site.get('already_applied_count', 0)}"
        )
    applied_jobs = report.get("applied_jobs") if isinstance(report.get("applied_jobs"), list) else []
    lines.extend(["", "## 投递相关岗位"])
    if not applied_jobs:
        lines.append("")
        lines.append("- 无")
        return "\n".join(lines)
    for job in applied_jobs:
        title = str(job.get("title") or "Untitled")
        site_name = str(job.get("site_name") or job.get("site_key") or "site")
        location = str(job.get("location") or "-")
        posted = str(job.get("posted") or "-")
        status = str(job.get("status") or "-")
        summary = str(job.get("jd_summary") or "")
        lines.extend(
            [
                "",
                f"### {title}",
                f"- Site: {site_name}",
                f"- Status: {status}",
                f"- Location: {location}",
                f"- Posted: {posted}",
            ]
        )
        url = str(job.get("url") or "")
        if url:
            lines.append(f"- URL: {url}")
        if summary:
            lines.append(f"- JD 摘要: {summary}")
    return "\n".join(lines)


def build_site_report(
    *,
    workspace: Path,
    site_store: SiteStore,
    batch_id: str,
    site_key: str,
    site_row: dict[str, Any],
    report_date: str,
) -> dict[str, Any]:
    rows = _unique_run_jobs(site_store.list_run_jobs(site_key, batch_id))
    applied_jobs: list[dict[str, Any]] = []
    submitted_count = 0
    already_applied_count = 0
    for row in rows:
        status = _job_status(row)
        if status == "submitted":
            submitted_count += 1
        if status == "already_applied":
            already_applied_count += 1
        if status not in APPLIED_RELATED_STATUSES:
            continue
        applied_jobs.append(
            {
                "job_id": str(row.get("job_id") or ""),
                "title": str(row.get("title") or ""),
                "url": str(row.get("url") or ""),
                "location": str(row.get("location") or ""),
                "posted": str(row.get("posted_label") or row.get("posted_at") or ""),
                "status": status,
                "jd_summary": _description_summary(workspace, row),
            }
        )

    report = {
        "batch_id": batch_id,
        "report_date": report_date,
        "site_key": site_key,
        "site_name": str(site_row.get("site_name") or site_row.get("canonical_company") or site_key),
        "retrieved_count": len(rows),
        "submitted_count": submitted_count,
        "already_applied_count": already_applied_count,
        "applied_jobs": applied_jobs,
    }
    json_path, md_path = _site_report_paths(workspace, site_key, batch_id, report_date)
    _write_json(json_path, report)
    _write_markdown(md_path, _site_markdown(report))
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
    report_date = _batch_report_date(batch)
    site_store = SiteStore(workspace, project_root=project_root)
    sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
    site_reports: list[dict[str, Any]] = []
    applied_jobs: list[dict[str, Any]] = []
    totals = {
        "retrieved_count": 0,
        "submitted_count": 0,
        "already_applied_count": 0,
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
        )
        site_reports.append(site_report)
        for key in totals:
            totals[key] += int(site_report.get(key) or 0)
        for job in site_report.get("applied_jobs") or []:
            if not isinstance(job, dict):
                continue
            applied_jobs.append(
                {
                    "site_key": site_key,
                    "site_name": str(site_report.get("site_name") or site_key),
                    **job,
                }
            )

    report = {
        "batch_id": batch_id,
        "report_date": report_date,
        "status": str(batch.get("status") or ""),
        "totals": totals,
        "sites": site_reports,
        "applied_jobs": applied_jobs,
    }
    json_path, md_path = _batch_report_paths(workspace, batch_id, report_date)
    _write_json(json_path, report)
    _write_markdown(md_path, _batch_markdown(report))
    report["json_path"] = str(json_path)
    report["markdown_path"] = str(md_path)
    return report
