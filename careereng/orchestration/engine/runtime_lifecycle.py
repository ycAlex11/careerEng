"""Generic orchestration port for retained site runtime lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from careereng.orchestration.agent_protocol.runtime_lifecycle import release_site_payload


@dataclass
class SiteRuntimeLifecycle:
    """Release resources only after orchestration has made its terminal decision."""

    browser_runner: Any | None

    def complete_site_work_item(self, site_key: str) -> bool:
        normalized_site_key = release_site_payload(site_key=site_key)["site_key"]
        complete_work_item = getattr(self.browser_runner, "complete_site_work_item", None)
        if not callable(complete_work_item):
            return False
        result = complete_work_item(normalized_site_key)
        return result is not False

    def release_site(self, site_key: str) -> bool:
        normalized_site_key = release_site_payload(site_key=site_key)["site_key"]
        finish_site = getattr(self.browser_runner, "finish_site", None)
        if not callable(finish_site):
            return False
        result = finish_site(normalized_site_key)
        return result is not False


def is_non_resumable_site_terminal(site: dict[str, Any]) -> bool:
    """Check generic persisted lifecycle state without interpreting site behavior."""

    status = str(site.get("status") or "").strip()
    if status in {"failed", "cancelled"}:
        return True
    if status != "completed":
        return False
    apply = site.get("apply") if isinstance(site.get("apply"), dict) else {}
    return str(apply.get("status") or "").strip() not in {"pending", "running"}
