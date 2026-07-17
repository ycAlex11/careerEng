"""Local event stores for assistant bridge interactions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.storage.jsonl import JSONLStore
from careereng.utils import ensure_dir, make_id, now_iso


class AssistantBridgeStore:
    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.bridge_dir = ensure_dir(self.workspace / "assistant_bridge")
        self.memory_dir = ensure_dir(self.workspace / "memory")
        self.interviews_dir = ensure_dir(self.workspace / "interviews")
        self.intake_events = JSONLStore(self.bridge_dir / "intake_events.jsonl")
        self.action_events = JSONLStore(self.bridge_dir / "action_events.jsonl")
        self.correction_events = JSONLStore(self.bridge_dir / "correction_events.jsonl")
        self.routing_examples = JSONLStore(self.bridge_dir / "routing_examples.jsonl")
        self.profile_signals = JSONLStore(self.memory_dir / "profile_signals.jsonl")
        self.intent_signals = JSONLStore(self.memory_dir / "intent_signals.jsonl")
        self.application_feedback_signals = JSONLStore(self.memory_dir / "application_feedback_signals.jsonl")
        self.interview_events = JSONLStore(self.interviews_dir / "events.jsonl")

    def append_intake_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = {
            "event_id": payload.get("event_id") or make_id("aintake"),
            "created_at": now_iso(),
            **payload,
        }
        self.intake_events.append(row)
        return row

    def append_action_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = {
            "action_event_id": payload.get("action_event_id") or make_id("aaction"),
            "created_at": now_iso(),
            "status": payload.get("status") or "suggested",
            **payload,
        }
        self.action_events.append(row)
        return row

    def append_correction_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = {
            "correction_id": payload.get("correction_id") or make_id("acorrection"),
            "created_at": now_iso(),
            "status": payload.get("status") or "raw",
            **payload,
        }
        self.correction_events.append(row)
        return row

    def append_routing_example(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = {
            "routing_example_id": payload.get("routing_example_id") or make_id("aroute_example"),
            "created_at": now_iso(),
            **payload,
        }
        self.routing_examples.append(row)
        return row

    def append_profile_signal(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = {
            "signal_id": payload.get("signal_id") or make_id("profile_signal"),
            "created_at": now_iso(),
            "status": payload.get("status") or "raw",
            **payload,
        }
        self.profile_signals.append(row)
        return row

    def append_intent_signal(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = {
            "signal_id": payload.get("signal_id") or make_id("intent_signal"),
            "created_at": now_iso(),
            "status": payload.get("status") or "raw",
            **payload,
        }
        self.intent_signals.append(row)
        return row

    def append_application_feedback_signal(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = {
            "signal_id": payload.get("signal_id") or make_id("app_feedback"),
            "created_at": now_iso(),
            "status": payload.get("status") or "raw",
            **payload,
        }
        self.application_feedback_signals.append(row)
        return row

    def append_interview_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = {
            "interview_event_id": payload.get("interview_event_id") or make_id("interview_evt"),
            "created_at": now_iso(),
            "status": payload.get("status") or "raw",
            **payload,
        }
        self.interview_events.append(row)
        return row
