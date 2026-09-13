"""Leased worker-mediated notifications over an external Desktop tool."""

from datetime import datetime, timedelta
from pathlib import Path

from careereng.platform.project_state.notifications import AgentNotificationStore
from careereng.utils import make_id, now_iso, read_json, write_json


class UrgentNotificationRelay:
    def __init__(self, workspace: Path | str):
        self.notifications = AgentNotificationStore(workspace)

    def claim(self, *, worker: dict, expected_control_epoch: int,
              progress_interval_seconds: int = 300, observed_at: str = "") -> dict:
        if int(worker.get("control_epoch", -1)) != expected_control_epoch:
            raise ValueError("obsolete control epoch")
        if worker.get("desired_state") == "cancelled" or worker.get("work_state") == "cancelled":
            return {"status": "cancelled"}
        parent = str(worker.get("parent_agent_id") or "")
        if not parent or parent == worker.get("agent_id"):
            return {"status": "unavailable", "reason": "registered parent task required"}
        if not worker.get("batch_id") or not worker.get("site_key") or not worker.get("agent_id"):
            return {"status": "unavailable", "reason": "complete worker binding required"}
        stamp = observed_at or now_iso()
        now = datetime.fromisoformat(stamp)
        store = self.notifications
        offers = store.plan(progress_interval_seconds=progress_interval_seconds, site_key=worker["site_key"],
                            batch_id=worker["batch_id"], observed_at=stamp)
        urgent_ids = {event["event_id"] for offer in offers if offer["urgent"]
                      for event in offer["events"] if event.get("urgent")}
        with store.lock:
            state = read_json(store.path) or {}
            pending = state.get("pending", {})
            urgent_ids.intersection_update(pending)
            if not urgent_ids:
                return {"status": "empty"}
            group = f"{worker['batch_id']}:{worker['site_key']}:{parent}"
            relay = state.setdefault("relays", {}).setdefault(group, {})
            outstanding = set(relay.get("event_ids", [])) & set(pending)
            new_events = urgent_ids - set(relay.get("event_ids", []))
            if outstanding and not new_events and now < datetime.fromisoformat(relay["retry_after"]):
                return {"status": "deferred", "retry_after": relay["retry_after"]}
            attempts = int(relay.get("attempts", 0)) if outstanding and not new_events else 0
            if attempts >= 3:
                return {"status": "polling_fallback", "reason": "relay attempts exhausted"}
            attempt_id = make_id("urgent_attempt")
            relay.update(attempt_id=attempt_id, event_ids=sorted(urgent_ids),
                         attempts=attempts + 1, status="claimed", work_item_id=worker["work_item_id"],
                         control_epoch=expected_control_epoch, target_thread_id=parent,
                         retry_after=(now + timedelta(seconds=120)).isoformat())
            write_json(store.path, state)
            return {
                "status": "send_required", "attempt_id": attempt_id,
                "target_thread_id": parent, "tool": "send_message_to_thread",
                "message": (
                    f"CareerEng urgent event signal {attempt_id}. "
                    f"Read careereng_monitor_agent_events with batch_id={worker['batch_id']} "
                    f"and site_key={worker['site_key']}. Present currently pending notifications, "
                    "then acknowledge their delivery_id with careereng_ack_notifications. "
                    "If already acknowledged, do not repeat the notification. "
                    "This signal does not authorize restarting or controlling any workflow."
                ),
                "receipt_tool": "careereng_record_urgent_notification_send",
                "retry_after": relay["retry_after"],
            }

    def receipt(self, *, work_item_id: str, attempt_id: str, accepted: bool,
                error: str = "", observed_at: str = "") -> dict:
        store = self.notifications
        with store.lock:
            state = read_json(store.path) or {}
            relay = next((row for row in state.get("relays", {}).values()
                          if row.get("attempt_id") == attempt_id
                          and row.get("work_item_id") == work_item_id), None)
            if relay is None:
                raise ValueError("unknown or superseded relay attempt")
            if relay["status"] != "claimed":
                return dict(relay)
            now = datetime.fromisoformat(observed_at or now_iso())
            relay.update(status="accepted" if accepted else "failed", error=error[:1000],
                         retry_after=(now + timedelta(seconds=300 if accepted else 30)).isoformat())
            write_json(store.path, state)
            return dict(relay)
