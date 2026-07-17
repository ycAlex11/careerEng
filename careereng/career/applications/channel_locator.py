"""Apply-channel lookup helpers."""

from __future__ import annotations

from typing import Any


class ChannelLocator:
    def __init__(self, *, site_tools: Any, search_store: Any):
        self.site_tools = site_tools
        self.search_store = search_store

    def resolve_company_apply_channels(
        self,
        *,
        query_id: str,
        companies: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        resolved_rows: list[dict[str, Any]] = []
        for row in companies:
            resolved = dict(row)
            company = str(row.get("company") or "").strip()
            base_url = str(row.get("base_url") or "").strip()
            if not company or base_url:
                resolved_rows.append(resolved)
                continue
            existing = self.site_tools.site_store.find_site(company)
            if isinstance(existing, dict):
                existing_url = str(existing.get("base_url") or "").strip()
                if existing_url:
                    resolved["base_url"] = existing_url
                    resolved["channel_source"] = "site_registry"
            resolved_rows.append(resolved)
        return resolved_rows
