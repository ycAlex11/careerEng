"""Durable mechanical counters for site evolution scheduling."""

from __future__ import annotations

import hashlib
from pathlib import Path
from threading import RLock
from typing import Any

from careereng.utils import ensure_dir, now_iso, read_json, write_json


EXPLORATION_READY_STREAK = 3


class EvolutionProgressStore:
    """Persist version-scoped counters without making workflow decisions."""

    def __init__(self, workspace: Path | str):
        self.path = ensure_dir(Path(workspace) / "evolution") / "progress.json"
        self._lock = RLock()

    def observe_exploration_cycle(
        self,
        *,
        site_key: str,
        cycle_id: str,
        outcome: str,
        skill_path: Path | str,
    ) -> dict[str, Any]:
        normalized_site = str(site_key or "").strip()
        normalized_cycle = str(cycle_id or "").strip()
        if not normalized_site or not normalized_cycle:
            raise ValueError("exploration progress requires site_key and cycle_id")
        version = _file_version(Path(skill_path))
        with self._lock:
            payload = self._load()
            sites = payload.setdefault("exploration", {})
            current = dict(sites.get(normalized_site) or {})
            if str(current.get("skill_version") or "") != version:
                current = {
                    "skill_version": version,
                    "consecutive_successes": 0,
                    "observed_cycle_ids": [],
                }
            observed = [str(value) for value in current.get("observed_cycle_ids") or [] if str(value)]
            if normalized_cycle not in observed:
                observed.append(normalized_cycle)
                if outcome == "successful_apply_cycle":
                    current["consecutive_successes"] = int(current.get("consecutive_successes") or 0) + 1
                elif outcome == "confirmed_internal_failure":
                    current["consecutive_successes"] = 0
            current.update(
                {
                    "site_key": normalized_site,
                    "skill_version": version,
                    "observed_cycle_ids": observed[-20:],
                    "last_cycle_id": normalized_cycle,
                    "last_outcome": str(outcome or "unknown"),
                    "updated_at": now_iso(),
                }
            )
            current["readiness_due"] = int(current.get("consecutive_successes") or 0) >= EXPLORATION_READY_STREAK
            sites[normalized_site] = current
            payload["updated_at"] = now_iso()
            write_json(self.path, payload)
            return dict(current)

    def _load(self) -> dict[str, Any]:
        payload = read_json(self.path)
        return payload if isinstance(payload, dict) else {}


def _file_version(path: Path) -> str:
    if not path.is_file():
        return "missing"
    return hashlib.sha256(path.read_bytes()).hexdigest()
