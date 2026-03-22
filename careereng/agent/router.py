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
    has_url = bool(re.search(r"https?://[^\s]+", raw))
    apply_requested = ("投递" in raw) or ("apply" in lowered)

    site_keywords = ["公司", "官网", "网站", "career", "careers", "workday", "linkedin", "greenhouse", "lever"]
    trigger = apply_requested or has_url or any(k in raw or k in lowered for k in site_keywords)
    if not trigger:
        return {"is_site_flow": False, "apply_requested": False, "company": "", "base_url": ""}

    m = re.search(r"(?:检索投递|投递|检索)\s*([\w一-鿿\- ]+?)(?:公司|官网|网站|careers?|jobs?|$)", raw, flags=re.I)
    company = m.group(1).strip() if m else ""

    if not company:
        m2 = re.search(r"([\w一-鿿\- ]+?)\s*(?:公司|careers|jobs)", raw, flags=re.I)
        company = m2.group(1).strip() if m2 else ""

    if not company and (apply_requested or has_url):
        company = "target-site"

    base_url = ""
    url_match = re.search(r"https?://[^\s]+", raw)
    if url_match:
        base_url = url_match.group(0)

    if not company and not base_url:
        return {"is_site_flow": False, "apply_requested": False, "company": "", "base_url": ""}

    return {
        "is_site_flow": True,
        "apply_requested": apply_requested,
        "company": company,
        "base_url": base_url,
    }


def detect_jobs_batch_request(text: str) -> dict:
    raw = text.strip()
    lowered = raw.lower()
    apply_requested = ("投递" in raw) or ("apply" in lowered) or ("submit" in lowered)
    retrieve_terms = ("检索", "查", "看看", "找", "search", "retrieve")
    registered_terms = ("已注册", "注册的", "registered")
    company_terms = ("公司", "站点", "sites", "site")
    explicit_phrases = (
        "开始检索并投递已注册的公司",
        "投递已注册的公司",
        "检索已注册的公司",
        "检查已注册公司的岗位",
        "开始投递已注册的公司",
    )
    if any(phrase in raw or phrase in lowered for phrase in explicit_phrases):
        return {"is_jobs_batch_flow": True, "apply_requested": apply_requested}
    if any(term in raw for term in registered_terms) and any(term in raw or term in lowered for term in company_terms):
        if apply_requested or any(term in raw or term in lowered for term in retrieve_terms):
            return {"is_jobs_batch_flow": True, "apply_requested": apply_requested}
    if "相关的公司" in raw and apply_requested:
        return {"is_jobs_batch_flow": True, "apply_requested": True}
    if "registered" in lowered and ("company" in lowered or "site" in lowered) and (
        apply_requested or any(term in lowered for term in retrieve_terms)
    ):
        return {"is_jobs_batch_flow": True, "apply_requested": apply_requested}
    return {"is_jobs_batch_flow": False, "apply_requested": False}


def detect_jobs_batch_request(text: str) -> dict:
    raw = text.strip()
    lowered = raw.lower()
    apply_requested = ("投递" in raw) or ("apply" in lowered) or ("submit" in lowered)
    retrieve_terms = ("检索", "查", "看看", "找", "search", "retrieve")
    registered_terms = ("已注册", "注册的", "registered")
    company_terms = ("公司", "站点", "sites", "site")
    explicit_phrases = (
        "开始检索并投递已注册的公司",
        "投递已注册的公司",
        "检索已注册的公司",
        "检查已注册公司的岗位",
        "开始投递已注册的公司",
    )
    if any(phrase in raw or phrase in lowered for phrase in explicit_phrases):
        return {"is_jobs_batch_flow": True, "apply_requested": apply_requested}
    if any(term in raw for term in registered_terms) and any(term in raw or term in lowered for term in company_terms):
        if apply_requested or any(term in raw or term in lowered for term in retrieve_terms):
            return {"is_jobs_batch_flow": True, "apply_requested": apply_requested}
    if "相关的公司" in raw and apply_requested:
        return {"is_jobs_batch_flow": True, "apply_requested": True}
    if "registered" in lowered and ("company" in lowered or "site" in lowered) and (
        apply_requested or any(term in lowered for term in retrieve_terms)
    ):
        return {"is_jobs_batch_flow": True, "apply_requested": apply_requested}
    return {"is_jobs_batch_flow": False, "apply_requested": False}


def detect_search_request(text: str) -> dict:
    raw = text.strip()
    lowered = raw.lower()
    explicit_keywords = [
        "搜索岗位",
        "检索岗位",
        "找工作",
        "找岗位",
        "搜索职位",
        "job search",
        "search jobs",
        "find jobs",
        "我现在在找工作",
        "在找工作",
        "求职",
        "推荐一些公司",
        "推荐公司",
        "适合我的岗位",
        "适合我的工作",
    ]
    if any(k in raw or k in lowered for k in explicit_keywords):
        return {"is_search_flow": True, "query": raw}

    zh_search_verbs = ("搜索", "检索", "找", "查", "搜")
    zh_job_terms = ("岗位", "职位", "工作")
    if any(v in raw for v in zh_search_verbs) and any(t in raw for t in zh_job_terms):
        return {"is_search_flow": True, "query": raw}

    if "推荐" in raw and "公司" in raw:
        return {"is_search_flow": True, "query": raw}

    if "适合" in raw and any(t in raw for t in zh_job_terms):
        return {"is_search_flow": True, "query": raw}

    en_pattern = r"\b(search|find|look for)\b.*\b(job|jobs|role|roles|position|positions)\b"
    if re.search(en_pattern, lowered):
        return {"is_search_flow": True, "query": raw}

    return {"is_search_flow": False, "query": ""}


def parse_yes_no_reason(text: str) -> tuple[str, str]:
    val = text.strip()
    lowered = val.lower()
    if lowered.startswith("y") or val.startswith(("是", "好", "确认", "可以")):
        return "yes", ""
    if lowered.startswith("n") or val.startswith(("否", "不", "不要", "不用", "取消")):
        reason = val[1:].strip() if val else ""
        for prefix in [":", "：", "-", "，", ","]:
            if reason.startswith(prefix):
                reason = reason[1:].strip()
        return "no", reason
    return "unknown", ""
