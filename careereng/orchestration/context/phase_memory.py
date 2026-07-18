"""Run-local phase memory for browser phases."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PhaseActionRecord:
    tool: str
    action: str
    status: str
    url: str = ""
    title: str = ""
    outcome: str = ""


@dataclass
class BrowserPhaseMemory:
    recent_action_limit: int = 4
    recent_actions: list[PhaseActionRecord] = field(default_factory=list)
    completed: dict[str, str] = field(default_factory=dict)
    confirmed: dict[str, str] = field(default_factory=dict)
    pending: dict[str, str] = field(default_factory=dict)
    do_not_repeat: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: object) -> "BrowserPhaseMemory":
        """Rebuild run-local memory from a durable phase-session payload."""

        data = payload if isinstance(payload, dict) else {}
        raw_actions = data.get("recent_actions") if isinstance(data.get("recent_actions"), list) else []
        actions: list[PhaseActionRecord] = []
        for item in raw_actions:
            if not isinstance(item, dict):
                continue
            actions.append(
                PhaseActionRecord(
                    tool=str(item.get("tool") or "").strip(),
                    action=str(item.get("action") or "").strip(),
                    status=str(item.get("status") or "").strip(),
                    url=str(item.get("url") or "").strip(),
                    title=str(item.get("title") or "").strip(),
                    outcome=str(item.get("outcome") or "").strip(),
                )
            )
        def _text_map(key: str) -> dict[str, str]:
            raw_map = data.get(key)
            if not isinstance(raw_map, dict):
                return {}
            return {str(item_key): str(value) for item_key, value in raw_map.items() if str(item_key) and str(value)}

        raw_metrics = data.get("metrics")
        metrics = raw_metrics if isinstance(raw_metrics, dict) else {}
        memory = cls(
            recent_action_limit=max(1, int(data.get("recent_action_limit") or 4)),
            recent_actions=actions,
            completed=_text_map("completed"),
            confirmed=_text_map("confirmed"),
            pending=_text_map("pending"),
            do_not_repeat=_text_map("do_not_repeat"),
            metrics={
                str(key): int(value)
                for key, value in metrics.items()
                if str(key) and isinstance(value, int) and value > 0
            },
        )
        if len(memory.recent_actions) > memory.recent_action_limit:
            memory.recent_actions[:] = memory.recent_actions[-memory.recent_action_limit :]
        return memory

    def as_payload(self) -> dict[str, object]:
        """Return only serializable run-local state for a phase session."""

        return {
            "recent_action_limit": self.recent_action_limit,
            "recent_actions": [
                {
                    "tool": action.tool,
                    "action": action.action,
                    "status": action.status,
                    "url": action.url,
                    "title": action.title,
                    "outcome": action.outcome,
                }
                for action in self.recent_actions
            ],
            "completed": dict(self.completed),
            "confirmed": dict(self.confirmed),
            "pending": dict(self.pending),
            "do_not_repeat": dict(self.do_not_repeat),
            "metrics": dict(self.metrics),
        }

    def record_action(
        self,
        *,
        tool: str,
        action: str,
        status: str,
        url: str = "",
        title: str = "",
        outcome: str = "",
    ) -> None:
        self.recent_actions.append(
            PhaseActionRecord(
                tool=str(tool or "").strip(),
                action=str(action or "").strip(),
                status=str(status or "").strip(),
                url=str(url or "").strip(),
                title=str(title or "").strip(),
                outcome=str(outcome or "").strip(),
            )
        )
        if len(self.recent_actions) > max(1, int(self.recent_action_limit or 1)):
            del self.recent_actions[:-max(1, int(self.recent_action_limit or 1))]

    def clear_recent_actions(self) -> None:
        self.recent_actions.clear()

    def keep_last_recent_action(self) -> None:
        if self.recent_actions:
            self.recent_actions[:] = self.recent_actions[-1:]

    def has_recent_actions(self) -> bool:
        return bool(self.recent_actions)

    def set_completed(self, *, key: str, text: str) -> None:
        normalized = str(key or "").strip()
        if normalized and str(text or "").strip():
            self.completed[normalized] = str(text).strip()

    def set_confirmed(self, *, key: str, text: str) -> None:
        normalized = str(key or "").strip()
        if normalized and str(text or "").strip():
            self.confirmed[normalized] = str(text).strip()

    def set_pending(self, *, key: str, text: str) -> None:
        normalized = str(key or "").strip()
        if normalized and str(text or "").strip():
            self.pending[normalized] = str(text).strip()

    def set_do_not_repeat(self, *, key: str, text: str) -> None:
        normalized = str(key or "").strip()
        if normalized and str(text or "").strip():
            self.do_not_repeat[normalized] = str(text).strip()

    def set_metric(self, *, key: str, value: int) -> None:
        normalized = str(key or "").strip()
        number = int(value or 0)
        if normalized and number > 0:
            self.metrics[normalized] = number

    def get_metric(self, key: str) -> int | None:
        normalized = str(key or "").strip()
        value = self.metrics.get(normalized)
        if isinstance(value, int) and value > 0:
            return value
        return None

    def retrieval_budget_pages(self, *, default_page_size: int = 10, max_pages: int = 10) -> int | None:
        total_pages = self.get_metric("total_pages")
        if total_pages:
            return min(total_pages, max(1, int(max_pages or 1)))

        results_count = self.get_metric("results_count")
        if not results_count:
            return None
        page_size = self.get_metric("page_size") or max(1, int(default_page_size or 1))
        estimated_pages = (results_count + page_size - 1) // page_size
        return min(max(1, estimated_pages), max(1, int(max_pages or 1)))

    def drop(self, *keys: str) -> None:
        for raw in keys:
            normalized = str(raw or "").strip()
            if not normalized:
                continue
            self.completed.pop(normalized, None)
            self.confirmed.pop(normalized, None)
            self.pending.pop(normalized, None)
            self.do_not_repeat.pop(normalized, None)
            self.metrics.pop(normalized, None)

    def get_text(self, key: str) -> str:
        normalized = str(key or "").strip()
        if not normalized:
            return ""
        for bucket in (self.completed, self.confirmed, self.pending, self.do_not_repeat):
            value = str(bucket.get(normalized) or "").strip()
            if value:
                return value
        return ""

    def recent_actions_text(self) -> str:
        lines: list[str] = []
        for index, step in enumerate(self.recent_actions[-self.recent_action_limit :], start=1):
            tool = str(step.tool or "").strip() or "unknown"
            lines.append(f"Step {index}: {tool}")
            action = str(step.action or "").strip()
            if action:
                lines.append(f"Action: {action}")
            status = str(step.status or "").strip()
            if status:
                lines.append(f"Result: {status}")
            outcome = str(step.outcome or "").strip()
            if outcome:
                lines.append(f"Outcome: {outcome}")
            url = str(step.url or "").strip()
            if url:
                lines.append(f"URL: {url}")
            title = str(step.title or "").strip()
            if title:
                lines.append(f"Title: {title}")
        return "\n".join(lines).strip()

    def phase_memory_text(self) -> str:
        lines: list[str] = []
        if self.completed:
            lines.append("Completed:")
            for text in self.completed.values():
                lines.append(f"- {text}")
        if self.confirmed:
            lines.append("Confirmed:")
            for text in self.confirmed.values():
                lines.append(f"- {text}")
        if self.pending:
            lines.append("Pending:")
            for text in self.pending.values():
                lines.append(f"- {text}")
        if self.do_not_repeat:
            lines.append("Do not repeat:")
            for text in self.do_not_repeat.values():
                lines.append(f"- {text}")
        if self.metrics:
            lines.append("Metrics:")
            for key, value in self.metrics.items():
                lines.append(f"- {key}: {value}")
        return "\n".join(lines).strip()
