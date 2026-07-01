"""Evolution strategy router helpers.

This module exposes router/spec paths and short text payloads. It does not
choose business evidence or propose workflow strategy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


STRATEGY_ROUTER_RELATIVE_PATH = Path("docs") / "evolution" / "EVOLUTION_STRATEGY_ROUTER.md"
SITE_WORKFLOW_FAMILY_IDS = (
    "new_site_workflow_transfer",
    "apply_form_workflow",
    "site_workflow_compaction",
)


def strategy_router_path(project_root: Path | str) -> Path:
    return Path(project_root) / STRATEGY_ROUTER_RELATIVE_PATH


def strategy_router_payload(project_root: Path | str, *, max_chars: int = 12000) -> dict[str, Any]:
    path = strategy_router_path(project_root)
    if not path.exists():
        return {
            "path": str(path),
            "relative_path": str(STRATEGY_ROUTER_RELATIVE_PATH),
            "status": "missing",
            "text": "",
        }
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    return {
        "path": str(path),
        "relative_path": str(STRATEGY_ROUTER_RELATIVE_PATH),
        "status": "found",
        "text": _truncate(text, max_chars),
    }


def related_strategy_spec_payloads(
    project_root: Path | str,
    *,
    candidate_id: str,
    max_chars: int = 12000,
) -> list[dict[str, Any]]:
    root = Path(project_root)
    normalized = str(candidate_id or "").strip()
    ids = list(SITE_WORKFLOW_FAMILY_IDS) if normalized in SITE_WORKFLOW_FAMILY_IDS else [normalized]
    payloads: list[dict[str, Any]] = []
    seen: set[str] = set()
    for spec_id in ids:
        if not spec_id or spec_id in seen:
            continue
        seen.add(spec_id)
        relative = Path("docs") / "evolution" / "candidates" / f"{spec_id}.md"
        path = root / relative
        if not path.exists():
            payloads.append({"id": spec_id, "path": str(path), "relative_path": str(relative), "status": "missing", "text": ""})
            continue
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        payloads.append(
            {
                "id": spec_id,
                "path": str(path),
                "relative_path": str(relative),
                "status": "found",
                "text": _truncate(text, max_chars),
            }
        )
    return payloads


def strategy_family(candidate_id: str) -> str:
    normalized = str(candidate_id or "").strip()
    if normalized in SITE_WORKFLOW_FAMILY_IDS:
        return "site_workflow_evolution"
    if normalized == "assistant_router_memory_intake":
        return "assistant_memory_evolution"
    if normalized == "application_strategy_evolution":
        return "application_strategy_evolution"
    if normalized == "resume_profile_evolution":
        return "resume_profile_evolution"
    if normalized == "target_company_intelligence_evolution":
        return "target_company_intelligence_evolution"
    return normalized or "unknown"


def _truncate(text: str, max_chars: int) -> str:
    value = str(text or "")
    limit = max(1, int(max_chars or 1))
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n...[truncated]"
