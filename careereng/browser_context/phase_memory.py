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
    do_not_repeat: dict[str, str] = field(default_factory=dict)

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

    def set_do_not_repeat(self, *, key: str, text: str) -> None:
        normalized = str(key or "").strip()
        if normalized and str(text or "").strip():
            self.do_not_repeat[normalized] = str(text).strip()

    def drop(self, *keys: str) -> None:
        for raw in keys:
            normalized = str(raw or "").strip()
            if not normalized:
                continue
            self.completed.pop(normalized, None)
            self.confirmed.pop(normalized, None)
            self.do_not_repeat.pop(normalized, None)

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
        if self.do_not_repeat:
            lines.append("Do not repeat:")
            for text in self.do_not_repeat.values():
                lines.append(f"- {text}")
        return "\n".join(lines).strip()
