"""Generic orchestration port for retained site runtime lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from careereng.orchestration.agent_protocol.runtime_lifecycle import release_site_payload


@dataclass
class SiteRuntimeLifecycle:
    """Release resources only after orchestration has made its terminal decision."""

    browser_runner: Any | None
    _released_site_keys: set[str] = field(default_factory=set)

    def release_site(self, site_key: str) -> bool:
        normalized_site_key = release_site_payload(site_key=site_key)["site_key"]
        if normalized_site_key in self._released_site_keys:
            return False
        finish_site = getattr(self.browser_runner, "finish_site", None)
        if not callable(finish_site):
            return False
        finish_site(normalized_site_key)
        self._released_site_keys.add(normalized_site_key)
        return True


def is_non_resumable_site_terminal(site: dict[str, Any]) -> bool:
    """Check generic persisted lifecycle state without interpreting site behavior."""

    status = str(site.get("status") or "").strip()
    if status in {"failed", "cancelled"}:
        return True
    if status != "completed":
        return False
    apply = site.get("apply") if isinstance(site.get("apply"), dict) else {}
    return str(apply.get("status") or "").strip() not in {"pending", "running"}
