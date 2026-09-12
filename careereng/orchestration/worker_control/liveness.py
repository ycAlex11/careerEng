"""Objective activity evidence, separate from business progress and intent."""

from datetime import datetime
from typing import Any


def last_activity(worker: dict[str, Any]) -> str:
    return max(
        str(worker.get("last_heartbeat_at") or ""),
        str(worker.get("last_activity_at") or ""),
    ) or str(worker.get("registered_at") or "")


def has_recent_activity(worker: dict[str, Any], now: datetime, idle_seconds: int, inflight_seconds: int) -> bool:
    stamp = last_activity(worker)
    if stamp and (now - datetime.fromisoformat(stamp)).total_seconds() < idle_seconds:
        return True
    return any(
        (now - datetime.fromisoformat(started)).total_seconds() < inflight_seconds
        for started in (worker.get("inflight_operations") or {}).values()
    )


def obsolete_recovery(worker: dict[str, Any], payload: dict[str, Any]) -> bool:
    if "expected_activity_revision" not in payload:
        return False
    return (
        int(payload["expected_activity_revision"]) != int(worker.get("activity_revision") or 0)
        or worker.get("work_state") != "running"
        or worker.get("desired_state") != "running"
    )
