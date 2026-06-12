"""Structured policy helpers for job skill front matter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.skill_schema.schema import context_hash
from careereng.utils import parse_front_matter, safe_file_stem


DEFAULT_RETRIEVAL_POLICY: dict[str, Any] = {
    "preferred_sort": "newest_first",
    "posted_window_days": 30,
    "posted_window_comparison": "strictly_less_than",
    "date_window_stop_enabled": True,
    "history_fast_stop_enabled": True,
    "unknown_posted_age": "review",
}

DEFAULT_APPLY_CANDIDATE_POLICY: dict[str, Any] = {
    "posted_window_days": 30,
    "posted_window_comparison": "strictly_less_than",
    "unknown_posted_age": "review",
}


def read_skill_front_matter(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    front_matter, _body = parse_front_matter(text)
    return front_matter if isinstance(front_matter, dict) else {}


def _merge_policy(defaults: dict[str, Any], project_meta: dict[str, Any], site_meta: dict[str, Any], key: str) -> dict[str, Any]:
    policy = dict(defaults)
    project_policy = project_meta.get(key)
    site_policy = site_meta.get(key)
    if isinstance(project_policy, dict):
        policy.update({str(k): v for k, v in project_policy.items()})
    if isinstance(site_policy, dict):
        policy.update({str(k): v for k, v in site_policy.items()})
    return normalize_posted_window_policy(policy)


def normalize_posted_window_policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(policy or {})
    try:
        days = int(payload.get("posted_window_days") or 0)
    except Exception:
        days = 0
    payload["posted_window_days"] = max(0, days)

    comparison = str(payload.get("posted_window_comparison") or "strictly_less_than").strip().lower()
    if comparison not in {"strictly_less_than", "less_than_or_equal"}:
        comparison = "strictly_less_than"
    payload["posted_window_comparison"] = comparison

    unknown = str(payload.get("unknown_posted_age") or "review").strip().lower()
    if unknown not in {"review", "filtered_out"}:
        unknown = "review"
    payload["unknown_posted_age"] = unknown
    return payload


def load_job_skill_policies(project_root: Path, site_key: str) -> dict[str, dict[str, Any]]:
    project_root = Path(project_root)
    project_meta = read_skill_front_matter(project_root / "skills" / "search" / "jobs" / "SKILL.md")
    site_meta = read_skill_front_matter(
        project_root / "skills" / "search" / "jobs" / "sites" / safe_file_stem(site_key) / "SKILL.md"
    )
    retrieval_policy = _merge_policy(DEFAULT_RETRIEVAL_POLICY, project_meta, site_meta, "retrieval_policy")
    apply_candidate_policy = _merge_policy(
        DEFAULT_APPLY_CANDIDATE_POLICY,
        project_meta,
        site_meta,
        "apply_candidate_policy",
    )
    return {
        "retrieval_policy": retrieval_policy,
        "apply_candidate_policy": apply_candidate_policy,
    }


def policy_hash(policy: dict[str, Any] | None) -> str:
    return context_hash(dict(policy or {}))
