"""Local candidate matching for interview-session binding."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from careereng.interviews.store import InterviewStore
from careereng.utils import read_json


INTERVIEW_STAGE_WEIGHTS = {
    "interview": 45,
    "assessment": 38,
    "in_process": 35,
    "resume_review": 15,
    "received": 8,
}
COMPANY_ALIASES = {
    "英伟达": "nvidia",
    "微软": "microsoft",
    "高通": "qualcomm",
    "超威": "amd",
}


def find_interview_candidates(
    *,
    workspace: Path | str,
    company: str = "",
    title: str = "",
    limit: int = 10,
) -> list[dict[str, Any]]:
    workspace_path = Path(workspace)
    query = {"company": str(company or "").strip(), "title": str(title or "").strip()}
    rows = [
        *_history_candidates(workspace_path),
        *_application_summary_candidates(workspace_path),
        *_existing_session_candidates(workspace_path),
    ]
    deduped = _dedupe_candidates(rows)
    scored: list[dict[str, Any]] = []
    has_query = bool(query["company"] or query["title"])
    for row in deduped:
        score, reasons = _score_candidate(row, query)
        if has_query and score <= 0:
            continue
        scored.append({**row, "match_score": score, "match_reason": "; ".join(reasons) or "available local candidate"})
    scored.sort(
        key=lambda item: (
            int(item.get("match_score") or 0),
            _status_priority(item),
            str(item.get("last_seen_at") or item.get("updated_at") or ""),
        ),
        reverse=True,
    )
    if limit > 0:
        scored = scored[: int(limit)]
    return scored


def save_interview_candidates(
    *,
    workspace: Path | str,
    company: str = "",
    title: str = "",
    limit: int = 10,
) -> list[dict[str, Any]]:
    candidates = find_interview_candidates(workspace=workspace, company=company, title=title, limit=limit)
    return InterviewStore(workspace).save_candidates(query={"company": company, "title": title}, candidates=candidates)


def _history_candidates(workspace: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sites_dir = workspace / "sites"
    if not sites_dir.exists():
        return rows
    for path in sorted(sites_dir.glob("*/jobs/history_jobs.json")):
        site_key = path.parents[1].name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = []
        if not isinstance(data, list):
            continue
        for row in data:
            if isinstance(row, dict):
                rows.append(_candidate_from_job(row, site_key=site_key, source_type="history_job"))
    return rows


def _application_summary_candidates(workspace: Path) -> list[dict[str, Any]]:
    summary = read_json(workspace / "application_summary" / "application_summary.json")
    active_pipeline = summary.get("active_pipeline") if isinstance(summary.get("active_pipeline"), dict) else {}
    rows: list[dict[str, Any]] = []
    for stage, items in active_pipeline.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            row = _candidate_from_job(item, site_key=str(item.get("site_key") or ""), source_type="application_summary")
            row["application_stage"] = str(item.get("stage") or stage or "").strip()
            row["application_status"] = str(item.get("application_review_status") or "").strip()
            row["application_review_status_raw"] = str(item.get("status_raw") or "").strip()
            row["last_seen_at"] = str(item.get("checked_at") or "").strip()
            rows.append(row)
    return rows


def _existing_session_candidates(workspace: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for session in InterviewStore(workspace).list_sessions(status="all", limit=0):
        rows.append(
            {
                "candidate_id": _candidate_id("session", session),
                "source_type": "interview_session",
                "existing_session_id": session.get("session_id") or "",
                "company": session.get("company") or "",
                "employer": session.get("company") or "",
                "title": session.get("title") or "",
                "site_key": session.get("site_key") or "",
                "site_job_id": session.get("site_job_id") or "",
                "canonical_job_id": session.get("canonical_job_id") or "",
                "job_id": "",
                "url": session.get("url") or "",
                "application_status": session.get("application_status") or "",
                "application_stage": session.get("application_stage") or "",
                "application_review_status_raw": "",
                "source_history_ref": session.get("source_history_ref") or "",
                "updated_at": session.get("updated_at") or session.get("created_at") or "",
            }
        )
    return rows


def _candidate_from_job(row: dict[str, Any], *, site_key: str, source_type: str) -> dict[str, Any]:
    normalized_site = str(row.get("site_id") or row.get("site_key") or site_key or "").strip()
    candidate = {
        "source_type": source_type,
        "existing_session_id": "",
        "company": str(row.get("employer") or row.get("company") or normalized_site).strip(),
        "employer": str(row.get("employer") or row.get("company") or normalized_site).strip(),
        "title": str(row.get("title") or "").strip(),
        "site_key": normalized_site,
        "site_job_id": str(row.get("site_job_id") or row.get("source_job_id") or "").strip(),
        "canonical_job_id": str(row.get("canonical_job_id") or "").strip(),
        "job_id": str(row.get("job_id") or "").strip(),
        "url": str(row.get("url") or row.get("application_review_url") or "").strip(),
        "application_status": str(row.get("application_status") or row.get("application_review_status") or "").strip(),
        "application_stage": str(row.get("application_review_stage") or row.get("stage") or "").strip(),
        "application_review_status_raw": str(row.get("application_review_status_raw") or row.get("status_raw") or "").strip(),
        "source_history_ref": _source_history_ref(normalized_site, row),
        "last_seen_at": str(row.get("last_seen_at") or row.get("application_review_checked_at") or row.get("ts") or "").strip(),
    }
    candidate["candidate_id"] = _candidate_id("job", candidate)
    return candidate


def _source_history_ref(site_key: str, row: dict[str, Any]) -> str:
    for key in ("job_id", "canonical_job_id", "site_job_id"):
        value = str(row.get(key) or "").strip()
        if value:
            return f"{site_key}:{key}:{value}"
    title = str(row.get("title") or "").strip()
    return f"{site_key}:title:{title}" if title else site_key


def _candidate_id(prefix: str, row: dict[str, Any]) -> str:
    source = "|".join(
        str(row.get(key) or "").strip().lower()
        for key in ("site_key", "canonical_job_id", "site_job_id", "url", "title", "existing_session_id")
    )
    return f"interview_candidate_{hashlib.sha1((prefix + '|' + source).encode('utf-8')).hexdigest()[:12]}"


def _dedupe_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("site_key") or "").lower(),
            str(row.get("canonical_job_id") or "").lower(),
            str(row.get("site_job_id") or "").lower(),
            str(row.get("url") or "").lower() or str(row.get("title") or "").lower(),
        )
        current = grouped.get(key)
        if current is None or _source_rank(row) > _source_rank(current):
            grouped[key] = row
    return list(grouped.values())


def _score_candidate(row: dict[str, Any], query: dict[str, str]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    company = _canonical_company(query.get("company") or "")
    title = str(query.get("title") or "").strip().lower()
    candidate_company_values = {
        _canonical_company(str(row.get("company") or "")),
        _canonical_company(str(row.get("employer") or "")),
        _canonical_company(str(row.get("site_key") or "")),
    }
    if company:
        if company in candidate_company_values:
            score += 55
            reasons.append("company/site matched")
        elif any(company in value or value in company for value in candidate_company_values if value):
            score += 30
            reasons.append("company/site partially matched")
        else:
            score -= 25
    if title:
        title_score = _title_score(title, str(row.get("title") or ""))
        if title_score:
            score += title_score
            reasons.append("title matched")
        else:
            score -= 8
    stage = str(row.get("application_stage") or "").strip().lower()
    raw = str(row.get("application_review_status_raw") or "").strip().lower()
    status = str(row.get("application_status") or "").strip().lower()
    if stage in INTERVIEW_STAGE_WEIGHTS:
        score += INTERVIEW_STAGE_WEIGHTS[stage]
        reasons.append(f"stage={stage}")
    if "interview" in raw:
        score += 35
        reasons.append("raw status mentions interview")
    elif "assessment" in raw:
        score += 30
        reasons.append("raw status mentions assessment")
    elif "in process" in raw or "in_process" in raw:
        score += 28
        reasons.append("raw status mentions in process")
    elif "review" in raw:
        score += 18
        reasons.append("raw status mentions review")
    if status in {"active", "already_applied", "submitted"}:
        score += 8
    if status in {"rejected", "declined", "withdrawn"} or "declined" in raw:
        score -= 25
        reasons.append("terminal status reduced score")
    if str(row.get("source_type") or "") == "interview_session":
        score += 20
        reasons.append("existing interview session")
    return score, reasons


def _title_score(query_title: str, candidate_title: str) -> int:
    candidate_lower = candidate_title.lower()
    if query_title and query_title in candidate_lower:
        return 45
    query_tokens = _tokens(query_title)
    candidate_tokens = set(_tokens(candidate_lower))
    if not query_tokens:
        return 0
    hits = [token for token in query_tokens if token in candidate_tokens or token in candidate_lower]
    if not hits:
        return 0
    return min(45, 12 * len(hits))


def _tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", value.lower()) if len(token) > 1]


def _canonical_company(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return COMPANY_ALIASES.get(normalized, normalized)


def _source_rank(row: dict[str, Any]) -> int:
    return {"interview_session": 4, "history_job": 3, "application_summary": 2}.get(str(row.get("source_type") or ""), 1)


def _status_priority(row: dict[str, Any]) -> int:
    stage = str(row.get("application_stage") or "").strip().lower()
    return INTERVIEW_STAGE_WEIGHTS.get(stage, 0)
