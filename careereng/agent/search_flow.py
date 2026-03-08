"""Company selection and registration flow helpers."""

from __future__ import annotations

import re
from typing import Any, Callable


class SearchFlow:
    def __init__(
        self,
        *,
        site_tools: Any,
        save_state_fn: Callable[[str, dict[str, Any]], None],
        channel_locator: Any,
    ):
        self.site_tools = site_tools
        self.save_state_fn = save_state_fn
        self.channel_locator = channel_locator

    @staticmethod
    def parse_company_indices(message: str, max_idx: int) -> list[int]:
        if max_idx <= 0:
            return []
        nums = [int(x) for x in re.findall(r"\d+", message)]
        seen: set[int] = set()
        out: list[int] = []
        for n in nums:
            if 1 <= n <= max_idx and n not in seen:
                seen.add(n)
                out.append(n)
        return out

    def finalize_company_selection(
        self,
        *,
        session_id: str,
        turn_id: str,
        query_id: str,
        selected_companies: list[dict[str, Any]],
        state: dict[str, Any],
        run_site_searches_parallel: Callable[..., list[dict[str, Any]]],
    ) -> str:
        if not selected_companies:
            self.save_state_fn(session_id, state)
            return "已完成公司筛选，但没有保留公司。可重新发起搜索。"

        selected_companies = self.channel_locator.resolve_company_apply_channels(
            query_id=query_id,
            companies=selected_companies,
        )
        search_results = run_site_searches_parallel(
            session_id=session_id,
            turn_id=turn_id,
            selected_companies=selected_companies,
        )
        self.save_state_fn(session_id, state)

        if not search_results:
            return "公司已确认，但未能完成站点注册。"

        lines = [f"已注册 {len(search_results)} 个站点，并更新注册表。"]
        lines.append("当前阶段只保存站点入口 URL，不写入 jobs/catalog.jsonl 或 discoveries。")
        for idx, row in enumerate(search_results, 1):
            site_result = row.get("site_result") if isinstance(row.get("site_result"), dict) else {}
            company = str(site_result.get("site_name") or row.get("company") or "")
            site_id = str(site_result.get("site_id") or row.get("site_id") or "")
            base_url = str(site_result.get("base_url") or row.get("base_url") or "")
            skill_path = str(site_result.get("skill_path") or "")
            skill_note = "已生成模板" if bool(site_result.get("skill_template_created")) else "沿用已有模板"
            lines.append(f"{idx}. {company} [{site_id}]")
            if base_url:
                lines.append(f"   - entry_url: {base_url}")
            else:
                lines.append("   - entry_url: 未定位到，可后续手动更新")
            if skill_path:
                lines.append(f"   - site_skill: {skill_path} ({skill_note})")
        return "\n".join(lines)
