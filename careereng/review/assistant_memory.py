"""Review pack builder for assistant router and career-memory intake."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from careereng.review.pack import create_review_pack
from careereng.review.schema import ReviewPack
from careereng.storage.jsonl import JSONLStore
from careereng.utils import read_json


SAMPLE_LIMIT = 10


def build_assistant_memory_review_pack(
    *,
    workspace: Path | str,
    run_payload: dict[str, Any],
    applied_at: str,
    sample_limit: int = SAMPLE_LIMIT,
) -> ReviewPack:
    workspace_path = Path(workspace)
    intake_path = workspace_path / "assistant_bridge" / "intake_events.jsonl"
    routing_examples_path = workspace_path / "assistant_bridge" / "routing_examples.jsonl"
    corrections_path = workspace_path / "assistant_bridge" / "correction_events.jsonl"
    action_events_path = workspace_path / "assistant_bridge" / "action_events.jsonl"
    intake_state_path = workspace_path / "assistant_bridge" / "intake_state.json"
    context_path = workspace_path / "assistant_bridge" / "context" / "latest.md"
    memory_units_path = workspace_path / "memory" / "memory_units.jsonl"
    taskboard_current_path = workspace_path / "taskboard" / "current.md"

    intake_rows = _after(_read_jsonl(intake_path), applied_at, fields=("created_at",))
    routing_rows = _after(_read_jsonl(routing_examples_path), applied_at, fields=("created_at",))
    correction_rows = _after(_read_jsonl(corrections_path), applied_at, fields=("created_at",))
    action_rows = _after(_read_jsonl(action_events_path), applied_at, fields=("created_at",))
    memory_rows = _after(_read_jsonl(memory_units_path), applied_at, fields=("created_at", "updated_at"))

    duplicate_groups = _duplicate_memory_groups(memory_rows)
    codex_imported_rows = [row for row in memory_rows if _is_codex_imported_memory(row)]
    explicit_intake_count = sum(1 for row in intake_rows if bool(row.get("explicit_trigger")) or str(row.get("trigger_mode") or "") == "explicit")
    promoted_event_ids = {str(row.get("source_event_id") or "") for row in memory_rows if str(row.get("source_event_id") or "")}
    explicit_promoted_count = sum(1 for row in intake_rows if str(row.get("event_id") or "") in promoted_event_ids)
    window_mode = "after_applied_at" if applied_at else "all_current_evidence"
    intake_state = read_json(intake_state_path)
    candidate_file = str(intake_state.get("last_candidate_file") or "").strip()

    metrics = {
        "window": {"mode": window_mode, "applied_at": applied_at},
        "intake_count": len(intake_rows),
        "explicit_intake_count": explicit_intake_count,
        "routing_example_count": len(routing_rows),
        "action_event_count": len(action_rows),
        "memory_unit_count": len(memory_rows),
        "codex_imported_memory_count": len(codex_imported_rows),
        "correction_count": len(correction_rows),
        "category_distribution": dict(sorted(Counter(str(row.get("category") or row.get("data_category") or "unknown") for row in memory_rows).items())),
        "memory_status_distribution": dict(sorted(Counter(str(row.get("status") or "unknown") for row in memory_rows).items())),
        "duplicate_group_count": len(duplicate_groups),
        "duplicate_memory_count": sum(max(0, len(rows) - 1) for rows in duplicate_groups),
        "explicit_intake_promoted_count": explicit_promoted_count,
        "explicit_intake_to_memory_proxy": round(explicit_promoted_count / explicit_intake_count, 4) if explicit_intake_count else 0,
        "correction_to_memory_ratio": round(len(correction_rows) / len(memory_rows), 4) if memory_rows else 0,
    }

    trigger = run_payload.get("trigger") if isinstance(run_payload.get("trigger"), dict) else {}
    sections = [
        {
            "title": "Trigger",
            "body": str(trigger.get("reason") or "No trigger reason found in run archive."),
            "rows": [trigger] if trigger else [],
        },
        {
            "title": "Review Scope",
            "body": (
                "This pack reviews assistant routing and career-memory intake behavior from the selected evidence window. "
                "It is intentionally review-only: it does not accept, reject, or rollback the run automatically."
            ),
        },
    ]
    if intake_state:
        sections.append(
            {
                "title": "Recent Conversation Intake State",
                "body": "Latest recorded recent-N conversation intake state.",
                "rows": [intake_state],
            }
        )
    evidence_refs = _existing_refs(
        workspace_path,
        [
            intake_path,
            routing_examples_path,
            corrections_path,
            action_events_path,
            intake_state_path,
            context_path,
            memory_units_path,
            taskboard_current_path,
            workspace_path / candidate_file if candidate_file else None,
        ],
    )
    sample_rows = {
        "recent_intake_events": _compact_rows(intake_rows[-sample_limit:], kind="intake"),
        "recent_memory_units": _compact_rows(memory_rows[-sample_limit:], kind="memory"),
        "codex_imported_memory_units": _compact_rows(codex_imported_rows[-sample_limit:], kind="memory"),
        "correction_events": _compact_rows(correction_rows[-sample_limit:], kind="correction"),
        "possible_duplicate_memory_groups": _compact_duplicate_groups(duplicate_groups[:sample_limit]),
    }

    return create_review_pack(
        review_type="assistant_router_memory_intake",
        subject_id=str(run_payload.get("run_id") or ""),
        subject_ref=str(run_payload.get("candidate_id") or "assistant_router_memory_intake"),
        metrics=metrics,
        sections=sections,
        sample_rows=sample_rows,
        review_questions=_assistant_memory_review_questions(),
        evidence_refs=evidence_refs,
    )


def _assistant_memory_review_questions() -> list[str]:
    return [
        "Do the sampled memory units have clear source evidence?",
        "Are categories such as profile_resume_signal, career_intent_strategy, application_feedback, and interview_record assigned correctly?",
        "Are summary, facts, entities, and tags useful for later resume/profile, application strategy, target-company intelligence, or interview preparation work?",
        "Did CareerEng save ordinary development chatter, temporary commands, or process-control messages that should have been suppressed?",
        "Did the evidence suggest obvious high-value career content that was not promoted into memory?",
        "Do correction events indicate a route, category, save policy, or curation rule that should be adjusted?",
        "Should the next status be accepted, keep_observing, low_confidence, rejected, or rollback_recommended?",
    ]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return JSONLStore(path).read_all() if path.exists() else []


def _after(rows: list[dict[str, Any]], boundary: str, *, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    if not boundary:
        return rows
    return [row for row in rows if _is_after_or_equal(_first_text(row, fields), boundary)]


def _first_text(row: dict[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = str(row.get(field) or "").strip()
        if value:
            return value
    return ""


def _is_after_or_equal(ts: str, boundary: str) -> bool:
    if not boundary:
        return True
    left = _parse_ts(ts)
    right = _parse_ts(boundary)
    if left is None or right is None:
        return ts >= boundary
    try:
        return left >= right
    except TypeError:
        return ts >= boundary


def _parse_ts(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duplicate_memory_groups(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = str(row.get("dedupe_key") or "").strip()
        if not key:
            key = "|".join(
                [
                    str(row.get("category") or "").strip(),
                    str(row.get("summary") or "").strip().lower(),
                    str(row.get("source_text") or "").strip().lower()[:180],
                ]
            )
        if key.strip("|"):
            grouped[key].append(row)
    return [items for items in grouped.values() if len(items) > 1]


def _is_codex_imported_memory(row: dict[str, Any]) -> bool:
    if str(row.get("source_thread_id") or "").strip():
        return True
    source_path = str(row.get("source_path") or "").strip()
    raw_signal_paths = {
        "memory/profile_signals.jsonl",
        "memory/intent_signals.jsonl",
        "memory/application_feedback_signals.jsonl",
        "interviews/events.jsonl",
        "assistant_bridge/correction_events.jsonl",
    }
    return bool(source_path and source_path not in raw_signal_paths)


def _compact_rows(rows: list[dict[str, Any]], *, kind: str) -> list[dict[str, Any]]:
    return [_compact_row(row, kind=kind) for row in rows]


def _compact_row(row: dict[str, Any], *, kind: str) -> dict[str, Any]:
    if kind == "memory":
        return {
            "memory_id": row.get("memory_id"),
            "created_at": row.get("created_at"),
            "category": row.get("category"),
            "status": row.get("status"),
            "summary": row.get("summary"),
            "tags": row.get("tags") if isinstance(row.get("tags"), list) else [],
            "entities": row.get("entities") if isinstance(row.get("entities"), dict) else {},
            "source_event_id": row.get("source_event_id"),
            "source_signal_id": row.get("source_signal_id"),
            "source_thread_id": row.get("source_thread_id"),
            "source_text": _clip(str(row.get("source_text") or ""), 500),
        }
    if kind == "correction":
        return {
            "correction_id": row.get("correction_id"),
            "created_at": row.get("created_at"),
            "intake_event_id": row.get("intake_event_id"),
            "wrong_route": row.get("wrong_route"),
            "correct_route": row.get("correct_route"),
            "user_correction": row.get("user_correction"),
            "confidence": row.get("confidence"),
        }
    return {
        "event_id": row.get("event_id"),
        "created_at": row.get("created_at"),
        "trigger_mode": row.get("trigger_mode"),
        "data_category": row.get("data_category"),
        "route": row.get("route"),
        "semantic_labels": row.get("semantic_labels") if isinstance(row.get("semantic_labels"), list) else [],
        "suggested_action": row.get("suggested_action"),
        "normalized_message": _clip(str(row.get("normalized_message") or ""), 500),
        "reason": row.get("reason"),
    }


def _compact_duplicate_groups(groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rows in groups:
        out.append(
            {
                "count": len(rows),
                "memory_ids": [str(row.get("memory_id") or "") for row in rows],
                "category": str(rows[0].get("category") or "") if rows else "",
                "summary": str(rows[0].get("summary") or "") if rows else "",
            }
        )
    return out


def _clip(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _existing_refs(root: Path, paths: list[Path | None]) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if path is None or not path.exists():
            continue
        ref = _rel(root, path)
        if ref in seen:
            continue
        seen.add(ref)
        refs.append(ref)
    return refs
