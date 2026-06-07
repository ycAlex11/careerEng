"""Cross-run browser workflow memory for site phase context."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from careereng.storage.jsonl import JSONLStore
from careereng.utils import ensure_dir, now_iso, read_json, safe_file_stem, write_json


TERMINAL_STATUSES = {"done", "blocked", "failed", "interrupted"}


class WorkflowMemoryStore:
    """Persist compact site+phase workflow evidence across browser runs."""

    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)

    def path(self, site_key: str) -> Path:
        root = self.workspace / "sites" / safe_file_stem(site_key) / "evolution"
        ensure_dir(root)
        return root / "workflow_memory.json"

    def load(self, site_key: str) -> dict[str, Any]:
        path = self.path(site_key)
        payload = read_json(path)
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("site_key", safe_file_stem(site_key))
        payload.setdefault("updated_at", "")
        payload.setdefault("phases", {})
        if not isinstance(payload.get("phases"), dict):
            payload["phases"] = {}
        return payload

    def update_phase(
        self,
        *,
        site_key: str,
        phase: str,
        status: str,
        batch_id: str = "",
        turn_id: str = "",
        current_url: str = "",
        trace_ref: str = "",
        reason_tag: str = "",
        summary: str = "",
        step_count: int = 0,
        recorded_count: int = 0,
        new_count: int = 0,
    ) -> dict[str, Any]:
        normalized_phase = str(phase or "").strip()
        normalized_status = str(status or "").strip().lower()
        if not normalized_phase or normalized_status not in TERMINAL_STATUSES:
            return self.load(site_key)

        payload = self.load(site_key)
        phases = payload.setdefault("phases", {})
        phase_payload = phases.get(normalized_phase)
        if not isinstance(phase_payload, dict):
            phase_payload = {
                "phase": normalized_phase,
                "success_count": 0,
                "failure_count": 0,
                "blocked_count": 0,
                "interrupted_count": 0,
            }

        event = {
            "status": normalized_status,
            "batch_id": str(batch_id or "").strip(),
            "turn_id": str(turn_id or "").strip(),
            "current_url": str(current_url or "").strip(),
            "trace_ref": str(trace_ref or "").strip(),
            "reason_tag": str(reason_tag or "").strip(),
            "summary": _cap(summary, 700),
            "step_count": int(step_count or 0),
            "recorded_count": int(recorded_count or 0),
            "new_count": int(new_count or 0),
            "updated_at": now_iso(),
        }

        phase_payload["last_status"] = normalized_status
        phase_payload["last_event"] = event
        phase_payload["last_batch_id"] = event["batch_id"]
        phase_payload["last_trace_ref"] = event["trace_ref"]
        phase_payload["last_reason_tag"] = event["reason_tag"]
        phase_payload["last_current_url"] = event["current_url"]
        phase_payload["updated_at"] = event["updated_at"]

        if normalized_status == "done":
            phase_payload["success_count"] = int(phase_payload.get("success_count") or 0) + 1
            phase_payload["latest_success"] = event
            phase_payload["next_recommended_action"] = (
                "Reuse the latest successful phase strategy when the live page still matches; "
                "do not rediscover already proven entry points."
            )
        elif normalized_status == "blocked":
            phase_payload["blocked_count"] = int(phase_payload.get("blocked_count") or 0) + 1
            phase_payload["latest_blocked"] = event
            phase_payload["next_recommended_action"] = (
                "Resume only after the user completes the blocked human-only step, then continue from the live page."
            )
        elif normalized_status == "interrupted":
            phase_payload["interrupted_count"] = int(phase_payload.get("interrupted_count") or 0) + 1
            phase_payload["latest_interrupted"] = event
            phase_payload["known_failed_strategy"] = "The previous run was interrupted before this phase reached a terminal outcome."
            phase_payload["next_recommended_action"] = (
                "Treat the previous attempt as incomplete. Use the latest live page and evidence to continue or choose a new strategy; "
                "do not assume the phase succeeded."
            )
        else:
            phase_payload["failure_count"] = int(phase_payload.get("failure_count") or 0) + 1
            phase_payload["latest_failure"] = event
            phase_payload["known_failed_strategy"] = _known_failed_strategy(event)
            phase_payload["next_recommended_action"] = _next_recommended_action(event)

        recent_events = [item for item in phase_payload.get("recent_events", []) if isinstance(item, dict)]
        recent_events.append(event)
        phase_payload["recent_events"] = recent_events[-5:]
        phases[normalized_phase] = phase_payload
        payload["updated_at"] = event["updated_at"]
        write_json(self.path(site_key), payload)
        return payload

    def context_text(self, *, site_key: str, phase: str, max_bullets: int = 8) -> str:
        payload = self.load(site_key)
        phase_payload = payload.get("phases", {}).get(str(phase or "").strip())
        if not isinstance(phase_payload, dict):
            return ""
        bullets: list[str] = []
        latest_success = phase_payload.get("latest_success") if isinstance(phase_payload.get("latest_success"), dict) else {}
        latest_failure = phase_payload.get("latest_failure") if isinstance(phase_payload.get("latest_failure"), dict) else {}
        latest_blocked = phase_payload.get("latest_blocked") if isinstance(phase_payload.get("latest_blocked"), dict) else {}
        latest_interrupted = (
            phase_payload.get("latest_interrupted") if isinstance(phase_payload.get("latest_interrupted"), dict) else {}
        )

        if latest_success:
            bullets.append(_event_bullet("Latest successful run", latest_success))
        if latest_failure:
            bullets.append(_event_bullet("Latest failed run", latest_failure))
        if latest_blocked:
            bullets.append(_event_bullet("Latest blocked run", latest_blocked))
        if latest_interrupted:
            bullets.append(_event_bullet("Latest interrupted run", latest_interrupted))

        failed_strategy = str(phase_payload.get("known_failed_strategy") or "").strip()
        if failed_strategy:
            bullets.append(f"Known failed strategy: {failed_strategy}")
        next_action = str(phase_payload.get("next_recommended_action") or "").strip()
        if next_action:
            bullets.append(f"Next recommended action: {next_action}")

        counts = (
            f"success={int(phase_payload.get('success_count') or 0)}, "
            f"failed={int(phase_payload.get('failure_count') or 0)}, "
            f"blocked={int(phase_payload.get('blocked_count') or 0)}, "
            f"interrupted={int(phase_payload.get('interrupted_count') or 0)}"
        )
        bullets.append(f"Phase evidence counts: {counts}.")

        if not bullets:
            return ""
        selected = bullets[: max(1, int(max_bullets or 1))]
        lines = [
            f"Previous workflow evidence for site `{safe_file_stem(site_key)}` phase `{phase}`:",
            *[f"- {line}" for line in selected if line],
            (
                "Use this as cross-run memory. It is evidence guidance, not a replacement for the live page, "
                "project Skill, or site Skill."
            ),
        ]
        return "\n".join(lines).strip()


def extract_failure_snapshot_from_trace(
    *,
    workspace: Path | str,
    site_key: str,
    batch_id: str,
    phase: str,
    trace_ref: str,
) -> str:
    """Save the latest useful trace observation as a small failure snapshot."""
    workspace_path = Path(workspace)
    ref = str(trace_ref or "").strip()
    if not ref:
        return ""
    trace_path = workspace_path / ref
    if not trace_path.exists():
        return ""
    rows = JSONLStore(trace_path).read_all()
    selected: dict[str, Any] | None = None
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        tool_name = str(row.get("tool_name") or row.get("tool") or "").strip()
        output = str(row.get("output") or row.get("result") or "").strip()
        if tool_name == "browser_snapshot" or "Page URL:" in output or "### Page" in output:
            selected = row
            break
    if selected is None and rows:
        last = rows[-1]
        selected = last if isinstance(last, dict) else None
    if selected is None:
        return ""

    root = workspace_path / "sites" / safe_file_stem(site_key) / "evolution" / "failure_snapshots"
    ensure_dir(root)
    safe_batch = safe_file_stem(batch_id or "batch")
    safe_phase = safe_file_stem(phase or "phase")
    snapshot_path = root / f"{safe_batch}_{safe_phase}.md"
    content = [
        "# Failure Snapshot",
        "",
        f"- Site: `{safe_file_stem(site_key)}`",
        f"- Batch: `{batch_id}`",
        f"- Phase: `{phase}`",
        f"- Trace: `{trace_ref}`",
        "",
        "## Latest Useful Observation",
        "",
        "```json",
        json.dumps(selected, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    snapshot_path.write_text("\n".join(content), encoding="utf-8")
    try:
        return str(snapshot_path.relative_to(workspace_path))
    except ValueError:
        return str(snapshot_path)


def record_interrupted_batches(
    *,
    workspace: Path | str,
    batches: list[dict[str, Any]],
    reason_tag: str = "batch_cancelled",
) -> None:
    """Record open batch cancellation as incomplete workflow memory."""
    store = WorkflowMemoryStore(workspace)
    for batch in batches:
        if not isinstance(batch, dict):
            continue
        batch_id = str(batch.get("batch_id") or "")
        turn_id = str(batch.get("turn_id") or "")
        sites = batch.get("sites") if isinstance(batch.get("sites"), dict) else {}
        for site_key, row in sites.items():
            if not isinstance(row, dict):
                continue
            status = str(row.get("status") or "").strip()
            if status not in {"queued", "running", "ready", "cancelled"}:
                continue
            phase = _infer_interrupted_phase(row)
            if not phase:
                continue
            store.update_phase(
                site_key=str(site_key or row.get("site_key") or ""),
                phase=phase,
                status="interrupted",
                batch_id=batch_id,
                turn_id=turn_id,
                current_url=str(row.get("current_url") or row.get("entry_url") or ""),
                trace_ref=str(row.get("trace_ref") or ""),
                reason_tag=reason_tag,
                summary=f"Batch was cancelled while site status was `{status}` and phase was `{phase}`.",
            )


def _event_bullet(label: str, event: dict[str, Any]) -> str:
    pieces = [f"{label}: status={event.get('status') or ''}"]
    reason = str(event.get("reason_tag") or "").strip()
    if reason:
        pieces.append(f"reason={reason}")
    batch = str(event.get("batch_id") or "").strip()
    if batch:
        pieces.append(f"batch={batch}")
    url = str(event.get("current_url") or "").strip()
    if url:
        pieces.append(f"url={_cap(url, 140)}")
    summary = str(event.get("summary") or "").strip()
    if summary:
        pieces.append(f"summary={_cap(summary, 220)}")
    return "; ".join(pieces) + "."


def _infer_interrupted_phase(row: dict[str, Any]) -> str:
    current_phase = str(row.get("current_phase") or "").strip()
    if current_phase:
        return current_phase
    apply_payload = row.get("apply") if isinstance(row.get("apply"), dict) else {}
    retrieve_payload = row.get("retrieve") if isinstance(row.get("retrieve"), dict) else {}
    if str(apply_payload.get("status") or "").strip() == "running":
        return "apply"
    if str(retrieve_payload.get("status") or "").strip() == "running":
        return "job_retrieval"
    return ""


def _known_failed_strategy(event: dict[str, Any]) -> str:
    reason = str(event.get("reason_tag") or "").strip()
    url = str(event.get("current_url") or "").strip()
    if reason in {"same_url_no_progress", "same_url_no_progress_tokens"}:
        return (
            "The previous attempt stayed on the same page without durable progress. "
            "Do not repeat unchanged clicks, filter reopening, or observation-only loops on that page."
        )
    if reason == "phase_timeout":
        return "The previous attempt consumed the phase budget. Do not repeat the same long exploration path unchanged."
    if url:
        return f"The previous attempt failed at {_cap(url, 180)}; change strategy or end the phase if success is already visible."
    return "The previous attempt failed; change strategy instead of repeating the same unchanged path."


def _next_recommended_action(event: dict[str, Any]) -> str:
    reason = str(event.get("reason_tag") or "").strip()
    if reason in {"same_url_no_progress", "same_url_no_progress_tokens"}:
        return (
            "Inspect the current page for the phase success signal first. If it is already satisfied, call phase_result done; "
            "otherwise try one different route described by the active Skills."
        )
    if reason == "phase_timeout":
        return "Start with the shortest visible route to the phase success signal and record evidence before exploring alternatives."
    return "Use the latest live page plus active Skills to choose a different strategy, then record evidence or stop cleanly."


def _cap(value: Any, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 14)].rstrip() + "...[truncated]"
