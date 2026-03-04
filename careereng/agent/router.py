"""Message intent routing helpers."""

from __future__ import annotations

import re


def is_yes(text: str) -> bool:
    val = text.strip().lower()
    return val in {"y", "yes", "是", "好", "确认", "ok"}


def is_no(text: str) -> bool:
    val = text.strip().lower()
    return val in {"n", "no", "否", "不用", "取消"}


def detect_site_request(text: str) -> dict:
    raw = text.strip()
    lowered = raw.lower()

    trigger = any(
        kw in raw
        for kw in ["检索投递", "投递", "检索", "岗位", "职位", "公司", "apply", "search jobs", "job"]
    )
    if not trigger:
        return {"is_site_flow": False, "apply_requested": False, "company": "", "base_url": ""}

    apply_requested = ("投递" in raw) or ("apply" in lowered)

    m = re.search(r"(?:检索投递|投递|检索)\s*([\w一-鿿\- ]+?)(?:公司|官网|网站|岗位|职位|$)", raw)
    company = m.group(1).strip() if m else ""

    if not company:
        m2 = re.search(r"([\w一-鿿\- ]+?)\s*(?:公司|careers|jobs)", raw, flags=re.I)
        company = m2.group(1).strip() if m2 else ""

    if not company:
        company = "target-site"

    base_url = ""
    url_match = re.search(r"https?://[^\s]+", raw)
    if url_match:
        base_url = url_match.group(0)

    return {
        "is_site_flow": True,
        "apply_requested": apply_requested,
        "company": company,
        "base_url": base_url,
    }
