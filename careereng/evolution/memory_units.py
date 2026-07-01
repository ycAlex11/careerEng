"""JSONL-backed evolution memory units.

This module is intentionally thin. It normalizes row shape, upserts JSONL
records, appends usage/validation events, and renders context. It does not
infer business value or invent materialized evolution.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from careereng.evolution.proposals import SUPPORTED_CHANGE_TYPES
from careereng.storage.jsonl import JSONLStore
from careereng.utils import now_iso, safe_file_stem


MATERIALIZED_CHANGE_TYPES = set(SUPPORTED_CHANGE_TYPES)
PROPOSAL_STATUSES = {"incomplete", "materialized"}
NON_MATERIALIZED_CHANGE_SOURCES = {"loop_control_llm_guidance", "loop_control_evidence", "python_guidance"}
RUN_LOCAL_CLOSED_FOR_SYNTHESIS = "closed_for_synthesis"


def evolution_memory_path(workspace: Path | str) -> Path:
    return Path(workspace) / "evolution" / "memory" / "units.jsonl"


class EvolutionMemoryStore:
    """Small JSONL store for evolution memory units."""

    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.store = JSONLStore(evolution_memory_path(self.workspace))

    def upsert(self, unit: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_evolution_memory_unit(unit)
        rows = self.store.read_all()
        fingerprint = str(normalized.get("fingerprint") or "")
        now = now_iso()
        updated = False
        next_rows: list[dict[str, Any]] = []
        for row in rows:
            if fingerprint and str(row.get("fingerprint") or "") == fingerprint:
                merged = {
                    **row,
                    **normalized,
                    "created_at": row.get("created_at") or normalized.get("created_at"),
                    "updated_at": now,
                }
                next_rows.append(merged)
                normalized = merged
                updated = True
            else:
                next_rows.append(row)
        if not updated:
            next_rows.append(normalized)
        self.store.write_all(next_rows)
        return normalized

    def query(
        self,
        *,
        candidate_id: str = "",
        scopes: list[str] | tuple[str, ...] | None = None,
        phase: str = "",
        lifecycles: list[str] | tuple[str, ...] | None = None,
        statuses: list[str] | tuple[str, ...] | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        candidate = str(candidate_id or "").strip()
        scope_set = {str(item or "").strip() for item in (scopes or []) if str(item or "").strip()}
        lifecycle_set = {str(item or "").strip() for item in (lifecycles or []) if str(item or "").strip()}
        status_set = {str(item or "").strip() for item in (statuses or []) if str(item or "").strip()}
        phase_text = str(phase or "").strip()
        rows: list[dict[str, Any]] = []
        for raw in self.store.read_all():
            row = normalize_evolution_memory_unit(raw)
            if candidate and str(row.get("candidate_id") or "") != candidate:
                continue
            if scope_set and str(row.get("scope") or "") not in scope_set:
                continue
            if phase_text and str(row.get("phase") or "") not in {"", phase_text}:
                continue
            if lifecycle_set and str(row.get("lifecycle") or "") not in lifecycle_set:
                continue
            if status_set and str(row.get("status") or "") not in status_set:
                continue
            rows.append(row)
        rows.sort(key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""))
        return rows[-max(1, int(limit or 1)) :]

    def append_validation_event(self, *, memory_id: str = "", proposal_id: str = "", event: dict[str, Any]) -> dict[str, Any]:
        return self._append_event(memory_id=memory_id, proposal_id=proposal_id, event=event, key="validation_events")

    def append_usage_event(self, *, memory_id: str = "", proposal_id: str = "", event: dict[str, Any]) -> dict[str, Any]:
        return self._append_event(memory_id=memory_id, proposal_id=proposal_id, event=event, key="usage_events")

    def close_run_local_scope_after_synthesis(
        self,
        *,
        scope: str,
        reason: str,
        run_id: str = "",
        exclude_memory_ids: list[str] | tuple[str, ...] | None = None,
        exclude_proposal_ids: list[str] | tuple[str, ...] | None = None,
        status: str = RUN_LOCAL_CLOSED_FOR_SYNTHESIS,
    ) -> dict[str, Any]:
        """Close active run-local execution state after synthesis has consumed it.

        Records are retained as history/evidence. This only changes execution
        lifecycle state; it does not evaluate proposal quality.
        """

        normalized_scope = str(scope or "").strip()
        if not normalized_scope:
            return {"closed_count": 0, "closed_memory_ids": []}
        excluded_memory = {str(item or "").strip() for item in (exclude_memory_ids or []) if str(item or "").strip()}
        excluded_proposals = {str(item or "").strip() for item in (exclude_proposal_ids or []) if str(item or "").strip()}
        close_status = str(status or RUN_LOCAL_CLOSED_FOR_SYNTHESIS).strip()
        now = now_iso()
        rows = self.store.read_all()
        closed_ids: list[str] = []
        next_rows: list[dict[str, Any]] = []
        for raw in rows:
            proposal = raw.get("proposal") if isinstance(raw.get("proposal"), dict) else {}
            memory_id = str(raw.get("memory_id") or "").strip()
            proposal_id = str(proposal.get("proposal_id") or "").strip()
            should_close = (
                str(raw.get("scope") or "").strip() == normalized_scope
                and str(raw.get("lifecycle") or "").strip() == "run_local"
                and str(raw.get("status") or "").strip() == "active"
                and memory_id not in excluded_memory
                and proposal_id not in excluded_proposals
            )
            if should_close:
                row = normalize_evolution_memory_unit(raw)
                row["status"] = close_status
                row["updated_at"] = now
                close_events = _dict_list(row.get("close_events"))
                close_events.append(
                    {
                        "closed_at": now,
                        "status": close_status,
                        "reason": str(reason or "").strip(),
                        "run_id": str(run_id or "").strip(),
                    }
                )
                row["close_events"] = close_events[-20:]
                closed_ids.append(memory_id)
                next_rows.append(row)
            else:
                next_rows.append(raw)
        if closed_ids:
            self.store.write_all(next_rows)
        return {"closed_count": len(closed_ids), "closed_memory_ids": closed_ids, "scope": normalized_scope, "status": close_status}

    def _append_event(
        self,
        *,
        memory_id: str = "",
        proposal_id: str = "",
        event: dict[str, Any],
        key: str,
    ) -> dict[str, Any]:
        wanted_memory = str(memory_id or "").strip()
        wanted_proposal = str(proposal_id or "").strip()
        if not wanted_memory and not wanted_proposal:
            return {}
        event_payload = dict(event) if isinstance(event, dict) else {}
        rows = self.store.read_all()
        now = now_iso()
        updated: dict[str, Any] = {}
        next_rows: list[dict[str, Any]] = []
        for raw in rows:
            row = normalize_evolution_memory_unit(raw)
            proposal = row.get("proposal") if isinstance(row.get("proposal"), dict) else {}
            matches = bool(wanted_memory and str(row.get("memory_id") or "") == wanted_memory) or bool(
                wanted_proposal and str(proposal.get("proposal_id") or "") == wanted_proposal
            )
            if matches:
                events = _dict_list(row.get(key))
                events.append({**event_payload, "recorded_at": str(event_payload.get("recorded_at") or now)})
                row[key] = events[-20:]
                row["updated_at"] = now
                updated = row
            next_rows.append(row)
        if updated:
            self.store.write_all(next_rows)
        return updated


def build_loop_evolution_memory(
    *,
    candidate_id: str,
    scope: str,
    site_key: str,
    phase: str,
    lifecycle: str,
    status: str,
    pattern: str,
    evidence: str,
    summary: str,
    avoid_patterns: list[str] | tuple[str, ...] | None = None,
    recommended_patterns: list[str] | tuple[str, ...] | None = None,
    source: dict[str, Any] | None = None,
    target: str = "",
    confidence: float = 0.5,
    proposal: dict[str, Any] | None = None,
    usage_events: list[dict[str, Any]] | None = None,
    validation_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = {
        "candidate_id": str(candidate_id or "site_apply_loop_control").strip(),
        "scope": str(scope or "").strip(),
        "site_key": safe_file_stem(site_key or ""),
        "phase": str(phase or "").strip(),
        "lifecycle": str(lifecycle or "run_local").strip(),
        "status": str(status or "active").strip(),
        "pattern": safe_file_stem(pattern or "unknown").replace("-", "_"),
        "evidence": str(evidence or "").strip(),
        "summary": str(summary or "").strip(),
        "avoid_patterns": _string_list(avoid_patterns),
        "recommended_patterns": _string_list(recommended_patterns),
        "source": source if isinstance(source, dict) else {},
        "target": str(target or "").strip(),
        "confidence": float(confidence or 0.0),
        "proposal": proposal if isinstance(proposal, dict) else {},
        "usage_events": usage_events if isinstance(usage_events, list) else [],
        "validation_events": validation_events if isinstance(validation_events, list) else [],
    }
    return normalize_evolution_memory_unit(payload)


def normalize_evolution_memory_unit(payload: dict[str, Any]) -> dict[str, Any]:
    now = now_iso()
    candidate_id = str(payload.get("candidate_id") or "site_apply_loop_control").strip()
    scope = str(payload.get("scope") or "").strip()
    phase = str(payload.get("phase") or "").strip()
    pattern = safe_file_stem(str(payload.get("pattern") or "unknown")).replace("-", "_")
    lifecycle = str(payload.get("lifecycle") or "run_local").strip()
    status = str(payload.get("status") or "active").strip()
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    proposal = payload.get("proposal") if isinstance(payload.get("proposal"), dict) else {}
    fingerprint_payload = {
        "candidate_id": candidate_id,
        "scope": scope,
        "phase": phase,
        "pattern": pattern,
        "lifecycle": lifecycle,
        "source_batch_id": str(source.get("batch_id") or ""),
        "source_job_id": str(source.get("job_id") or ""),
        "source_run_id": str(source.get("run_id") or ""),
        "proposal_id": str(proposal.get("proposal_id") or ""),
    }
    fingerprint = str(payload.get("fingerprint") or _fingerprint(fingerprint_payload))
    memory_id = str(payload.get("memory_id") or f"evo_mem_{fingerprint[:16]}").strip()
    return {
        "memory_id": memory_id,
        "created_at": str(payload.get("created_at") or now),
        "updated_at": str(payload.get("updated_at") or now),
        "memory_type": "evolution_loop_guidance",
        "candidate_id": candidate_id,
        "scope": scope,
        "site_key": safe_file_stem(str(payload.get("site_key") or "")),
        "phase": phase,
        "lifecycle": lifecycle,
        "status": status,
        "pattern": pattern,
        "summary": str(payload.get("summary") or "").strip(),
        "evidence": str(payload.get("evidence") or "").strip(),
        "avoid_patterns": _string_list(payload.get("avoid_patterns")),
        "recommended_patterns": _string_list(payload.get("recommended_patterns")),
        "source": source,
        "target": str(payload.get("target") or "").strip(),
        "confidence": float(payload.get("confidence") or 0.0),
        "proposal": normalize_proposal(proposal),
        "usage_events": _dict_list(payload.get("usage_events"))[-20:],
        "validation_events": _dict_list(payload.get("validation_events"))[-20:],
        "close_events": _dict_list(payload.get("close_events"))[-20:],
        "labels": _string_list(payload.get("labels")) or [
            "evolution_memory",
            candidate_id,
            lifecycle,
            phase,
            pattern,
        ],
        "fingerprint": fingerprint,
    }


def normalize_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(proposal, dict) or not proposal:
        return {}
    materialized = proposal.get("materialized_change") if isinstance(proposal.get("materialized_change"), dict) else {}
    change_type = str(materialized.get("type") or "").strip()
    change_content = str(materialized.get("content") or "").strip()
    change_source = str(materialized.get("source") or "").strip()
    materialized_payload = (
        {
            "type": change_type,
            "content": change_content,
            "source": change_source,
        }
        if change_type in MATERIALIZED_CHANGE_TYPES
        and change_content
        and change_source not in NON_MATERIALIZED_CHANGE_SOURCES
        else {}
    )
    proposal_status = str(proposal.get("proposal_status") or "incomplete").strip()
    if proposal_status not in PROPOSAL_STATUSES:
        proposal_status = "incomplete"
    if proposal_status == "materialized" and not materialized_payload:
        proposal_status = "incomplete"
    return {
        "proposal_id": str(proposal.get("proposal_id") or "").strip(),
        "proposal_kind": str(proposal.get("proposal_kind") or "").strip(),
        "prompt_overlay": str(proposal.get("prompt_overlay") or "").strip(),
        "expected_validation": str(proposal.get("expected_validation") or "").strip(),
        "source_evidence_id": str(proposal.get("source_evidence_id") or "").strip(),
        "target_ref": str(proposal.get("target_ref") or "").strip(),
        "materialized_change": materialized_payload if proposal_status == "materialized" else {},
        "proposal_status": proposal_status,
    }


def evolution_memory_has_materialized_change(unit: dict[str, Any] | None) -> bool:
    if not isinstance(unit, dict):
        return False
    proposal = unit.get("proposal") if isinstance(unit.get("proposal"), dict) else {}
    if str(proposal.get("proposal_status") or "").strip() != "materialized":
        return False
    materialized = proposal.get("materialized_change") if isinstance(proposal.get("materialized_change"), dict) else {}
    return bool(
        str(materialized.get("type") or "").strip() in MATERIALIZED_CHANGE_TYPES
        and str(materialized.get("content") or "").strip()
    )


def render_evolution_memory_guidance(units: list[dict[str, Any]], *, title: str = "Relevant Evolution Memory") -> str:
    if not units:
        return ""
    lines = [f"## {title}", ""]
    for unit in units:
        lines.append(
            f"- `{unit.get('memory_id')}` lifecycle=`{unit.get('lifecycle')}` "
            f"scope=`{unit.get('scope')}` phase=`{unit.get('phase')}` pattern=`{unit.get('pattern')}`"
        )
        summary = str(unit.get("summary") or "").strip()
        if summary:
            lines.append(f"  Summary: {summary}")
        avoid = _string_list(unit.get("avoid_patterns"))
        if avoid:
            lines.append(f"  Avoid: {'; '.join(avoid[:4])}")
        recommended = _string_list(unit.get("recommended_patterns"))
        if recommended:
            lines.append(f"  Prefer: {'; '.join(recommended[:4])}")
        proposal = unit.get("proposal") if isinstance(unit.get("proposal"), dict) else {}
        overlay = str(proposal.get("prompt_overlay") or "").strip()
        if overlay:
            label = "Active run-local proposal under validation" if evolution_memory_has_materialized_change(unit) else "Incomplete run-local proposal"
            lines.append(f"  {label}: `{proposal.get('proposal_id') or ''}` kind=`{proposal.get('proposal_kind') or ''}`")
            lines.append(f"  {overlay}")
        expected = str(proposal.get("expected_validation") or "").strip()
        if expected:
            lines.append(f"  Validation target: {expected}")
    return "\n".join(lines).rstrip()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _fingerprint(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()
