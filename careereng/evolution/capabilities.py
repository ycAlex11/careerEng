"""Persist accepted evolution capabilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.platform.persistence import JSONLStore
from careereng.utils import ensure_dir, make_id, now_iso, read_json, safe_file_stem, write_json


class EvolutionCapabilityStore:
    """Track accepted evolution outcomes that should change future runtime behavior."""

    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.root = ensure_dir(self.workspace / "evolution" / "capabilities")
        self.current_path = self.root / "current.json"
        self.events = JSONLStore(self.root / "events.jsonl")

    @staticmethod
    def key(*, site_key: str, phase: str, candidate_id: str) -> str:
        site = safe_file_stem(site_key)
        return f"site:{site}|phase:{str(phase or '').strip()}|candidate:{str(candidate_id or '').strip()}"

    def load(self) -> dict[str, Any]:
        payload = read_json(self.current_path)
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("capabilities", {})
        if not isinstance(payload.get("capabilities"), dict):
            payload["capabilities"] = {}
        return payload

    def get(self, *, site_key: str, phase: str, candidate_id: str) -> dict[str, Any]:
        payload = self.load()
        capabilities = payload.get("capabilities") if isinstance(payload.get("capabilities"), dict) else {}
        row = capabilities.get(self.key(site_key=site_key, phase=phase, candidate_id=candidate_id))
        return dict(row) if isinstance(row, dict) else {}

    def is_accepted(self, *, site_key: str, phase: str, candidate_id: str) -> bool:
        row = self.get(site_key=site_key, phase=phase, candidate_id=candidate_id)
        return str(row.get("status") or "").strip().lower() == "accepted"

    def accept(
        self,
        *,
        site_key: str,
        phase: str,
        candidate_id: str,
        source_run_id: str,
        source_batch_id: str = "",
        report_json: str = "",
        report_md: str = "",
        metrics: dict[str, Any] | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        normalized_site = safe_file_stem(site_key)
        normalized_phase = str(phase or "").strip()
        normalized_candidate = str(candidate_id or "").strip()
        record_key = self.key(site_key=normalized_site, phase=normalized_phase, candidate_id=normalized_candidate)
        now = now_iso()
        payload = self.load()
        capabilities = payload.setdefault("capabilities", {})
        existing = capabilities.get(record_key) if isinstance(capabilities.get(record_key), dict) else {}
        record = {
            **existing,
            "capability_id": str(existing.get("capability_id") or make_id("capability")),
            "key": record_key,
            "site_key": normalized_site,
            "phase": normalized_phase,
            "candidate_id": normalized_candidate,
            "status": "accepted",
            "accepted_at": now,
            "source_run_id": str(source_run_id or ""),
            "source_batch_id": str(source_batch_id or ""),
            "report_json": str(report_json or ""),
            "report_md": str(report_md or ""),
            "metrics": dict(metrics or {}),
            "reason": str(reason or "").strip(),
            "future_mode": "full_apply" if normalized_phase == "apply" else "normal",
        }
        capabilities[record_key] = record
        payload["updated_at"] = now
        write_json(self.current_path, payload)
        self.events.append(
            {
                "event_id": make_id("cap_evt"),
                "ts": now,
                "event_type": "capability.accepted",
                "key": record_key,
                "site_key": normalized_site,
                "phase": normalized_phase,
                "candidate_id": normalized_candidate,
                "source_run_id": str(source_run_id or ""),
                "source_batch_id": str(source_batch_id or ""),
            }
        )
        return record

    def revoke(
        self,
        *,
        site_key: str,
        phase: str,
        candidate_id: str,
        reason: str = "",
    ) -> dict[str, Any]:
        payload = self.load()
        capabilities = payload.setdefault("capabilities", {})
        record_key = self.key(site_key=site_key, phase=phase, candidate_id=candidate_id)
        existing = capabilities.get(record_key) if isinstance(capabilities.get(record_key), dict) else {}
        if not existing:
            return {}
        now = now_iso()
        record = {
            **existing,
            "status": "revoked",
            "revoked_at": now,
            "revoke_reason": str(reason or "").strip(),
        }
        capabilities[record_key] = record
        payload["updated_at"] = now
        write_json(self.current_path, payload)
        self.events.append(
            {
                "event_id": make_id("cap_evt"),
                "ts": now,
                "event_type": "capability.revoked",
                "key": record_key,
                "site_key": safe_file_stem(site_key),
                "phase": str(phase or "").strip(),
                "candidate_id": str(candidate_id or "").strip(),
                "reason": str(reason or "").strip(),
            }
        )
        return record
