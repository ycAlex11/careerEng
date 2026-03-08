"""Apply-channel location helpers (official careers first)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from careereng.utils import safe_file_stem


class ChannelLocator:
    def __init__(self, *, site_tools: Any, search_store: Any):
        self.site_tools = site_tools
        self.search_store = search_store

    @staticmethod
    def is_official_careers_url(company: str, url: str) -> bool:
        raw = str(url or "").strip().lower()
        if not raw.startswith("http"):
            return False
        parsed = urlparse(raw)
        host = parsed.netloc.lower()
        path = parsed.path.lower()
        generic_hosts = ("workdayjobs.com", "greenhouse.io", "lever.co", "linkedin.com")
        if any(h in host for h in generic_hosts):
            return False
        company_key = safe_file_stem(company).replace("-", "")
        host_key = host.replace("-", "").replace(".", "")
        url_key = raw.replace("-", "")
        company_hit = bool(company_key and (company_key in host_key or company_key in url_key))
        careers_hit = any(k in host or k in path for k in ("careers", "career", "jobs"))
        return company_hit and careers_hit

    def score_apply_channel_url(self, company: str, url: str) -> float:
        raw = str(url or "").strip().lower()
        if not raw.startswith("http"):
            return -1.0
        if self.is_official_careers_url(company, raw):
            return 100.0
        parsed = urlparse(raw)
        host = parsed.netloc.lower()
        path = parsed.path.lower()
        score = 0.0
        company_key = safe_file_stem(company).replace("-", "")
        host_key = host.replace("-", "").replace(".", "")
        if company_key and (company_key in host_key or company_key in raw.replace("-", "")):
            score += 15.0
        if "workdayjobs.com" in host or "workday" in raw:
            score += 80.0
        elif "greenhouse.io" in host or "greenhouse" in raw:
            score += 75.0
        elif "lever.co" in host or "lever" in raw:
            score += 70.0
        elif "linkedin.com/jobs" in raw:
            score += 60.0
        elif "careers" in raw or "career" in raw or "/jobs" in path:
            score += 50.0
        else:
            score += 10.0
        if any(k in host for k in ("indeed.", "glassdoor.", "zhaopin.", "liepin.", "boss.")):
            score -= 25.0
        return score

    def resolve_company_apply_channels(
        self,
        *,
        query_id: str,
        companies: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        resolved_rows: list[dict[str, Any]] = []
        for row in companies:
            company = str(row.get("company") or "").strip()
            base_url = str(row.get("base_url") or "").strip()
            resolved = dict(row)
            if not company or base_url:
                resolved_rows.append(resolved)
                continue

            query_plan = [
                f"{company} careers",
                f"{company} workday jobs",
                f"{company} greenhouse jobs",
                f"{company} lever jobs",
                f"{company} linkedin jobs",
            ]
            best_url = ""
            best_score = -1.0
            official_found = False
            for query_text in query_plan:
                search_result = self.site_tools.playwright.search_google(query_text, max_items=8)
                items = search_result.get("items") if isinstance(search_result.get("items"), list) else []
                normalized: list[dict[str, Any]] = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    url = str(item.get("url") or "").strip()
                    if not url:
                        continue
                    normalized.append(
                        {
                            "result_type": "apply_channel",
                            "company": company,
                            "title": str(item.get("title") or "").strip(),
                            "url": url,
                            "snippet": str(item.get("snippet") or "").strip(),
                            "query_text": query_text,
                        }
                    )
                    score = self.score_apply_channel_url(company, url)
                    if score > best_score:
                        best_score = score
                        best_url = url
                    if self.is_official_careers_url(company, url):
                        official_found = True
                        if score >= best_score:
                            best_score = score
                            best_url = url
                        break
                self.search_store.append_web_results(
                    query_id=query_id,
                    query_text=query_text,
                    source="google_channel",
                    items=normalized,
                )
                if official_found:
                    break

            if best_url:
                resolved["base_url"] = best_url
                resolved["channel_source"] = "google_channel_official" if official_found else "google_channel"
            resolved_rows.append(resolved)
        return resolved_rows
