"""Typed career memory units built from assistant bridge signals."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from careereng.integrations.assistant_bridge.schema import (
    DATA_CATEGORY_APPLICATION_FEEDBACK,
    DATA_CATEGORY_CAREER_INTENT_STRATEGY,
    DATA_CATEGORY_CORRECTION,
    DATA_CATEGORY_EVOLUTION_LESSON,
    DATA_CATEGORY_INTERVIEW_RECORD,
    DATA_CATEGORY_PROFILE_RESUME_SIGNAL,
    DATA_CATEGORIES,
)
from careereng.storage.jsonl import JSONLStore
from careereng.utils import ensure_dir, make_id, now_iso


PROMOTABLE_CATEGORIES = {
    DATA_CATEGORY_PROFILE_RESUME_SIGNAL,
    DATA_CATEGORY_CAREER_INTENT_STRATEGY,
    DATA_CATEGORY_APPLICATION_FEEDBACK,
    DATA_CATEGORY_INTERVIEW_RECORD,
    DATA_CATEGORY_CORRECTION,
}
IMPORTABLE_CANDIDATE_CATEGORIES = {*PROMOTABLE_CATEGORIES, DATA_CATEGORY_EVOLUTION_LESSON}

SIGNAL_SPECS = (
    (DATA_CATEGORY_PROFILE_RESUME_SIGNAL, Path("memory/profile_signals.jsonl"), "signal_id"),
    (DATA_CATEGORY_CAREER_INTENT_STRATEGY, Path("memory/intent_signals.jsonl"), "signal_id"),
    (DATA_CATEGORY_APPLICATION_FEEDBACK, Path("memory/application_feedback_signals.jsonl"), "signal_id"),
    (DATA_CATEGORY_INTERVIEW_RECORD, Path("interviews/events.jsonl"), "interview_event_id"),
    (DATA_CATEGORY_CORRECTION, Path("assistant_bridge/correction_events.jsonl"), "correction_id"),
)

MAX_SUMMARY_CHARS = 220
MAX_TEXT_CHARS = 2000


class CareerMemoryError(RuntimeError):
    """Raised when career memory input cannot be validated."""


class CareerMemoryStore:
    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.memory_dir = ensure_dir(self.workspace / "memory")
        self.units = JSONLStore(self.memory_dir / "memory_units.jsonl")

    def read_units(self) -> list[dict[str, Any]]:
        return self.units.read_all()

    def write_units(self, rows: list[dict[str, Any]]) -> None:
        self.units.write_all(rows)

    def append_units(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            self.units.append(row)


def promote_assistant_signals(*, workspace: Path | str, limit: int | None = None) -> dict[str, Any]:
    """Promote existing assistant bridge signal rows into unified memory units."""

    workspace_path = Path(workspace)
    store = CareerMemoryStore(workspace_path)
    existing = store.read_units()
    existing_keys = {_dedupe_key(row) for row in existing if _dedupe_key(row)}

    created: list[dict[str, Any]] = []
    skipped = 0
    scanned = 0
    max_rows = int(limit or 0)

    for category, relative_path, id_field in SIGNAL_SPECS:
        path = workspace_path / relative_path
        rows = JSONLStore(path).read_all() if path.exists() else []
        for row in rows:
            scanned += 1
            unit = _unit_from_signal(
                category=category,
                source_path=relative_path.as_posix(),
                id_field=id_field,
                row=row,
            )
            key = _dedupe_key(unit)
            if not key or key in existing_keys:
                skipped += 1
                continue
            existing_keys.add(key)
            created.append(unit)
            if max_rows > 0 and len(created) >= max_rows:
                break
        if max_rows > 0 and len(created) >= max_rows:
            break

    store.append_units(created)
    return {
        "workspace": str(workspace_path),
        "memory_units_path": str(store.units.path),
        "scanned": scanned,
        "created": len(created),
        "skipped_existing": skipped,
        "memory_ids": [str(row.get("memory_id") or "") for row in created],
    }


def import_memory_candidates(
    *,
    workspace: Path | str,
    input_path: Path | str,
    source_limit: int | None = None,
    source_thread: str = "",
    source_client: str = "",
) -> dict[str, Any]:
    """Import Codex-curated memory candidates after schema validation."""

    workspace_path = Path(workspace)
    path = Path(input_path)
    if not path.exists():
        raise CareerMemoryError(f"candidate input file not found: {path}")
    normalized_source_limit = _normalize_source_limit(source_limit)
    normalized_source_thread = str(source_thread or "").strip()
    normalized_source_client = str(source_client or "").strip()

    candidates = _read_candidate_file(path)
    store = CareerMemoryStore(workspace_path)
    existing = store.read_units()
    existing_keys = {_dedupe_key(row) for row in existing if _dedupe_key(row)}

    created: list[dict[str, Any]] = []
    created_lessons: list[dict[str, Any]] = []
    created_evidence: list[dict[str, Any]] = []
    skipped = 0
    for idx, candidate in enumerate(candidates, 1):
        enriched_candidate = _candidate_with_import_metadata(
            candidate,
            source_path=str(path),
            source_limit=normalized_source_limit,
            source_thread=normalized_source_thread,
            source_client=normalized_source_client,
        )
        category = str(enriched_candidate.get("category") or "").strip()
        if category not in IMPORTABLE_CANDIDATE_CATEGORIES:
            raise CareerMemoryError(f"candidate #{idx} has unsupported category: {category or '<empty>'}")
        if category == DATA_CATEGORY_EVOLUTION_LESSON:
            lesson, created_flag = _append_evolution_lesson_from_candidate(
                workspace=workspace_path,
                candidate=enriched_candidate,
                source_path=str(path),
                index=idx,
            )
            if not created_flag:
                skipped += 1
                continue
            lesson_row = lesson.to_dict()
            created_lessons.append(lesson_row)
            evidence = _evolution_evidence_from_lesson(lesson_row, source_path=str(path), index=idx)
            if _append_unique_evolution_evidence(workspace_path, evidence):
                created_evidence.append(evidence)
            continue
        unit = _unit_from_candidate(enriched_candidate, source_path=str(path), index=idx)
        key = _dedupe_key(unit)
        if not key or key in existing_keys:
            skipped += 1
            continue
        existing_keys.add(key)
        created.append(unit)

    store.append_units(created)
    return {
        "workspace": str(workspace_path),
        "memory_units_path": str(store.units.path),
        "input_path": str(path),
        "source_client": normalized_source_client,
        "source_thread": normalized_source_thread,
        "source_limit": normalized_source_limit,
        "read": len(candidates),
        "created": len(created),
        "created_lessons": len(created_lessons),
        "created_evolution_evidence": len(created_evidence),
        "skipped_existing": skipped,
        "memory_ids": [str(row.get("memory_id") or "") for row in created],
        "lesson_ids": [str(row.get("lesson_id") or "") for row in created_lessons],
        "evidence_ids": [str(row.get("evidence_id") or "") for row in created_evidence],
    }


def list_memory_units(
    *,
    workspace: Path | str,
    category: str = "",
    status: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    rows = CareerMemoryStore(workspace).read_units()
    if category:
        rows = [row for row in rows if str(row.get("category") or "") == category]
    if status:
        rows = [row for row in rows if str(row.get("status") or "") == status]
    rows = sorted(rows, key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""), reverse=True)
    return rows[: max(0, int(limit))]


def show_memory_unit(*, workspace: Path | str, memory_id: str) -> dict[str, Any]:
    wanted = str(memory_id or "").strip()
    if not wanted:
        raise CareerMemoryError("memory_id is required")
    for row in CareerMemoryStore(workspace).read_units():
        if str(row.get("memory_id") or "") == wanted:
            return row
    raise CareerMemoryError(f"memory unit not found: {wanted}")


def _unit_from_signal(*, category: str, source_path: str, id_field: str, row: dict[str, Any]) -> dict[str, Any]:
    now = now_iso()
    source_signal_id = str(row.get(id_field) or "")
    source_event_id = str(row.get("intake_event_id") or "")
    source_text = _source_text(row)
    semantic_labels = _string_list(row.get("semantic_labels"))
    entities = _dict(row.get("detected_entities"))
    facts = _facts_from_signal(category=category, row=row)
    tags = _tags_for_category(category, semantic_labels)
    summary = _summary_from_parts(source_text, facts=facts)

    return {
        "memory_id": make_id("memory"),
        "created_at": now,
        "updated_at": now,
        "category": category,
        "source_event_id": source_event_id,
        "source_signal_id": source_signal_id,
        "source_path": source_path,
        "source_text": _clip(source_text, MAX_TEXT_CHARS),
        "summary": summary,
        "facts": facts,
        "entities": entities,
        "confidence": _float(row.get("confidence"), default=0.5),
        "status": _memory_status_for_category(category),
        "tags": tags,
        "supersedes": [],
        "evidence_refs": _evidence_refs(source_path=source_path, source_event_id=source_event_id, source_signal_id=source_signal_id),
        "dedupe_key": _stable_key(
            "signal",
            category,
            source_path,
            source_signal_id,
            source_event_id,
            source_text,
        ),
    }


def _unit_from_candidate(candidate: dict[str, Any], *, source_path: str, index: int) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise CareerMemoryError(f"candidate #{index} must be an object")

    category = str(candidate.get("category") or "").strip()
    if category not in PROMOTABLE_CATEGORIES:
        raise CareerMemoryError(f"candidate #{index} has unsupported category: {category or '<empty>'}")

    summary = str(candidate.get("summary") or "").strip()
    evidence_text = str(candidate.get("evidence_text") or candidate.get("source_text") or "").strip()
    if not summary:
        summary = _clip(evidence_text, MAX_SUMMARY_CHARS)
    if not summary:
        raise CareerMemoryError(f"candidate #{index} must include summary or evidence_text")

    now = now_iso()
    source_thread_id = str(candidate.get("source_thread_id") or "").strip()
    source_event_id = str(candidate.get("source_event_id") or "").strip()
    source_signal_id = str(candidate.get("source_signal_id") or "").strip()
    facts = _dict(candidate.get("facts"))
    entities = _dict(candidate.get("entities"))
    tags = _string_list(candidate.get("tags"))
    semantic_labels = _string_list(candidate.get("semantic_labels"))
    confidence = _float(candidate.get("confidence"), default=0.7)
    status = str(candidate.get("status") or "active").strip() or "active"

    return {
        "memory_id": str(candidate.get("memory_id") or "").strip() or make_id("memory"),
        "created_at": str(candidate.get("created_at") or "").strip() or now,
        "updated_at": str(candidate.get("updated_at") or "").strip() or now,
        "category": category,
        "source_event_id": source_event_id,
        "source_signal_id": source_signal_id,
        "source_thread_id": source_thread_id,
        "source_path": source_path,
        "source_text": _clip(evidence_text, MAX_TEXT_CHARS),
        "summary": _clip(summary, MAX_SUMMARY_CHARS),
        "facts": facts,
        "entities": entities,
        "confidence": confidence,
        "status": status,
        "tags": sorted(set(_tags_for_category(category, semantic_labels) + tags)),
        "supersedes": _string_list(candidate.get("supersedes")),
        "evidence_refs": _candidate_evidence_refs(candidate, source_path=source_path),
        "dedupe_key": _stable_key(
            "candidate",
            category,
            source_thread_id,
            source_event_id,
            source_signal_id,
            summary,
            evidence_text,
        ),
    }


def _normalize_source_limit(source_limit: int | None) -> int:
    try:
        value = int(source_limit or 0)
    except Exception as exc:
        raise CareerMemoryError("source_limit must be an integer") from exc
    if value < 0:
        raise CareerMemoryError("source_limit must be >= 0")
    return value


def _candidate_with_import_metadata(
    candidate: dict[str, Any],
    *,
    source_path: str,
    source_limit: int,
    source_thread: str,
    source_client: str,
) -> dict[str, Any]:
    if not any((source_limit, source_thread, source_client)):
        return candidate

    enriched = dict(candidate)
    if source_thread:
        enriched["source_thread_id"] = source_thread

    facts = _dict(enriched.get("facts"))
    if source_limit:
        facts["source_message_limit"] = source_limit
    if source_thread:
        facts["source_thread_id"] = source_thread
    if source_client:
        facts["source_client"] = source_client
    if facts:
        enriched["facts"] = facts

    refs = [dict(ref) for ref in enriched.get("evidence_refs", []) if isinstance(ref, dict)]
    import_ref: dict[str, Any] = {"source_path": source_path}
    if source_limit:
        import_ref["scope"] = f"recent_{source_limit}_messages"
    if source_thread:
        import_ref["source_thread_id"] = source_thread
    if source_client:
        import_ref["source_client"] = source_client
    import_ref["note"] = "Imported from assistant-curated recent conversation candidates."
    if import_ref not in refs:
        refs.append(import_ref)
    enriched["evidence_refs"] = refs

    tags = set(_string_list(enriched.get("tags")))
    if source_client:
        tags.add(f"{safe_tag(source_client)}_curated")
    if source_limit:
        tags.add("recent_thread_import")
    if tags:
        enriched["tags"] = sorted(tags)
    return enriched


def _append_evolution_lesson_from_candidate(
    *,
    workspace: Path,
    candidate: dict[str, Any],
    source_path: str,
    index: int,
) -> tuple[Any, bool]:
    from careereng.evolution.browser_control.lessons import BrowserControlLessonStore

    summary = str(candidate.get("summary") or "").strip()
    evidence_text = str(candidate.get("evidence_text") or candidate.get("source_text") or "").strip()
    if not summary:
        summary = _clip(evidence_text, MAX_SUMMARY_CHARS)
    if not summary:
        raise CareerMemoryError(f"candidate #{index} must include summary or evidence_text")

    facts = _dict(candidate.get("facts"))
    evidence_origin = _dict(candidate.get("evidence_origin"))
    if not evidence_origin:
        evidence_origin = _dict(facts.get("evidence_origin"))
    source_thread_id = str(candidate.get("source_thread_id") or facts.get("source_thread_id") or "").strip()
    source_client = str(facts.get("source_client") or "").strip()
    if source_thread_id and "source_thread_id" not in evidence_origin:
        evidence_origin["source_thread_id"] = source_thread_id
    if source_client and "source_client" not in evidence_origin:
        evidence_origin["source_client"] = source_client

    payload = {
        "lesson_id": str(candidate.get("lesson_id") or "").strip(),
        "created_at": str(candidate.get("created_at") or "").strip(),
        "status": str(candidate.get("status") or "accepted").strip() or "accepted",
        "phase": str(candidate.get("phase") or facts.get("phase") or "").strip(),
        "lesson_type": str(candidate.get("lesson_type") or facts.get("lesson_type") or "conversation_curated").strip(),
        "summary": summary,
        "rationale": str(candidate.get("rationale") or evidence_text or "").strip(),
        "evidence_origin": evidence_origin,
        "applicability_scope": str(candidate.get("applicability_scope") or facts.get("applicability_scope") or "site_skill_evolution").strip(),
        "applicability_tags": _string_list(
            candidate.get("applicability_tags")
            or facts.get("applicability_tags")
            or candidate.get("applies_to")
            or candidate.get("future_use")
        ),
        "source_evidence_ids": _string_list(candidate.get("source_evidence_ids")),
        "source_candidate_ids": _string_list(candidate.get("source_candidate_ids")),
        "source_run_ids": _string_list(candidate.get("source_run_ids")),
        "evidence_refs": _candidate_evidence_refs(candidate, source_path=source_path),
        "avoid_patterns": _string_list(candidate.get("avoid_patterns") or facts.get("avoid_patterns")),
        "recommended_patterns": _string_list(candidate.get("recommended_patterns") or facts.get("recommended_patterns")),
        "dedupe_key": str(candidate.get("dedupe_key") or "").strip(),
    }
    return BrowserControlLessonStore(workspace).append_unique(payload)


def _evolution_evidence_from_lesson(lesson: dict[str, Any], *, source_path: str, index: int) -> dict[str, Any]:
    origin = lesson.get("evidence_origin") if isinstance(lesson.get("evidence_origin"), dict) else {}
    evidence_id = "evidence_" + _stable_key(
        "evolution_lesson",
        lesson.get("lesson_id"),
        lesson.get("dedupe_key"),
        source_path,
        index,
    )
    return {
        "evidence_id": evidence_id,
        "created_at": now_iso(),
        "source_type": "evolution_lesson",
        "source_ref": str(lesson.get("lesson_id") or source_path),
        "area": "browser_control",
        "site_key": str(origin.get("site_key") or ""),
        "phase": str(lesson.get("phase") or origin.get("phase") or ""),
        "event_type": "accepted_lesson",
        "severity": "info",
        "summary": str(lesson.get("summary") or ""),
        "details": {
            "lesson_id": lesson.get("lesson_id"),
            "applicability_scope": lesson.get("applicability_scope") or lesson.get("scope"),
            "applicability_tags": lesson.get("applicability_tags") or lesson.get("applies_to") or [],
            "evidence_origin": origin,
            "source_path": source_path,
        },
        "entities": {
            "lesson_id": lesson.get("lesson_id"),
            "origin_site_key": origin.get("site_key") or "",
        },
        "tags": ["evolution_lesson", "browser_control", str(lesson.get("applicability_scope") or "site_skill_evolution")],
        "fingerprint": str(lesson.get("dedupe_key") or ""),
    }


def _append_unique_evolution_evidence(workspace: Path, row: dict[str, Any]) -> bool:
    path = workspace / "evolution" / "evidence" / "all.jsonl"
    store = JSONLStore(path)
    wanted = str(row.get("evidence_id") or "").strip()
    if wanted and any(str(existing.get("evidence_id") or "").strip() == wanted for existing in store.read_all()):
        return False
    store.append(row)
    return True


def _read_candidate_file(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise CareerMemoryError(f"invalid JSON candidate file: {exc}") from exc
        if not isinstance(data, list):
            raise CareerMemoryError("candidate JSON file must contain a list")
        if not all(isinstance(item, dict) for item in data):
            raise CareerMemoryError("candidate JSON list must contain objects")
        return list(data)

    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CareerMemoryError(f"invalid JSONL candidate line {line_no}: {exc}") from exc
        if not isinstance(data, dict):
            raise CareerMemoryError(f"candidate line {line_no} must be an object")
        rows.append(data)
    return rows


def _source_text(row: dict[str, Any]) -> str:
    for key in ("source_text", "evidence", "content", "user_correction"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _facts_from_signal(*, category: str, row: dict[str, Any]) -> dict[str, Any]:
    skip = {
        "signal_id",
        "interview_event_id",
        "correction_id",
        "created_at",
        "updated_at",
        "status",
        "intake_event_id",
        "source_text",
        "semantic_labels",
        "detected_entities",
        "confidence",
        "candidate_patch",
    }
    facts: dict[str, Any] = {"source_category": category}
    for key, value in row.items():
        if key in skip or value in (None, "", [], {}):
            continue
        if isinstance(value, (str, int, float, bool, list, dict)):
            facts[key] = value
    return facts


def _summary_from_parts(source_text: str, *, facts: dict[str, Any]) -> str:
    text = str(source_text or "").strip()
    if text:
        return _clip(text, MAX_SUMMARY_CHARS)
    fallback_parts = [str(value) for value in facts.values() if isinstance(value, str) and value.strip()]
    return _clip("; ".join(fallback_parts), MAX_SUMMARY_CHARS)


def _tags_for_category(category: str, semantic_labels: list[str]) -> list[str]:
    tags = [category]
    tags.extend(semantic_labels)
    if category == DATA_CATEGORY_CORRECTION:
        tags.append("router_feedback")
    return sorted({tag for tag in tags if tag})


def _memory_status_for_category(category: str) -> str:
    if category == DATA_CATEGORY_CORRECTION:
        return "raw"
    return "active"


def _candidate_evidence_refs(candidate: dict[str, Any], *, source_path: str) -> list[dict[str, Any]]:
    refs = candidate.get("evidence_refs")
    if isinstance(refs, list):
        cleaned = [ref for ref in refs if isinstance(ref, dict)]
        if cleaned:
            return cleaned
    out: list[dict[str, Any]] = [{"source_path": source_path}]
    thread_id = str(candidate.get("source_thread_id") or "").strip()
    if thread_id:
        out[0]["source_thread_id"] = thread_id
    return out


def _evidence_refs(*, source_path: str, source_event_id: str, source_signal_id: str) -> list[dict[str, Any]]:
    ref: dict[str, Any] = {"source_path": source_path}
    if source_event_id:
        ref["source_event_id"] = source_event_id
    if source_signal_id:
        ref["source_signal_id"] = source_signal_id
    return [ref]


def _dedupe_key(row: dict[str, Any]) -> str:
    explicit = str(row.get("dedupe_key") or "").strip()
    if explicit:
        return explicit
    return _stable_key(
        str(row.get("category") or ""),
        str(row.get("source_path") or ""),
        str(row.get("source_signal_id") or ""),
        str(row.get("source_event_id") or ""),
        str(row.get("source_thread_id") or ""),
        str(row.get("summary") or ""),
        str(row.get("source_text") or ""),
    )


def _stable_key(*parts: Any) -> str:
    payload = "\x1f".join(str(part or "").strip() for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _clip(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def safe_tag(value: str) -> str:
    return "_".join(str(value or "").strip().lower().replace("-", "_").split())


def validate_memory_category(category: str) -> None:
    if category not in DATA_CATEGORIES:
        raise CareerMemoryError(f"unknown memory category: {category}")
