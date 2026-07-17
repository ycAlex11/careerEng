"""Durable browser-control lessons for site Skill evolution."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any

from careereng.platform.persistence import JSONLStore
from careereng.utils import make_id, now_iso, safe_file_stem


ACCEPTED_STATUS = "accepted"
DEFAULT_STATUS = ACCEPTED_STATUS
DISPLAY_LIMIT = 12


@dataclass(frozen=True)
class BrowserControlLesson:
    lesson_id: str
    created_at: str
    status: str
    phase: str
    lesson_type: str
    summary: str
    rationale: str
    evidence_origin: dict[str, str]
    applicability_scope: str
    applicability_tags: list[str]
    source_evidence_ids: list[str]
    source_candidate_ids: list[str]
    source_run_ids: list[str]
    evidence_refs: list[dict[str, str]]
    avoid_patterns: list[str]
    recommended_patterns: list[str]
    dedupe_key: str

    def to_dict(self) -> dict[str, Any]:
        origin_site = str(self.evidence_origin.get("site_key") or "").strip()
        return {
            "lesson_id": self.lesson_id,
            "created_at": self.created_at,
            "status": self.status,
            "phase": self.phase,
            "lesson_type": self.lesson_type,
            "summary": self.summary,
            "rationale": self.rationale,
            "evidence_origin": dict(self.evidence_origin),
            "applicability_scope": self.applicability_scope,
            "applicability_tags": list(self.applicability_tags),
            "source_evidence_ids": list(self.source_evidence_ids),
            "source_candidate_ids": list(self.source_candidate_ids),
            "source_run_ids": list(self.source_run_ids),
            "evidence_refs": list(self.evidence_refs),
            "avoid_patterns": list(self.avoid_patterns),
            "recommended_patterns": list(self.recommended_patterns),
            "dedupe_key": self.dedupe_key,
            # Backward-compatible aliases. `site_key` is the evidence origin, not the applicability boundary.
            "scope": self.applicability_scope,
            "site_key": origin_site,
            "applies_to": list(self.applicability_tags),
        }


def lessons_path(workspace: Path | str) -> Path:
    return Path(workspace) / "evolution" / "browser_control" / "lessons.jsonl"


class BrowserControlLessonStore:
    """Read and write curated lessons used by browser-control evolution."""

    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.path = lessons_path(self.workspace)
        self.store = JSONLStore(self.path)

    def append(self, payload: dict[str, Any]) -> BrowserControlLesson:
        lesson = normalize_lesson(payload)
        self.store.append(lesson.to_dict())
        return lesson

    def list(
        self,
        *,
        status: str = ACCEPTED_STATUS,
        site_key: str = "",
        phase: str = "",
        scope: str = "",
        tags: list[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        rows = [normalize_lesson(row).to_dict() for row in self.store.read_all() if isinstance(row, dict)]
        filtered = [
            row
            for row in rows
            if _matches(row, status=status, site_key=site_key, phase=phase, scope=scope, tags=tags or [])
        ]
        if limit is None:
            return filtered
        return filtered[-max(0, int(limit)) :]

    def accepted(
        self,
        *,
        site_key: str = "",
        phase: str = "",
        scope: str = "",
        tags: list[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.list(status=ACCEPTED_STATUS, site_key=site_key, phase=phase, scope=scope, tags=tags or [], limit=limit)

    def append_unique(self, payload: dict[str, Any]) -> tuple[BrowserControlLesson, bool]:
        lesson = normalize_lesson(payload)
        existing_keys = {
            str(row.get("dedupe_key") or "").strip()
            for row in self.list(status="", limit=None)
            if str(row.get("dedupe_key") or "").strip()
        }
        if lesson.dedupe_key in existing_keys:
            return lesson, False
        self.store.append(lesson.to_dict())
        return lesson, True


def normalize_lesson(payload: dict[str, Any]) -> BrowserControlLesson:
    return BrowserControlLesson(
        lesson_id=str(payload.get("lesson_id") or payload.get("id") or make_id("lesson")).strip(),
        created_at=str(payload.get("created_at") or payload.get("ts") or now_iso()).strip(),
        status=_normalized_text(payload.get("status"), default=DEFAULT_STATUS),
        phase=_normalized_text(payload.get("phase"), default=""),
        lesson_type=_normalized_text(payload.get("lesson_type"), default="general"),
        summary=_normalized_text(payload.get("summary"), default=""),
        rationale=_normalized_text(payload.get("rationale") or payload.get("problem") or payload.get("principle"), default=""),
        evidence_origin=_evidence_origin(payload),
        applicability_scope=_normalized_text(payload.get("applicability_scope") or payload.get("scope"), default="site_skill_evolution"),
        applicability_tags=_string_list(payload.get("applicability_tags") or payload.get("applies_to") or payload.get("future_use")),
        source_evidence_ids=_string_list(payload.get("source_evidence_ids")),
        source_candidate_ids=_string_list(payload.get("source_candidate_ids")),
        source_run_ids=_string_list(payload.get("source_run_ids")),
        evidence_refs=_evidence_refs(payload.get("evidence_refs")),
        avoid_patterns=_string_list(payload.get("avoid_patterns")),
        recommended_patterns=_string_list(payload.get("recommended_patterns") or payload.get("future_use")),
        dedupe_key=_lesson_dedupe_key(payload),
    )


def render_lessons_markdown(
    lessons: list[dict[str, Any]],
    *,
    title: str = "Accepted Browser-Control Lessons",
    limit: int = DISPLAY_LIMIT,
) -> str:
    selected = lessons[-max(1, int(limit or 1)) :]
    lines = [f"## {title}", ""]
    if not selected:
        lines.append("- No accepted browser-control lessons found.")
        return "\n".join(lines).rstrip() + "\n"
    for lesson in selected:
        origin = lesson.get("evidence_origin") if isinstance(lesson.get("evidence_origin"), dict) else {}
        origin_site = str(origin.get("site_key") or lesson.get("site_key") or "none").strip() or "none"
        applicability_scope = str(lesson.get("applicability_scope") or lesson.get("scope") or "site_skill_evolution").strip()
        applicability_tags = _string_list(lesson.get("applicability_tags") or lesson.get("applies_to"))
        phase = str(lesson.get("phase") or "all").strip() or "all"
        lesson_type = str(lesson.get("lesson_type") or "general").strip()
        lines.append(
            f"- `{lesson.get('lesson_id')}` status=`{lesson.get('status')}` "
            f"origin_site=`{origin_site}` phase=`{phase}` type=`{lesson_type}` "
            f"applicability=`{applicability_scope}` tags=`{', '.join(applicability_tags) or '-'}`"
        )
        summary = str(lesson.get("summary") or "").strip()
        if summary:
            lines.append(f"  Summary: {summary}")
        rationale = str(lesson.get("rationale") or "").strip()
        if rationale:
            lines.append(f"  Rationale: {rationale}")
        avoid = _string_list(lesson.get("avoid_patterns"))
        if avoid:
            lines.append(f"  Avoid: {'; '.join(avoid[:3])}")
        recommended = _string_list(lesson.get("recommended_patterns"))
        if recommended:
            lines.append(f"  Prefer: {'; '.join(recommended[:3])}")
    return "\n".join(lines).rstrip() + "\n"


def related_lessons_file(workspace: Path | str) -> str:
    path = lessons_path(workspace)
    return str(path) if path.exists() else ""


def _matches(row: dict[str, Any], *, status: str, site_key: str, phase: str, scope: str, tags: list[str]) -> bool:
    wanted_status = str(status or "").strip().lower()
    if wanted_status and str(row.get("status") or "").strip().lower() != wanted_status:
        return False
    wanted_site = _safe_site_key(site_key)
    origin = row.get("evidence_origin") if isinstance(row.get("evidence_origin"), dict) else {}
    row_site = _safe_site_key(origin.get("site_key") or row.get("site_key"))
    if wanted_site and row_site not in {"", wanted_site}:
        return False
    wanted_phase = str(phase or "").strip()
    row_phase = str(row.get("phase") or "").strip()
    if wanted_phase and row_phase not in {"", wanted_phase}:
        return False
    wanted_scope = str(scope or "").strip()
    row_scope = str(row.get("applicability_scope") or row.get("scope") or "").strip()
    if wanted_scope and row_scope not in {"", wanted_scope}:
        return False
    wanted_tags = set(_string_list(tags))
    if wanted_tags:
        row_tags = set(_string_list(row.get("applicability_tags") or row.get("applies_to")))
        if not wanted_tags.issubset(row_tags):
            return False
    return True


def _normalized_text(value: Any, *, default: str) -> str:
    text = str(value or "").strip()
    return text if text else default


def _safe_site_key(value: Any) -> str:
    text = str(value or "").strip()
    return safe_file_stem(text) if text else ""


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _evidence_origin(payload: dict[str, Any]) -> dict[str, str]:
    raw = payload.get("evidence_origin")
    origin = {str(key): str(value) for key, value in raw.items() if str(key).strip() and str(value).strip()} if isinstance(raw, dict) else {}
    if "site_key" not in origin:
        site_key = _safe_site_key(payload.get("source_site_key") or payload.get("site_key"))
        if site_key:
            origin["site_key"] = site_key
    for source_key, target_key in (
        ("batch_id", "batch_id"),
        ("phase", "phase"),
        ("trace_ref", "trace_ref"),
        ("source_thread_id", "source_thread_id"),
        ("source_client", "source_client"),
    ):
        value = str(payload.get(source_key) or "").strip()
        if value and target_key not in origin:
            origin[target_key] = value
    return origin


def _lesson_dedupe_key(payload: dict[str, Any]) -> str:
    explicit = str(payload.get("dedupe_key") or "").strip()
    if explicit:
        return explicit
    summary = str(payload.get("summary") or "").strip()
    rationale = str(payload.get("rationale") or payload.get("problem") or payload.get("principle") or "").strip()
    scope = str(payload.get("applicability_scope") or payload.get("scope") or "site_skill_evolution").strip()
    tags = "|".join(_string_list(payload.get("applicability_tags") or payload.get("applies_to") or payload.get("future_use")))
    payload = "\x1f".join((summary, rationale, scope, tags))
    return "lesson:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _evidence_refs(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    refs: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        ref = {str(key): str(val) for key, val in item.items() if str(key).strip() and str(val).strip()}
        if ref:
            refs.append(ref)
    return refs
