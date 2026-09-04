"""Shared apply-probe semantics for site apply workflow evolution."""

from __future__ import annotations

import re
from typing import Any

from careereng.career.applications.ranked_queue import ranked_state_counts


EXCLUDED_ROLE_RE = re.compile(
    r"\b(intern|internship|campus|student|new[\s-]*grad|new[\s-]*graduate|co[\s-]*op|coop)\b|校招|实习",
    flags=re.I,
)

FORM_WORKFLOW_RE = re.compile(
    r"\b("
    r"form|field|validation|validate|required|profile information|resumeparsedinfo|self-disclosure|"
    r"self disclosure|resume|upload|submit resume|submitted|submit|continue|state|province|"
    r"city|town|zip|postal|address|gender|disability|veteran|authorization|visa"
    r")\b",
    flags=re.I,
)

HUMAN_ONLY_RE = re.compile(
    r"\b(password|mfa|captcha|verification code|verify|passkey|device approval|human-only|human only)\b",
    flags=re.I,
)


def is_excluded_role(row: dict[str, Any] | None) -> bool:
    if not isinstance(row, dict):
        return False
    haystack = "\n".join(
        str(row.get(key) or "")
        for key in (
            "title",
            "card_text",
            "description",
            "jd_summary",
            "fit_reason",
            "last_apply_error",
            "apply_error",
        )
    )
    return bool(EXCLUDED_ROLE_RE.search(haystack))


def is_form_workflow_sample(row: dict[str, Any] | None) -> bool:
    if not isinstance(row, dict):
        return False
    application_status = _normalized(row.get("application_status"))
    if application_status == "submitted":
        return True
    if application_status not in {"apply_failed", "blocked"}:
        return False
    evidence = _sample_evidence(row)
    if HUMAN_ONLY_RE.search(evidence):
        return False
    if application_status == "apply_failed":
        return bool(FORM_WORKFLOW_RE.search(evidence))
    apply_state = _normalized(row.get("apply_state"))
    if apply_state in {"blocked_form_validation", "blocked_missing_fact", "missing_profile_fact"}:
        return True
    return bool(FORM_WORKFLOW_RE.search(evidence))


def is_form_workflow_success(row: dict[str, Any] | None) -> bool:
    if not isinstance(row, dict):
        return False
    return _normalized(row.get("application_status")) == "submitted"


def is_form_workflow_unsuccessful(row: dict[str, Any] | None) -> bool:
    if not is_form_workflow_sample(row):
        return False
    return not is_form_workflow_success(row)


def is_excluded_role_violation(row: dict[str, Any] | None) -> bool:
    if not isinstance(row, dict) or not is_excluded_role(row):
        return False
    decision_status = _normalized(row.get("decision_status"))
    application_status = _normalized(row.get("application_status"))
    if decision_status == "filtered_out" or application_status == "filtered_out":
        return False
    return is_form_workflow_sample(row) or application_status in {"submitted", "apply_failed", "blocked"}


def excluded_role_violations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not is_excluded_role_violation(row):
            continue
        violations.append(
            {
                "job_id": str(row.get("job_id") or ""),
                "site_job_id": str(row.get("site_job_id") or ""),
                "title": str(row.get("title") or ""),
                "url": str(row.get("url") or ""),
                "application_status": str(row.get("application_status") or ""),
                "apply_state": str(row.get("apply_state") or ""),
            }
        )
    return violations


def apply_probe_counters(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "retrieved": len([row for row in rows if isinstance(row, dict)]),
        "attempted": 0,
        "form_sampled": 0,
        "form_successful": 0,
        "form_unsuccessful": 0,
        "apply_path_attempted": 0,
        "submitted": 0,
        "already_applied": 0,
        "filtered_out": 0,
        "failed": 0,
        "blocked": 0,
        "excluded_role_violations": 0,
    }
    for row in rows:
        if not isinstance(row, dict):
            continue
        decision_status = _normalized(row.get("decision_status"))
        application_status = _normalized(row.get("application_status"))
        if decision_status == "filtered_out" or application_status == "filtered_out":
            counts["filtered_out"] += 1
        if application_status == "already_applied":
            counts["already_applied"] += 1
        if application_status in {"submitted", "apply_failed", "blocked", "already_applied"}:
            counts["apply_path_attempted"] += 1
        if application_status == "submitted":
            counts["submitted"] += 1
        elif application_status == "apply_failed":
            counts["failed"] += 1
        elif application_status == "blocked":
            counts["blocked"] += 1
        if is_form_workflow_sample(row):
            counts["form_sampled"] += 1
            counts["attempted"] += 1
        if is_form_workflow_success(row):
            counts["form_successful"] += 1
        if is_form_workflow_unsuccessful(row):
            counts["form_unsuccessful"] += 1
        if is_excluded_role_violation(row):
            counts["excluded_role_violations"] += 1
    counts.update(ranked_state_counts(rows))
    return counts


def _sample_evidence(row: dict[str, Any]) -> str:
    return "\n".join(
        str(row.get(key) or "")
        for key in (
            "application_status",
            "apply_state",
            "last_apply_error",
            "apply_error",
            "confirmation_text",
            "fit_reason",
            "card_text",
        )
    )


def _normalized(value: Any) -> str:
    return str(value or "").strip().lower()
