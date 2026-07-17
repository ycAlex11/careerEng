"""Helpers for normalizing job posting dates and relative posted labels."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any


_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_US_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
_MONTH_DATE_RE = re.compile(
    r"\b("
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,\s*(\d{4}))?\b",
    flags=re.IGNORECASE,
)
_RELATIVE_DAYS_RE = re.compile(r"\b(\d{1,4})\s*(\+)?\s*days?\b", flags=re.IGNORECASE)
_RELATIVE_HOURS_RE = re.compile(r"\b(\d{1,4})\s*(\+)?\s*hours?\b", flags=re.IGNORECASE)

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _coerce_observed_date(value: Any | None = None) -> date:
    text = str(value or "").strip()
    if text:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except Exception:
            parsed = parse_posted_date(text)
            if parsed is not None:
                return parsed
    return datetime.now().date()


def _parse_year(value: str | None, *, observed: date) -> int:
    if not value:
        return observed.year
    year = int(value)
    return year + 2000 if year < 100 else year


def parse_posted_date(value: Any, *, observed_at: Any | None = None) -> date | None:
    """Parse an absolute date from common careers-site date labels."""

    text = str(value or "").strip()
    if not text:
        return None
    observed = _coerce_observed_date(observed_at)

    match = _ISO_DATE_RE.search(text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except Exception:
            return None

    match = _US_DATE_RE.search(text)
    if match:
        try:
            return date(_parse_year(match.group(3), observed=observed), int(match.group(1)), int(match.group(2)))
        except Exception:
            return None

    match = _MONTH_DATE_RE.search(text)
    if match:
        month = _MONTHS.get(match.group(1).lower())
        if not month:
            return None
        try:
            year = _parse_year(match.group(3), observed=observed)
            parsed = date(year, month, int(match.group(2)))
        except Exception:
            return None
        if not match.group(3) and parsed > observed:
            parsed = date(parsed.year - 1, parsed.month, parsed.day)
        return parsed

    return None


def parse_relative_posted_age(value: Any) -> dict[str, Any]:
    """Parse labels such as `Posted 13 Days Ago` or `Posted 30+ Days Ago`."""

    text = str(value or "").strip().lower()
    if not text:
        return {"days": None, "is_lower_bound": False}
    if "today" in text or "just posted" in text or "hour ago" in text or "hours ago" in text:
        match = _RELATIVE_HOURS_RE.search(text)
        return {"days": 0, "is_lower_bound": bool(match and match.group(2))}
    if "yesterday" in text or "a day ago" in text or "one day ago" in text:
        return {"days": 1, "is_lower_bound": False}
    if "a month ago" in text or "one month ago" in text:
        return {"days": 30, "is_lower_bound": False}

    match = _RELATIVE_DAYS_RE.search(text)
    if not match:
        return {"days": None, "is_lower_bound": False}
    try:
        return {"days": int(match.group(1)), "is_lower_bound": bool(match.group(2))}
    except Exception:
        return {"days": None, "is_lower_bound": False}


def normalize_posted_fields(row: dict[str, Any], *, observed_at: Any | None = None) -> dict[str, Any]:
    """Populate stable posted-time metadata from absolute or relative site labels."""

    normalized = dict(row)
    observed_text = str(observed_at or normalized.get("posted_observed_at") or normalized.get("ts") or "").strip()
    observed_date = _coerce_observed_date(observed_text)
    observed_iso = observed_text or datetime.combine(observed_date, datetime.min.time()).isoformat(timespec="seconds")

    posted_at = parse_posted_date(normalized.get("posted_at"), observed_at=observed_iso)
    if posted_at is None:
        posted_at = parse_posted_date(normalized.get("posted_label"), observed_at=observed_iso)
    if posted_at is not None:
        normalized["inferred_posted_date"] = posted_at.isoformat()
        normalized["posted_observed_at"] = observed_iso
        age_days = max(0, (observed_date - posted_at).days)
        normalized["observed_posted_age_days"] = age_days
        normalized["observed_posted_age_is_lower_bound"] = False
        return normalized

    text = " ".join(
        str(normalized.get(field) or "")
        for field in ("posted_label", "posted_at", "fit_reason", "match_reason_initial", "match_reason_final", "reason")
    )
    relative = parse_relative_posted_age(text)
    days = relative.get("days")
    if days is None:
        return normalized

    age_days = int(days)
    normalized["posted_observed_at"] = observed_iso
    normalized["observed_posted_age_days"] = age_days
    normalized["observed_posted_age_is_lower_bound"] = bool(relative.get("is_lower_bound"))
    normalized["inferred_posted_date"] = (observed_date - timedelta(days=age_days)).isoformat()
    return normalized


def current_posted_age_observation(row: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    """Return the current age of a posting based on normalized observed-time metadata."""

    current_date = today or datetime.now().date()
    posted_date = parse_posted_date(row.get("posted_at"), observed_at=current_date.isoformat())
    if posted_date is None:
        posted_date = parse_posted_date(row.get("inferred_posted_date"), observed_at=current_date.isoformat())
    if posted_date is not None:
        return {
            "days": max(0, (current_date - posted_date).days),
            "is_lower_bound": bool(row.get("observed_posted_age_is_lower_bound")),
            "source": "posted_date",
        }

    observed_age = row.get("observed_posted_age_days")
    try:
        age_days = int(float(observed_age))
    except Exception:
        age_days = None
    if age_days is None:
        relative = parse_relative_posted_age(
            " ".join(str(row.get(field) or "") for field in ("posted_label", "posted_at", "fit_reason", "reason"))
        )
        age_days = relative.get("days")
        if age_days is None:
            return {"days": None, "is_lower_bound": False, "source": ""}
        return {
            "days": int(age_days),
            "is_lower_bound": bool(relative.get("is_lower_bound")),
            "source": "relative_label",
        }

    observed_at = str(row.get("posted_observed_at") or row.get("ts") or "").strip()
    if observed_at:
        observed_date = _coerce_observed_date(observed_at)
        age_days += max(0, (current_date - observed_date).days)
    return {
        "days": int(age_days),
        "is_lower_bound": bool(row.get("observed_posted_age_is_lower_bound")),
        "source": "observed_age",
    }
