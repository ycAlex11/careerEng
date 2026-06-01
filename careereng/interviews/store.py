"""Local storage for interview sessions and interview-derived evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.evolution.store import EvolutionStore
from careereng.interviews.schema import (
    ADOPTION_STATUSES,
    ADOPTION_UNKNOWN,
    CREATED_REASON_MANUAL_PREP,
    CREATED_REASON_STATUS_IN_PROCESS,
    CREATED_REASONS,
    EVIDENCE_TYPES,
    PREP_EVENT_NOTE,
    PREP_EVENT_TYPES,
    SESSION_STATUS_OPEN,
    SESSION_STATUSES,
    SOURCES,
    SOURCE_MANUAL,
    SPEAKER_UNKNOWN,
    SPEAKERS,
    TEXT_TYPE_NOTE,
    TEXT_TYPES,
)
from careereng.storage.jsonl import JSONLStore
from careereng.utils import ensure_dir, make_id, now_iso


class InterviewStoreError(ValueError):
    """Raised when interview storage operations fail."""


class InterviewStore:
    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.root = ensure_dir(self.workspace / "interviews")
        self.sessions_store = JSONLStore(self.root / "sessions.jsonl")
        self.events_store = JSONLStore(self.root / "events.jsonl")
        self.candidates_store = JSONLStore(self.root / "candidates.jsonl")

    def create_session(
        self,
        *,
        company: str,
        title: str = "",
        site_key: str = "",
        url: str = "",
        site_job_id: str = "",
        canonical_job_id: str = "",
        application_status: str = "",
        application_stage: str = "",
        source_history_ref: str = "",
        created_reason: str = CREATED_REASON_MANUAL_PREP,
        source_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        company_text = str(company or "").strip()
        if not company_text:
            raise InterviewStoreError("company is required")
        reason = _enum_value(created_reason, CREATED_REASONS, CREATED_REASON_MANUAL_PREP)
        now = now_iso()
        session_id = make_id("interview")
        row = {
            "session_id": session_id,
            "created_at": now,
            "updated_at": now,
            "status": SESSION_STATUS_OPEN,
            "company": company_text,
            "title": str(title or "").strip(),
            "site_key": str(site_key or "").strip(),
            "url": str(url or "").strip(),
            "site_job_id": str(site_job_id or "").strip(),
            "canonical_job_id": str(canonical_job_id or "").strip(),
            "application_status": str(application_status or "").strip(),
            "application_stage": str(application_stage or "").strip(),
            "source_history_ref": str(source_history_ref or "").strip(),
            "created_reason": reason,
            "source_refs": _clean_list(source_refs),
        }
        self.sessions_store.append(row)
        self._ensure_session_layout(session_id)
        self._append_event("session.created", session_id=session_id, payload=row)
        return row

    def list_sessions(self, *, status: str = "all", limit: int = 50) -> list[dict[str, Any]]:
        rows = self.sessions_store.read_all()
        normalized_status = str(status or "all").strip().lower()
        if normalized_status and normalized_status != "all":
            _require_enum(normalized_status, SESSION_STATUSES, "status")
            rows = [row for row in rows if str(row.get("status") or "") == normalized_status]
        rows.sort(key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""), reverse=True)
        return rows[: int(limit)] if limit > 0 else rows

    def get_session(self, session_id: str) -> dict[str, Any]:
        normalized = str(session_id or "").strip()
        if not normalized:
            raise InterviewStoreError("session_id is required")
        for row in self.sessions_store.read_all():
            if str(row.get("session_id") or "") == normalized:
                return row
        raise InterviewStoreError(f"interview session not found: {normalized}")

    def update_session(
        self,
        session_id: str,
        *,
        company: str | None = None,
        title: str | None = None,
        site_key: str | None = None,
        url: str | None = None,
        site_job_id: str | None = None,
        canonical_job_id: str | None = None,
        application_status: str | None = None,
        application_stage: str | None = None,
        source_history_ref: str | None = None,
        source_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        current = self.get_session(session_id)
        rows = self.sessions_store.read_all()
        updates = {
            "company": company,
            "title": title,
            "site_key": site_key,
            "url": url,
            "site_job_id": site_job_id,
            "canonical_job_id": canonical_job_id,
            "application_status": application_status,
            "application_stage": application_stage,
            "source_history_ref": source_history_ref,
        }
        next_row: dict[str, Any] | None = None
        for row in rows:
            if str(row.get("session_id") or "") != str(current.get("session_id") or ""):
                continue
            for key, value in updates.items():
                if value is not None:
                    row[key] = str(value or "").strip()
            if source_refs is not None:
                row["source_refs"] = _append_unique(_clean_list(row.get("source_refs")), _clean_list(source_refs))
            row["updated_at"] = now_iso()
            next_row = dict(row)
            break
        if next_row is None:
            raise InterviewStoreError(f"interview session not found: {session_id}")
        self.sessions_store.write_all(rows)
        self._append_event("session.updated", session_id=str(next_row["session_id"]), payload=next_row)
        return next_row

    def save_candidates(self, *, query: dict[str, Any], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = self.candidates_store.read_all()
        by_id: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for row in rows:
            candidate_id = str(row.get("candidate_id") or "").strip()
            if not candidate_id:
                continue
            if candidate_id not in by_id:
                order.append(candidate_id)
            by_id[candidate_id] = row
        query_id = make_id("interview_candidate_query")
        searched_at = now_iso()
        saved: list[dict[str, Any]] = []
        for idx, candidate in enumerate(candidates, start=1):
            candidate_id = str(candidate.get("candidate_id") or "").strip()
            if not candidate_id:
                continue
            row = {
                **candidate,
                "candidate_query_id": query_id,
                "candidate_rank": idx,
                "searched_at": searched_at,
                "query": dict(query or {}),
            }
            if candidate_id not in by_id:
                order.append(candidate_id)
            by_id[candidate_id] = row
            saved.append(row)
        self.candidates_store.write_all([by_id[item] for item in order if item in by_id])
        return saved

    def get_candidate(self, candidate_id: str) -> dict[str, Any]:
        normalized = str(candidate_id or "").strip()
        if not normalized:
            raise InterviewStoreError("candidate_id is required")
        for row in self.candidates_store.read_all():
            if str(row.get("candidate_id") or "") == normalized:
                return row
        raise InterviewStoreError(f"interview candidate not found: {normalized}")

    def create_session_from_candidate(self, candidate_id: str) -> tuple[dict[str, Any], bool]:
        candidate = self.get_candidate(candidate_id)
        existing_session_id = str(candidate.get("existing_session_id") or "").strip()
        if existing_session_id:
            return self.get_session(existing_session_id), False
        existing = self._find_existing_session(candidate)
        if existing:
            return existing, False
        session = self.create_session(
            company=str(candidate.get("company") or candidate.get("employer") or candidate.get("site_key") or "").strip(),
            title=str(candidate.get("title") or "").strip(),
            site_key=str(candidate.get("site_key") or "").strip(),
            url=str(candidate.get("url") or "").strip(),
            site_job_id=str(candidate.get("site_job_id") or "").strip(),
            canonical_job_id=str(candidate.get("canonical_job_id") or "").strip(),
            application_status=str(candidate.get("application_status") or "").strip(),
            application_stage=str(candidate.get("application_stage") or "").strip(),
            source_history_ref=str(candidate.get("source_history_ref") or "").strip(),
            created_reason=CREATED_REASON_STATUS_IN_PROCESS,
            source_refs=[str(candidate.get("candidate_id") or "").strip()],
        )
        return session, True

    def add_prep_event(
        self,
        session_id: str,
        *,
        event_type: str = PREP_EVENT_NOTE,
        summary: str,
        details: str = "",
        topic_tags: list[str] | None = None,
        source_refs: list[str] | None = None,
        memory_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        session = self.get_session(session_id)
        row = {
            "prep_event_id": make_id("prep_event"),
            "session_id": session["session_id"],
            "created_at": now_iso(),
            "event_type": _enum_value(event_type, PREP_EVENT_TYPES, PREP_EVENT_NOTE),
            "summary": _required_text(summary, "summary"),
            "details": str(details or "").strip(),
            "topic_tags": _clean_list(topic_tags),
            "source_refs": _clean_list(source_refs),
            "memory_refs": _clean_list(memory_refs),
        }
        self._session_store(session["session_id"], "prep_events.jsonl").append(row)
        self._touch_session(session["session_id"])
        self._append_event("prep_event.added", session_id=session["session_id"], payload=row)
        return row

    def _find_existing_session(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        canonical_job_id = str(candidate.get("canonical_job_id") or "").strip()
        site_job_id = str(candidate.get("site_job_id") or "").strip()
        url = str(candidate.get("url") or "").strip()
        site_key = str(candidate.get("site_key") or "").strip()
        company = str(candidate.get("company") or candidate.get("employer") or "").strip().lower()
        title = str(candidate.get("title") or "").strip().lower()
        for session in self.sessions_store.read_all():
            if canonical_job_id and canonical_job_id == str(session.get("canonical_job_id") or "").strip():
                return session
            if site_key and site_job_id and site_key == str(session.get("site_key") or "").strip() and site_job_id == str(session.get("site_job_id") or "").strip():
                return session
            if url and url == str(session.get("url") or "").strip():
                return session
            if company and title and company == str(session.get("company") or "").strip().lower() and title == str(session.get("title") or "").strip().lower():
                return session
        return None

    def add_predicted_question(
        self,
        session_id: str,
        *,
        question: str,
        reason: str = "",
        expected_topics: list[str] | None = None,
        suggested_answer_outline: str = "",
        source_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        session = self.get_session(session_id)
        row = {
            "question_id": make_id("question"),
            "session_id": session["session_id"],
            "created_at": now_iso(),
            "question": _required_text(question, "question"),
            "reason": str(reason or "").strip(),
            "expected_topics": _clean_list(expected_topics),
            "suggested_answer_outline": str(suggested_answer_outline or "").strip(),
            "source_refs": _clean_list(source_refs),
        }
        self._session_store(session["session_id"], "predicted_questions.jsonl").append(row)
        self._touch_session(session["session_id"])
        self._append_event("predicted_question.added", session_id=session["session_id"], payload=row)
        return row

    def add_turn(
        self,
        session_id: str,
        *,
        raw_text: str,
        speaker: str = SPEAKER_UNKNOWN,
        text_type: str = TEXT_TYPE_NOTE,
        topic_tags: list[str] | None = None,
        source: str = SOURCE_MANUAL,
        linked_question_id: str = "",
    ) -> dict[str, Any]:
        session = self.get_session(session_id)
        row = {
            "turn_id": make_id("turn"),
            "session_id": session["session_id"],
            "created_at": now_iso(),
            "speaker": _enum_value(speaker, SPEAKERS, SPEAKER_UNKNOWN),
            "text_type": _enum_value(text_type, TEXT_TYPES, TEXT_TYPE_NOTE),
            "raw_text": _required_text(raw_text, "raw_text"),
            "topic_tags": _clean_list(topic_tags),
            "source": _enum_value(source, SOURCES, SOURCE_MANUAL),
            "linked_question_id": str(linked_question_id or "").strip(),
        }
        self._session_store(session["session_id"], "turns.jsonl").append(row)
        self._touch_session(session["session_id"])
        self._append_event("turn.added", session_id=session["session_id"], payload=row)
        return row

    def add_suggestion(
        self,
        session_id: str,
        *,
        suggested_answer: str,
        linked_turn_id: str = "",
        strategy_notes: str = "",
        adoption_status: str = ADOPTION_UNKNOWN,
        actual_answer_turn_id: str = "",
        difference_notes: str = "",
        source_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        session = self.get_session(session_id)
        row = {
            "suggestion_id": make_id("suggestion"),
            "session_id": session["session_id"],
            "created_at": now_iso(),
            "linked_turn_id": str(linked_turn_id or "").strip(),
            "suggested_answer": _required_text(suggested_answer, "suggested_answer"),
            "strategy_notes": str(strategy_notes or "").strip(),
            "adoption_status": _enum_value(adoption_status, ADOPTION_STATUSES, ADOPTION_UNKNOWN),
            "actual_answer_turn_id": str(actual_answer_turn_id or "").strip(),
            "difference_notes": str(difference_notes or "").strip(),
            "source_refs": _clean_list(source_refs),
        }
        self._session_store(session["session_id"], "suggestions.jsonl").append(row)
        self._touch_session(session["session_id"])
        self._append_event("suggestion.added", session_id=session["session_id"], payload=row)
        return row

    def add_evidence(
        self,
        session_id: str,
        *,
        evidence_type: str,
        summary: str,
        details: str = "",
        source_refs: list[str] | None = None,
        confidence: float = 0.0,
        severity: str = "medium",
    ) -> dict[str, Any]:
        session = self.get_session(session_id)
        evidence_id = make_id("interview_evidence")
        now = now_iso()
        row = {
            "evidence_id": evidence_id,
            "session_id": session["session_id"],
            "created_at": now,
            "evidence_type": _enum_value(evidence_type, EVIDENCE_TYPES, "skill_gap"),
            "summary": _required_text(summary, "summary"),
            "details": str(details or "").strip(),
            "source_refs": _clean_list(source_refs),
            "confidence": _float(confidence),
            "severity": str(severity or "medium").strip().lower() or "medium",
            "company": session.get("company") or "",
            "title": session.get("title") or "",
            "site_key": session.get("site_key") or "",
            "url": session.get("url") or "",
            "site_job_id": session.get("site_job_id") or "",
            "canonical_job_id": session.get("canonical_job_id") or "",
        }
        self._session_store(session["session_id"], "evidence.jsonl").append(row)
        self._sync_evolution_evidence(row)
        self._touch_session(session["session_id"])
        self._append_event("evidence.added", session_id=session["session_id"], payload=row)
        return row

    def add_audio_chunk(self, session_id: str, chunk_metadata: dict[str, Any]) -> dict[str, Any]:
        session = self.get_session(session_id)
        if not isinstance(chunk_metadata, dict):
            raise InterviewStoreError("chunk_metadata must be an object")
        chunk_id = str(chunk_metadata.get("chunk_id") or "").strip()
        if not chunk_id:
            raise InterviewStoreError("chunk_metadata.chunk_id is required")
        row = {
            **chunk_metadata,
            "session_id": session["session_id"],
            "attached_at": now_iso(),
        }
        self._session_store(session["session_id"], "audio_chunks.jsonl").append(row)
        self._touch_session(session["session_id"])
        self._append_event("audio_chunk.added", session_id=session["session_id"], payload=row)
        return row

    def session_counts(self, session_id: str) -> dict[str, int]:
        session = self.get_session(session_id)
        sid = str(session["session_id"])
        return {
            "prep_events": len(self._session_store(sid, "prep_events.jsonl").read_all()),
            "predicted_questions": len(self._session_store(sid, "predicted_questions.jsonl").read_all()),
            "turns": len(self._session_store(sid, "turns.jsonl").read_all()),
            "suggestions": len(self._session_store(sid, "suggestions.jsonl").read_all()),
            "evidence": len(self._session_store(sid, "evidence.jsonl").read_all()),
            "audio_chunks": len(self._session_store(sid, "audio_chunks.jsonl").read_all()),
        }

    def recent_rows(self, session_id: str, *, limit: int = 5) -> dict[str, list[dict[str, Any]]]:
        session = self.get_session(session_id)
        sid = str(session["session_id"])
        return {
            "prep_events": self._session_store(sid, "prep_events.jsonl").read_last(limit),
            "predicted_questions": self._session_store(sid, "predicted_questions.jsonl").read_last(limit),
            "turns": self._session_store(sid, "turns.jsonl").read_last(limit),
            "suggestions": self._session_store(sid, "suggestions.jsonl").read_last(limit),
            "evidence": self._session_store(sid, "evidence.jsonl").read_last(limit),
            "audio_chunks": self._session_store(sid, "audio_chunks.jsonl").read_last(limit),
        }

    def _ensure_session_layout(self, session_id: str) -> None:
        directory = ensure_dir(self.root / str(session_id))
        for name in (
            "prep_events.jsonl",
            "predicted_questions.jsonl",
            "turns.jsonl",
            "suggestions.jsonl",
            "evidence.jsonl",
            "audio_chunks.jsonl",
        ):
            JSONLStore(directory / name)
        prep = directory / "prep.md"
        if not prep.exists():
            prep.write_text("# Interview Preparation\n\n", encoding="utf-8")

    def _session_store(self, session_id: str, filename: str) -> JSONLStore:
        self._ensure_session_layout(session_id)
        return JSONLStore(self.root / str(session_id) / filename)

    def _append_event(self, event_type: str, *, session_id: str, payload: dict[str, Any]) -> None:
        self.events_store.append(
            {
                "event_id": make_id("interview_event"),
                "created_at": now_iso(),
                "event_type": event_type,
                "session_id": session_id,
                "summary": payload.get("summary") or payload.get("question") or payload.get("raw_text") or event_type,
                "payload": payload,
            }
        )

    def _touch_session(self, session_id: str) -> None:
        rows = self.sessions_store.read_all()
        updated_at = now_iso()
        for row in rows:
            if str(row.get("session_id") or "") == str(session_id):
                row["updated_at"] = updated_at
                break
        self.sessions_store.write_all(rows)

    def _sync_evolution_evidence(self, row: dict[str, Any]) -> None:
        evolution_row = {
            "evidence_id": row["evidence_id"],
            "created_at": row["created_at"],
            "area": "interview",
            "site_key": row.get("site_key") or "",
            "phase": "interview",
            "event_type": row.get("evidence_type") or "",
            "severity": row.get("severity") or "medium",
            "summary": row.get("summary") or "",
            "details": row.get("details") or "",
            "source_ref": row.get("session_id") or "",
            "source_refs": row.get("source_refs") or [],
            "confidence": row.get("confidence") or 0.0,
            "company": row.get("company") or "",
            "title": row.get("title") or "",
            "url": row.get("url") or "",
            "site_job_id": row.get("site_job_id") or "",
            "canonical_job_id": row.get("canonical_job_id") or "",
        }
        EvolutionStore(self.workspace).upsert_evidence([evolution_row])


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


def _required_text(value: str, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise InterviewStoreError(f"{field} is required")
    return text


def _enum_value(value: str, allowed: set[str], default: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in allowed:
        return normalized
    return default


def _require_enum(value: str, allowed: set[str], field: str) -> None:
    if value not in allowed:
        raise InterviewStoreError(f"invalid {field}: {value}")


def _float(value: float) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0
