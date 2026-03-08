"""Response text templates for agent flows."""

from __future__ import annotations

from typing import Any


def format_company_pick_prompt(pending: dict[str, Any]) -> str:
    candidates = pending.get("candidates") if isinstance(pending.get("candidates"), list) else []
    idx = int(pending.get("index") or 0)
    total = len(candidates)
    if idx >= total:
        return ""
    row = candidates[idx]
    evidence = row.get("evidence_urls") if isinstance(row.get("evidence_urls"), list) else []
    lines = [
        f"候选公司 {idx + 1}/{total}: {row.get('company')} ({row.get('base_url')})",
        f"推荐理由: {row.get('reason') or 'n/a'}",
    ]
    if evidence:
        lines.append("证据链接:")
        for url in evidence[:3]:
            lines.append(f"- {url}")
    lines.append("是否保留该公司用于后续检索？回复 y 或 n（可附原因，如 `n 规模太小`）。")
    return "\n".join(lines)


def format_company_index_pick_prompt(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "暂无公司候选。"
    lines = ["已生成公司候选，请回复要注册的序号（例如: `1 3 5`）。"]
    for idx, row in enumerate(candidates, 1):
        lines.append(f"{idx}. {row.get('company')} - {row.get('reason') or 'n/a'}")
    return "\n".join(lines)


def format_site_result_text(site_result: dict[str, Any]) -> str:
    lines = [
        f"[站点] {site_result.get('site_name')} ({site_result.get('site_id')})",
        f"状态: {site_result.get('status') or 'active'}",
    ]
    base_url = str(site_result.get("base_url") or "")
    if base_url:
        lines.append(f"入口 URL: {base_url}")
    else:
        lines.append("入口 URL: 未定位到，可后续手动更新。")
    if site_result.get("skill_path"):
        label = "已生成模板" if site_result.get("skill_template_created") else "沿用已有模板"
        lines.append(f"站点 Skill: {site_result.get('skill_path')} ({label})")
    lines.append("当前阶段仅登记站点，不写入岗位发现数据。")
    return "\n".join(lines)
