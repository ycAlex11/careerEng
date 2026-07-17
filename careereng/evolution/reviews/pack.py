"""Generic Codex-readable review pack writer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from careereng.evolution.reviews.schema import ReviewPack
from careereng.utils import ensure_dir, make_id, now_iso, write_json


def create_review_pack(
    *,
    review_type: str,
    subject_id: str,
    subject_ref: str,
    metrics: dict[str, Any] | None = None,
    sections: list[dict[str, Any]] | None = None,
    sample_rows: dict[str, list[dict[str, Any]]] | None = None,
    review_questions: list[str] | None = None,
    evidence_refs: list[str] | None = None,
    status: str = "needs_codex_review",
    recommended_status: str = "needs_codex_review",
    codex_review_required: bool = True,
) -> ReviewPack:
    return ReviewPack(
        review_id=make_id("review"),
        created_at=now_iso(),
        review_type=str(review_type or "").strip(),
        subject_id=str(subject_id or "").strip(),
        subject_ref=str(subject_ref or "").strip(),
        status=status,
        recommended_status=recommended_status,
        codex_review_required=bool(codex_review_required),
        metrics=dict(metrics or {}),
        sections=list(sections or []),
        sample_rows=dict(sample_rows or {}),
        review_questions=[str(item).strip() for item in review_questions or [] if str(item).strip()],
        evidence_refs=[str(item).strip() for item in evidence_refs or [] if str(item).strip()],
    )


def save_review_pack(
    pack: ReviewPack,
    *,
    output_dir: Path | str,
    markdown_name: str = "codex_review_pack.md",
    json_name: str = "review_pack.json",
) -> dict[str, Path]:
    directory = ensure_dir(Path(output_dir))
    markdown_path = directory / markdown_name
    json_path = directory / json_name
    pack.pack_path = str(markdown_path)
    markdown_path.write_text(render_review_pack_markdown(pack), encoding="utf-8")
    write_json(json_path, pack.to_dict())
    return {"markdown": markdown_path, "json": json_path}


def render_review_pack_markdown(pack: ReviewPack) -> str:
    data = pack.to_dict()
    lines: list[str] = [
        "# Codex Review Pack",
        "",
        "## Subject",
        "",
        f"- Review ID: `{pack.review_id}`",
        f"- Review Type: `{pack.review_type}`",
        f"- Subject ID: `{pack.subject_id}`",
        f"- Subject Ref: `{pack.subject_ref}`",
        f"- Status: `{pack.status}`",
        f"- Recommended Status: `{pack.recommended_status}`",
        f"- Codex Review Required: `{str(pack.codex_review_required).lower()}`",
        f"- Created At: {pack.created_at}",
        "",
        "## Instructions For Codex",
        "",
        "- Use only the evidence in this pack and referenced local files.",
        "- Judge whether the stored memory/routing evidence is useful, grounded, and correctly categorized.",
        "- Do not assume model quality; evaluate CareerEng local behavior and stored data quality.",
        "- Do not modify files while reviewing. Produce a review summary and recommended next status.",
        "",
        "## Metrics",
        "",
        "```json",
        json.dumps(data.get("metrics") or {}, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
    ]

    if pack.evidence_refs:
        lines.extend(["## Evidence Refs", ""])
        lines.extend(f"- `{ref}`" for ref in pack.evidence_refs)
        lines.append("")

    for section in pack.sections:
        title = str(section.get("title") or "Section").strip()
        body = str(section.get("body") or "").strip()
        lines.extend([f"## {title}", ""])
        if body:
            lines.extend([body, ""])
        rows = section.get("rows") if isinstance(section.get("rows"), list) else []
        if rows:
            lines.extend(["```json", json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True), "```", ""])

    if pack.sample_rows:
        lines.extend(["## Sample Rows", ""])
        for name, rows in pack.sample_rows.items():
            lines.extend([f"### {name}", ""])
            if rows:
                lines.extend(["```json", json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True), "```", ""])
            else:
                lines.extend(["- none", ""])

    lines.extend(["## Review Questions", ""])
    if pack.review_questions:
        lines.extend(f"{idx}. {question}" for idx, question in enumerate(pack.review_questions, 1))
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Allowed Review Notes",
            "",
            "- `accepted`: evidence looks useful and correctly categorized.",
            "- `keep_observing`: evidence is inconclusive or too sparse.",
            "- `low_confidence`: evidence is partially useful but weak or noisy.",
            "- `rejected`: evidence shows the change or memory behavior is wrong.",
            "- `rollback_recommended`: only if a file patch caused clear regression.",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"
