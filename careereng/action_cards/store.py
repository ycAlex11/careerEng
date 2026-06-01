"""Local action-card store."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.action_cards.renderer import render_action_card_markdown
from careereng.action_cards.schema import (
    ACTION_CARD_CANCELLED,
    ACTION_CARD_DONE,
    ACTION_CARD_OPEN,
    ACTION_CARD_STATUSES,
    ActionCard,
)
from careereng.storage.jsonl import JSONLStore
from careereng.utils import ensure_dir, make_id, now_iso


class ActionCardError(ValueError):
    """Raised when an action-card operation cannot be completed."""


class ActionCardStore:
    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.root = self.workspace / "action_cards"
        self.open_dir = self.root / ACTION_CARD_OPEN
        self.done_dir = self.root / ACTION_CARD_DONE
        self.cancelled_dir = self.root / ACTION_CARD_CANCELLED
        self.index_path = self.root / "index.jsonl"
        self.events_path = self.root / "events.jsonl"
        self._ensure_layout()

    def create_card(
        self,
        *,
        card_type: str,
        title: str,
        goal: str,
        reason: str = "",
        source_type: str = "",
        source_id: str = "",
        source_ref: str = "",
        priority: str = "medium",
        related_files: list[str] | None = None,
        suggested_actions: list[str] | None = None,
        commands: list[str] | None = None,
        safety_notes: list[str] | None = None,
        done_when: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        semantic_tags: list[str] | None = None,
        dedupe_key: str = "",
    ) -> dict[str, Any]:
        normalized_type = str(card_type or "").strip()
        normalized_source_type = str(source_type or "").strip()
        normalized_source_id = str(source_id or "").strip()
        normalized_dedupe_key = str(dedupe_key or "").strip()
        if not normalized_dedupe_key and normalized_type and normalized_source_type and normalized_source_id:
            normalized_dedupe_key = f"{normalized_type}:{normalized_source_type}:{normalized_source_id}"
        if normalized_dedupe_key:
            existing = self.find_by_dedupe_key(normalized_dedupe_key)
            if existing:
                return existing

        now = now_iso()
        card_id = make_id("action_card")
        card = ActionCard(
            card_id=card_id,
            created_at=now,
            updated_at=now,
            status=ACTION_CARD_OPEN,
            card_type=normalized_type,
            title=str(title or "").strip() or card_id,
            goal=str(goal or "").strip(),
            reason=str(reason or "").strip(),
            source_type=normalized_source_type,
            source_id=normalized_source_id,
            source_ref=str(source_ref or "").strip(),
            priority=str(priority or "medium").strip() or "medium",
            related_files=_clean_list(related_files),
            suggested_actions=_clean_list(suggested_actions),
            commands=_clean_list(commands),
            safety_notes=_clean_list(safety_notes),
            done_when=_clean_list(done_when),
            metadata=dict(metadata or {}),
            semantic_tags=_clean_list(semantic_tags),
            markdown_path=self._relative_markdown_path(ACTION_CARD_OPEN, card_id),
            dedupe_key=normalized_dedupe_key,
        ).to_dict()
        self._write_card_markdown(card)
        self._upsert_index(card)
        self._append_event(
            event_type="card.created",
            card=card,
            summary=f"Created action card: {card['title']}",
        )
        return card

    def list_cards(self, *, status: str = ACTION_CARD_OPEN, limit: int = 50) -> list[dict[str, Any]]:
        normalized_status = str(status or "").strip().lower()
        rows = self._index_store().read_all()
        if normalized_status and normalized_status != "all":
            self._validate_status(normalized_status)
            rows = [row for row in rows if str(row.get("status") or "") == normalized_status]
        rows.sort(key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""), reverse=True)
        if limit > 0:
            rows = rows[: int(limit)]
        return rows

    def show_card(self, card_id: str) -> dict[str, Any]:
        normalized = str(card_id or "").strip()
        if not normalized:
            raise ActionCardError("card_id is required")
        for row in self._index_store().read_all():
            if str(row.get("card_id") or "") == normalized:
                return row
        raise ActionCardError(f"action card not found: {normalized}")

    def close_card(self, card_id: str, *, result_summary: str = "") -> dict[str, Any]:
        return self._transition_card(
            card_id,
            status=ACTION_CARD_DONE,
            result_summary=result_summary,
            event_type="card.closed",
            event_summary=f"Closed action card: {card_id}",
        )

    def cancel_card(self, card_id: str, *, reason: str = "") -> dict[str, Any]:
        summary = str(reason or "").strip()
        return self._transition_card(
            card_id,
            status=ACTION_CARD_CANCELLED,
            result_summary=summary,
            event_type="card.cancelled",
            event_summary=f"Cancelled action card: {card_id}",
        )

    def update_card_metadata(
        self,
        card_id: str,
        *,
        metadata: dict[str, Any] | None = None,
        related_files: list[str] | None = None,
        commands: list[str] | None = None,
        summary: str = "",
    ) -> dict[str, Any]:
        """Merge handoff metadata into an existing card and re-render it."""
        card = dict(self.show_card(card_id))
        existing_metadata = card.get("metadata") if isinstance(card.get("metadata"), dict) else {}
        card["metadata"] = {**existing_metadata, **dict(metadata or {})}
        if related_files is not None:
            card["related_files"] = _append_unique(_clean_list(card.get("related_files")), _clean_list(related_files))
        if commands is not None:
            card["commands"] = _append_unique(_clean_list(card.get("commands")), _clean_list(commands))
        card["updated_at"] = now_iso()
        self._write_card_markdown(card)
        self._upsert_index(card)
        self._append_event(
            event_type="card.updated",
            card=card,
            summary=str(summary or "Updated action card metadata.").strip(),
        )
        return card

    def find_by_dedupe_key(self, dedupe_key: str) -> dict[str, Any] | None:
        normalized = str(dedupe_key or "").strip()
        if not normalized:
            return None
        for row in self._index_store().read_all():
            if str(row.get("dedupe_key") or "") == normalized:
                return row
        return None

    def markdown_text(self, card_id: str) -> str:
        card = self.show_card(card_id)
        path = self._resolve_markdown_path(card)
        if path.exists():
            return path.read_text(encoding="utf-8")
        return render_action_card_markdown(card)

    def _transition_card(
        self,
        card_id: str,
        *,
        status: str,
        result_summary: str,
        event_type: str,
        event_summary: str,
    ) -> dict[str, Any]:
        self._validate_status(status)
        card = dict(self.show_card(card_id))
        old_path = self._resolve_markdown_path(card)
        card["status"] = status
        card["updated_at"] = now_iso()
        if result_summary:
            card["result_summary"] = str(result_summary).strip()
        card["markdown_path"] = self._relative_markdown_path(status, str(card["card_id"]))
        self._write_card_markdown(card)
        new_path = self._resolve_markdown_path(card)
        if old_path != new_path and old_path.exists():
            old_path.unlink()
        self._upsert_index(card)
        self._append_event(event_type=event_type, card=card, summary=event_summary)
        return card

    def _ensure_layout(self) -> None:
        ensure_dir(self.open_dir)
        ensure_dir(self.done_dir)
        ensure_dir(self.cancelled_dir)
        JSONLStore(self.index_path)
        JSONLStore(self.events_path)

    def _index_store(self) -> JSONLStore:
        return JSONLStore(self.index_path)

    def _events_store(self) -> JSONLStore:
        return JSONLStore(self.events_path)

    def _upsert_index(self, card: dict[str, Any]) -> None:
        rows = self._index_store().read_all()
        updated = False
        next_rows: list[dict[str, Any]] = []
        card_id = str(card.get("card_id") or "")
        for row in rows:
            if str(row.get("card_id") or "") == card_id:
                next_rows.append(dict(card))
                updated = True
            else:
                next_rows.append(row)
        if not updated:
            next_rows.append(dict(card))
        self._index_store().write_all(next_rows)

    def _append_event(self, *, event_type: str, card: dict[str, Any], summary: str) -> None:
        self._events_store().append(
            {
                "event_id": make_id("action_card_event"),
                "created_at": now_iso(),
                "event_type": event_type,
                "card_id": card.get("card_id") or "",
                "card_type": card.get("card_type") or "",
                "status": card.get("status") or "",
                "summary": summary,
                "source_type": card.get("source_type") or "",
                "source_id": card.get("source_id") or "",
            }
        )

    def _write_card_markdown(self, card: dict[str, Any]) -> None:
        path = self._resolve_markdown_path(card)
        ensure_dir(path.parent)
        path.write_text(render_action_card_markdown(card), encoding="utf-8")

    def _resolve_markdown_path(self, card: dict[str, Any]) -> Path:
        raw = str(card.get("markdown_path") or "").strip()
        if not raw:
            raw = self._relative_markdown_path(str(card.get("status") or ACTION_CARD_OPEN), str(card.get("card_id") or "card"))
        path = Path(raw)
        if path.is_absolute():
            return path
        return self.workspace / path

    def _relative_markdown_path(self, status: str, card_id: str) -> str:
        self._validate_status(status)
        return str(Path("action_cards") / status / f"{card_id}.md")

    def _validate_status(self, status: str) -> None:
        if status not in ACTION_CARD_STATUSES:
            raise ActionCardError(f"invalid action card status: {status}")


def _clean_list(value: list[str] | None) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _append_unique(existing: list[str], incoming: list[str]) -> list[str]:
    values = list(existing)
    seen = {item for item in values}
    for item in incoming:
        if item not in seen:
            values.append(item)
            seen.add(item)
    return values
