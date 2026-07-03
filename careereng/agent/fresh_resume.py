"""Fresh snapshot resume planning for interrupted browser phases."""

from __future__ import annotations

from typing import Any


def _text_contains_url_or_ref(*, text: str, ref: str) -> bool:
    normalized_text = str(text or "").strip()
    normalized_ref = str(ref or "").strip()
    if not normalized_text or not normalized_ref:
        return False
    return normalized_text == normalized_ref or normalized_text in normalized_ref or normalized_ref in normalized_text


def _remaining_phases_from(phase_slug: str, phases: tuple[str, ...]) -> tuple[str, ...]:
    normalized = str(phase_slug or "").strip()
    if not normalized:
        return phases
    try:
        index = tuple(phases).index(normalized)
    except ValueError:
        return phases
    return tuple(phases[index:])


def _fresh_resume_apply_target(
    *,
    current: dict[str, Any],
    browser_session: dict[str, Any],
    run_rows: list[dict[str, Any]],
) -> dict[str, str]:
    apply_payload = current.get("apply") if isinstance(current.get("apply"), dict) else {}
    loop_payload = apply_payload.get("loop_control") if isinstance(apply_payload.get("loop_control"), dict) else {}
    current_item_ref = str(loop_payload.get("current_item_ref") or "").strip()
    current_url = str(
        browser_session.get("last_known_url")
        or current.get("current_url")
        or loop_payload.get("current_item_ref")
        or current.get("entry_url")
        or ""
    ).strip()

    def matches(row: dict[str, Any]) -> bool:
        job_id = str(row.get("job_id") or "").strip()
        url = str(row.get("url") or "").strip()
        refs = [current_item_ref, current_url]
        return any(
            (job_id and _text_contains_url_or_ref(text=ref, ref=job_id))
            or (url and _text_contains_url_or_ref(text=ref, ref=url))
            for ref in refs
            if str(ref or "").strip()
        )

    matches_by_ref = [row for row in run_rows if isinstance(row, dict) and matches(row)]
    if len(matches_by_ref) == 1:
        row = matches_by_ref[0]
        return {
            "job_id": str(row.get("job_id") or ""),
            "job_url": str(row.get("url") or ""),
            "entry_url": current_url or current_item_ref or str(row.get("url") or ""),
            "title": str(row.get("title") or ""),
        }

    blocked_rows = [
        row
        for row in run_rows
        if isinstance(row, dict)
        and str(row.get("application_status") or "").strip().lower() == "blocked"
        and str(row.get("apply_state") or "").strip().lower() == "terminal_blocked"
    ]
    if len(blocked_rows) == 1:
        row = blocked_rows[0]
        return {
            "job_id": str(row.get("job_id") or ""),
            "job_url": str(row.get("url") or ""),
            "entry_url": current_url or current_item_ref or str(row.get("url") or ""),
            "title": str(row.get("title") or ""),
        }
    return {}


def build_fresh_snapshot_resume_plan(
    *,
    site_key: str,
    current: dict[str, Any],
    batch: dict[str, Any],
    message: str,
    phase_plan: tuple[str, ...],
    browser_session: dict[str, Any],
    run_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    batch_id = str(batch.get("batch_id") or "")
    browser_resume_phase = str(browser_session.get("resume_phase") or "").strip()
    current_phase = str(current.get("current_phase") or browser_resume_phase or "").strip()
    if current_phase == "idle":
        current_phase = ""
    if not current_phase:
        current_phase = phase_plan[0] if phase_plan else ""

    apply_payload = current.get("apply") if isinstance(current.get("apply"), dict) else {}
    apply_target: dict[str, str] = {}
    if current_phase == "apply" or str(apply_payload.get("status") or "").strip() in {"blocked", "waiting_user"}:
        current_phase = "apply"
        apply_target = _fresh_resume_apply_target(
            current=current,
            browser_session=browser_session,
            run_rows=run_rows,
        )

    phase_slugs = ("apply",) if current_phase == "apply" else _remaining_phases_from(current_phase, phase_plan)
    if not phase_slugs and current_phase:
        phase_slugs = (current_phase,)
    entry_url = str(
        apply_target.get("entry_url")
        or browser_session.get("last_known_url")
        or current.get("current_url")
        or current.get("entry_url")
        or ""
    )
    continuation_context = {
        "kind": "fresh_snapshot_resume",
        "site_key": site_key,
        "batch_id": batch_id,
        "phase": current_phase,
        "phase_slugs": list(phase_slugs),
        "user_message": str(message or ""),
        "site_status": str(current.get("status") or ""),
        "browser_pending_action": str(browser_session.get("pending_action") or ""),
        "last_known_url": str(browser_session.get("last_known_url") or ""),
        "current_url": str(current.get("current_url") or ""),
        "instruction": (
            "The user says the manual browser step is done. Start from the current live page with a fresh "
            "snapshot and continue this phase. Do not infer a terminal business outcome from this resume signal alone."
        ),
    }
    if apply_target:
        continuation_context["apply_target"] = {
            "job_id": apply_target.get("job_id", ""),
            "job_url": apply_target.get("job_url", ""),
            "title": apply_target.get("title", ""),
        }
    return {
        "phase_slugs": phase_slugs,
        "entry_url": entry_url,
        "apply_target_job_ids": (apply_target["job_id"],) if apply_target.get("job_id") else None,
        "continuation_context": continuation_context,
    }
