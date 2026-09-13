"""Durable, acknowledged notification batches independent of control events."""

from datetime import datetime
from hashlib import sha256
from pathlib import Path

from careereng.platform.persistence.mutex import workspace_mutex
from careereng.platform.project_state.agent_events import AgentEventStore
from careereng.utils import now_iso, read_json, write_json


class AgentNotificationStore:
    def __init__(self, workspace: Path | str):
        self.events = AgentEventStore(workspace)
        self.path = Path(workspace) / "agent_events" / "notifications.json"
        self.lock = workspace_mutex(self.path)

    def plan(self, *, progress_interval_seconds: int, site_key: str = "",
             batch_id: str = "", observed_at: str = "") -> list[dict]:
        stamp = observed_at or now_iso()
        now = datetime.fromisoformat(stamp)
        with self.lock:
            state = read_json(self.path) or {}
            pending = state.setdefault("pending", {})
            offered = state.setdefault("offered", {})
            last_sent = state.setdefault("last_sent", {})
            listed = self.events.list_events(cursor=str(state.get("cursor") or ""), limit=1000)
            for event in listed["events"]:
                kind = str(event.get("kind") or "")
                urgent = event.get("attention") in {"action_required", "review_required"} or kind in {
                    "site.completed", "batch.completed", "worker.completed", "worker.failed",
                    "evolution.failed", "evolution.resolved",
                }
                if urgent or kind in {"site.phase_advanced", "evolution.requested"}:
                    pending[event["event_id"]] = {**event, "urgent": urgent}
            state["cursor"] = listed["next_cursor"]
            groups: dict[str, list[dict]] = {}
            for event in pending.values():
                if site_key and event.get("site_key") != site_key:
                    continue
                if batch_id and event.get("batch_id") != batch_id:
                    continue
                group = f"{event.get('batch_id', '')}:{event.get('site_key', '')}"
                groups.setdefault(group, []).append(event)
            notifications = []
            for group, events in groups.items():
                urgent = any(event["urgent"] for event in events)
                earliest = min(str(event["created_at"]) for event in events)
                baseline = str(last_sent.get(group) or earliest)
                if not urgent and (now - datetime.fromisoformat(baseline)).total_seconds() < progress_interval_seconds:
                    continue
                ids = sorted(event["event_id"] for event in events)
                delivery_id = "notification_" + sha256("|".join(ids).encode()).hexdigest()[:20]
                offered[delivery_id] = {"event_ids": ids, "group": group}
                notifications.append({"delivery_id": delivery_id, "urgent": urgent, "events": events,
                                      "presentation": {"owner": "main_agent", "channel": "final",
                                                       "acknowledge": "after_final_on_next_turn",
                                                       "instruction": "Summarize current verified facts in a non-empty final reply. Never use commentary alone; no notifications means silence."}})
            write_json(self.path, state)
            return notifications

    def acknowledge(self, delivery_id: str, *, observed_at: str = "", final_response_text: str = "") -> dict:
        with self.lock:
            state = read_json(self.path) or {}
            offered = state.get("offered", {})
            delivery = offered.get(delivery_id)
            if not delivery:
                raise ValueError("unknown notification delivery")
            if delivery.get("acknowledged_at"):
                return dict(delivery)
            stamp = observed_at or now_iso()
            for event_id in delivery["event_ids"]:
                state.get("pending", {}).pop(event_id, None)
            state.setdefault("last_sent", {})[delivery["group"]] = stamp
            delivery["acknowledged_at"] = stamp
            if final_response_text.strip():
                delivery["final_response_text"] = final_response_text.strip()
                delivery["presentation_channel"] = "final"
            write_json(self.path, state)
            return dict(delivery)
