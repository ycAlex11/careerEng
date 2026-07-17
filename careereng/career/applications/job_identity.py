"""Shared job identity helpers for run rows, history rows, and apply plans."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from careereng.utils import safe_file_stem


WORKDAY_JOB_NUMBER_RE = re.compile(r"(?<![A-Z0-9])JR\d{3,}\b", flags=re.IGNORECASE)
YEAR_PREFIXED_JOB_ID_RE = re.compile(r"\b(?:19|20)\d{2}[-_](\d{3,})\b", flags=re.IGNORECASE)
SITE_JOB_ID_LIKE_RE = re.compile(r"(?:\d{3,}|(?:19|20)\d{2}[-_]\d{3,}|[a-z]{1,12}[-_]?\d{3,})")
UUID_JOB_ID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    flags=re.IGNORECASE,
)
DEFAULT_FRAGMENT_JOB_ROUTE_PATTERNS = (
    "#/job/{site_job_id}",
    "#/jobs/{site_job_id}",
)


def normalize_job_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def normalize_identity_url(url: object, identity_policy: dict[str, Any] | None = None) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except Exception:
        return raw
    if not parsed.scheme or not parsed.netloc:
        return raw
    kept_qs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=False)
        if not key.lower().startswith("utm_")
    ]
    fragment = parsed.fragment if extract_site_job_id_from_fragment(parsed.fragment, identity_policy) else ""
    return urlunparse(parsed._replace(query=urlencode(sorted(kept_qs)), fragment=fragment))


def site_job_id_aliases(value: object) -> list[str]:
    text = normalize_job_text(value)
    if not text:
        return []
    aliases = [text]
    for match in YEAR_PREFIXED_JOB_ID_RE.finditer(text):
        suffix = normalize_job_text(match.group(1))
        if suffix and suffix not in aliases:
            aliases.append(suffix)
    return aliases


def is_site_job_id_like(value: object) -> bool:
    text = normalize_job_text(value)
    return bool(text and (SITE_JOB_ID_LIKE_RE.fullmatch(text) or UUID_JOB_ID_RE.fullmatch(text)))


def infer_site_job_id_from_url(url: object, identity_policy: dict[str, Any] | None = None) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        raw_parsed = urlparse(raw)
    except Exception:
        raw_parsed = None
    if raw_parsed is not None:
        fragment_candidate = extract_site_job_id_from_fragment(raw_parsed.fragment, identity_policy)
        if fragment_candidate:
            return fragment_candidate

    normalized = normalize_identity_url(raw, identity_policy)
    if not normalized:
        return ""
    try:
        parsed = urlparse(normalized)
    except Exception:
        return ""

    query = {key.lower(): value for key, value in parse_qsl(parsed.query, keep_blank_values=False)}
    for field in ("pid", "jobid", "job_id", "jobnumber", "job_number", "requisitionid", "reqid"):
        candidate = str(query.get(field, "")).strip()
        if is_site_job_id_like(candidate):
            return candidate

    path_parts = [part for part in parsed.path.split("/") if part]
    for index, part in enumerate(path_parts[:-1]):
        if part.lower() not in {"job", "jobs"}:
            continue
        candidate = path_parts[index + 1].strip()
        if is_site_job_id_like(candidate):
            return candidate
    if path_parts and is_site_job_id_like(path_parts[-1]):
        return path_parts[-1]

    # Workday often embeds the requisition in a slug, e.g. `Title_JR2018990`.
    match = WORKDAY_JOB_NUMBER_RE.search(normalized)
    return match.group(0).upper() if match else ""


def extract_site_job_id_from_fragment(fragment: object, identity_policy: dict[str, Any] | None = None) -> str:
    text = str(fragment or "").strip()
    if not text:
        return ""

    values = [text]
    if not text.startswith("#"):
        values.append(f"#{text}")

    for pattern in _fragment_job_route_patterns(identity_policy):
        candidate = _extract_fragment_pattern_candidate(values, pattern)
        if is_site_job_id_like(candidate):
            return candidate

    # Common hash-routed ATS shape, for example Moka: `#/job/<uuid>`.
    match = re.search(r"(?:^|/)(?:job|jobs)/([^/?#&]+)", text, flags=re.IGNORECASE)
    candidate = match.group(1).strip() if match else ""
    return candidate if is_site_job_id_like(candidate) else ""


def canonical_job_identity_keys(site_key: str, row: dict[str, Any], identity_policy: dict[str, Any] | None = None) -> list[str]:
    if not isinstance(row, dict):
        return []
    site = safe_file_stem(site_key or str(row.get("site_id") or "site"))
    keys: list[str] = []
    seen: set[str] = set()

    def add(key: str) -> None:
        if key and key not in seen:
            seen.add(key)
            keys.append(key)

    explicit_site_job_id = str(row.get("site_job_id") or row.get("source_job_id") or "").strip()
    site_job_id = explicit_site_job_id or infer_site_job_id_from_url(row.get("url"), identity_policy)
    for alias in site_job_id_aliases(site_job_id):
        add(f"site_job_id|{site}|{alias}")

    for field in ("canonical_job_id", "job_id"):
        value = normalize_job_text(row.get(field))
        if value:
            add(f"{field}|{site}|{value}")

    url = normalize_identity_url(row.get("url"), identity_policy)
    if url:
        add(f"url|{site}|{url}")

    title = normalize_job_text(row.get("title"))
    location = normalize_job_text(row.get("location"))
    posted = normalize_job_text(row.get("posted_label") or row.get("posted_at"))
    if title and location and posted:
        add(f"fallback|{site}|{title}|{location}|{posted}")
    return keys


def primary_job_identity_key(site_key: str, row: dict[str, Any], identity_policy: dict[str, Any] | None = None) -> str:
    keys = canonical_job_identity_keys(site_key, row, identity_policy)
    return keys[0] if keys else ""


def _fragment_job_route_patterns(identity_policy: dict[str, Any] | None = None) -> list[str]:
    patterns: list[str] = []
    if isinstance(identity_policy, dict):
        raw = identity_policy.get("fragment_job_route_patterns")
        if isinstance(raw, str):
            patterns.append(raw)
        elif isinstance(raw, list):
            patterns.extend(str(item) for item in raw if str(item).strip())
    patterns.extend(DEFAULT_FRAGMENT_JOB_ROUTE_PATTERNS)
    deduped: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        value = str(pattern or "").strip()
        if value and "{site_job_id}" in value and value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _extract_fragment_pattern_candidate(values: list[str], pattern: str) -> str:
    marker = "{site_job_id}"
    if marker not in pattern:
        return ""
    escaped = re.escape(pattern).replace(re.escape(marker), r"(?P<site_job_id>[^/?#&]+)")
    regex = re.compile(rf"^{escaped}(?:[/?#&].*)?$", flags=re.IGNORECASE)
    for value in values:
        match = regex.match(value)
        if match:
            return str(match.group("site_job_id") or "").strip()
    return ""
