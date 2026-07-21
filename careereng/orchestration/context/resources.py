"""On-demand context resources shared by provider and external-agent paths."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from careereng.orchestration.context.registry import BrowserContextRegistry


CONTEXT_RESOURCE_IDS = ("apply_facts", "full_cv", "full_persona", "history_view")


def build_apply_initial_facts(
    *,
    registry: BrowserContextRegistry,
    staged_resume_pdf_path: str = "",
    target_job_ids: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Build lightweight apply facts shared by every LLM executor.

    The staged file reference is execution context, not CV content. It belongs
    in the initial apply envelope while detailed CV/persona data remains lazy.
    """

    facts = dict(registry.apply_facts or {})
    staged_path = str(staged_resume_pdf_path or "").strip()
    if staged_path:
        facts["staged_resume"] = {
            "path": staged_path,
            "filename": Path(staged_path).name,
        }
    normalized_targets = [str(item or "").strip() for item in (target_job_ids or ()) if str(item or "").strip()]
    if normalized_targets:
        facts["apply_target_job_ids"] = normalized_targets
    return facts


def render_apply_facts(facts: dict[str, Any], *, requested: bool = False) -> str:
    """Render a shared apply-facts envelope for a provider or external agent."""

    label = "requested bundle `apply_facts`" if requested else "initial apply context"
    return f"Current lightweight apply facts ({label}):\n" + json.dumps(facts or {}, ensure_ascii=False, indent=2)


@dataclass
class ContextResourceResolver:
    """Serve explicitly requested context without deciding when it is needed."""

    workspace: Path
    site_store: Any
    site_key: str
    batch_id: str
    registry: BrowserContextRegistry
    apply_initial_facts: dict[str, Any] | None = None

    @classmethod
    def create(
        cls,
        *,
        workspace: Path,
        site_store: Any,
        site_key: str,
        batch_id: str,
        registry: BrowserContextRegistry | None = None,
        apply_initial_facts: dict[str, Any] | None = None,
    ) -> "ContextResourceResolver":
        return cls(
            workspace=Path(workspace),
            site_store=site_store,
            site_key=str(site_key or "").strip(),
            batch_id=str(batch_id or "").strip(),
            registry=registry or BrowserContextRegistry(Path(workspace)),
            apply_initial_facts=dict(apply_initial_facts or {}),
        )

    def available_resource_ids(self) -> list[str]:
        available = list(self.registry.available_bundles())
        if self.apply_initial_facts and "apply_facts" not in available:
            available.append("apply_facts")
        if self.site_key and self.batch_id:
            available.append("history_view")
        return [resource_id for resource_id in CONTEXT_RESOURCE_IDS if resource_id in available]

    def read(self, resource_id: str, *, reason: str = "") -> dict[str, Any]:
        requested = str(resource_id or "").strip().lower()
        available = self.available_resource_ids()
        if requested not in CONTEXT_RESOURCE_IDS:
            raise ValueError(f"unknown context resource: {requested or '<missing>'}")
        if requested not in available:
            return self._unavailable(requested, available, reason)
        if requested == "history_view":
            return self._history_view(reason=reason)
        if requested == "apply_facts":
            facts = build_apply_initial_facts(registry=self.registry)
            facts.update(self.apply_initial_facts or {})
            value = render_apply_facts(facts, requested=True)
        else:
            value = self.registry.bundle_item_text(requested)
        return {
            "isError": False,
            "structuredContent": {
                "bundle": requested,
                "resource_id": requested,
                "available": True,
                "status": "provided",
                "reason": str(reason or "").strip(),
            },
            "content": [{"type": "text", "text": value}],
        }

    def request_bundle(self, *, bundle: str, reason: str = "") -> dict[str, Any]:
        """Compatibility surface used by the shared request_context tool."""

        return self.read(bundle, reason=reason)

    def _history_view(self, *, reason: str) -> dict[str, Any]:
        begin = getattr(self.site_store, "begin_batch_history_view", None)
        if not callable(begin):
            return self._unavailable("history_view", self.available_resource_ids(), reason)
        view = begin(self.site_key, self.batch_id, event_action="context_resource")
        rows = view.rows() if view is not None else []
        return {
            "isError": False,
            "structuredContent": {
                "resource_id": "history_view",
                "available": True,
                "status": "provided",
                "reason": str(reason or "").strip(),
                "site_key": self.site_key,
                "batch_id": self.batch_id,
                "row_count": len(rows),
            },
            "content": [
                {
                    "type": "text",
                    "text": "Current site history view (explicitly requested):\n"
                    + json.dumps(rows, ensure_ascii=False),
                }
            ],
        }

    @staticmethod
    def _unavailable(resource_id: str, available: list[str], reason: str) -> dict[str, Any]:
        return {
            "isError": False,
            "structuredContent": {
                "bundle": resource_id,
                "resource_id": resource_id,
                "available": False,
                "status": "unavailable",
                "available_resources": available,
                "reason": str(reason or "").strip(),
            },
            "content": [
                {
                    "type": "text",
                    "text": "### Result\n"
                    f"- Context resource `{resource_id}` is not available.\n"
                    f"- Available resources: {', '.join(available) or '(none)'}",
                }
            ],
        }
