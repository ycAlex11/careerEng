"""Markdown rendering for action cards."""

from __future__ import annotations

import json
from typing import Any


def render_action_card_markdown(card: dict[str, Any]) -> str:
    card_id = str(card.get("card_id") or "")
    commands = _list_values(card.get("commands"))
    if not commands and card_id:
        commands = [
            f"python -m careereng action-card show {card_id}",
            f'python -m careereng action-card close {card_id} --result "<review summary>"',
            f'python -m careereng action-card cancel {card_id} --reason "<reason>"',
        ]

    lines: list[str] = [
        f"# Action Card: {card.get('title') or card_id}",
        "",
        "## Metadata",
        "",
        f"- Card ID: `{card_id}`",
        f"- Type: `{card.get('card_type') or ''}`",
        f"- Status: `{card.get('status') or ''}`",
        f"- Priority: `{card.get('priority') or 'medium'}`",
        f"- Created At: {card.get('created_at') or ''}",
        f"- Updated At: {card.get('updated_at') or ''}",
        "",
        "## Task Metadata",
        "",
    ]
    metadata = card.get("metadata") if isinstance(card.get("metadata"), dict) else {}
    if metadata:
        lines.extend(["```json", json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True), "```"])
    else:
        lines.append("- none")
    required_output = str(metadata.get("required_output") or "").strip()
    proposal_contract = metadata.get("proposal_contract") if isinstance(metadata.get("proposal_contract"), dict) else {}
    if required_output or proposal_contract:
        lines.extend(["", "## Required Output For Codex", ""])
        if required_output:
            lines.append(f"- Required output: `{required_output}`")
        if proposal_contract:
            lines.extend(
                [
                    "- Proposal contract:",
                    "```json",
                    json.dumps(proposal_contract, ensure_ascii=False, indent=2, sort_keys=True),
                    "```",
                ]
            )
    lines.extend(["", "## Semantic Tags", ""])
    lines.extend(_bullets([f"`{tag}`" for tag in _list_values(card.get("semantic_tags"))]))
    lines.extend(["", "## Goal", "", _text_or_dash(card.get("goal"))])
    lines.extend(["", "## Why This Exists", "", _text_or_dash(card.get("reason"))])
    lines.extend(
        [
            "",
            "## Source",
            "",
            f"- Source Type: `{card.get('source_type') or ''}`",
            f"- Source ID: `{card.get('source_id') or ''}`",
            f"- Source Ref: `{card.get('source_ref') or ''}`",
            "",
            "## Related Files",
            "",
        ]
    )
    lines.extend(_bullets(_list_values(card.get("related_files"))))
    lines.extend(["", "## Suggested Actions For Codex", ""])
    lines.extend(_bullets(_list_values(card.get("suggested_actions"))))
    lines.extend(["", "## Commands", ""])
    lines.extend(_bullets([f"`{command}`" for command in commands]))
    lines.extend(["", "## Safety Notes", ""])
    lines.extend(_bullets(_list_values(card.get("safety_notes"))))
    lines.extend(["", "## Done When", ""])
    lines.extend(_bullets(_list_values(card.get("done_when"))))
    result_summary = str(card.get("result_summary") or "").strip()
    if result_summary:
        lines.extend(["", "## Result Summary", "", result_summary])
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _list_values(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _bullets(values: list[str]) -> list[str]:
    if not values:
        return ["- none"]
    return [f"- {value}" for value in values]


def _text_or_dash(value: Any) -> str:
    text = str(value or "").strip()
    return text if text else "-"
