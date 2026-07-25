"""Persist bounded external-agent continuity independently from batches.

``SiteWorkItem`` remains a single ``site + batch`` unit.  A
``SiteWorkerSession`` is the generic, backend-neutral continuity record that
may bind several consecutive work items to one external-agent thread.  It
does not inspect job, browser, or Skill content.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import threading
from typing import Any

from careereng.utils import ensure_dir, make_id, now_iso, read_json, write_json


_TERMINAL_EFFECTIVE_STATUSES = frozenset({"completed", "partial_completed", "failed"})


@dataclass(frozen=True)
class SiteWorkerSessionBinding:
    worker_session_id: str
    site_key: str
    backend: str
    batch_id: str
    batch_ordinal: int
    thread_id: str = ""
    reused: bool = False
    rotation_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "worker_session_id": self.worker_session_id,
            "site_key": self.site_key,
            "backend": self.backend,
            "batch_id": self.batch_id,
            "batch_ordinal": self.batch_ordinal,
            "thread_id": self.thread_id,
            "reused": self.reused,
            "rotation_reason": self.rotation_reason,
        }


class SiteWorkerSessionStore:
    """Mechanical persistence for site-scoped external-agent sessions."""

    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.root = ensure_dir(self.workspace / "sessions" / "site_workers")
        self.path = self.root / "sessions.json"
        self._lock = threading.RLock()

    def bind_batch(
        self,
        *,
        site_key: str,
        backend: str,
        batch_id: str,
        max_effective_batches: int,
    ) -> SiteWorkerSessionBinding:
        """Bind one work item to an eligible site session without judging work."""

        normalized_site = str(site_key or "").strip()
        normalized_backend = str(backend or "external_agent").strip() or "external_agent"
        normalized_batch = str(batch_id or "").strip()
        if not normalized_site or not normalized_batch:
            raise ValueError("site worker session requires site_key and batch_id")
        limit = max(1, int(max_effective_batches or 1))
        with self._lock:
            data = self._load_locked()
            sessions = data["sessions"]
            existing = self._find_batch_session(sessions, site_key=normalized_site, backend=normalized_backend, batch_id=normalized_batch)
            if existing is None:
                existing = self._find_reusable_session(
                    sessions,
                    site_key=normalized_site,
                    backend=normalized_backend,
                    max_effective_batches=limit,
                )
            if existing is None:
                existing = self._new_session(
                    site_key=normalized_site,
                    backend=normalized_backend,
                    max_effective_batches=limit,
                )
                sessions.append(existing)
                reused = False
                rotation_reason = "new_site_session"
            else:
                reused = bool(existing.get("batch_bindings"))
                rotation_reason = ""

            bindings = existing.setdefault("batch_bindings", [])
            row = next((item for item in bindings if str(item.get("batch_id") or "") == normalized_batch), None)
            if row is None:
                row = {
                    "batch_id": normalized_batch,
                    "ordinal": len(bindings) + 1,
                    "outcome": "pending",
                    "bound_at": now_iso(),
                    "updated_at": now_iso(),
                }
                bindings.append(row)
            existing["updated_at"] = now_iso()
            self._save_locked(data)
            return SiteWorkerSessionBinding(
                worker_session_id=str(existing["worker_session_id"]),
                site_key=normalized_site,
                backend=normalized_backend,
                batch_id=normalized_batch,
                batch_ordinal=int(row.get("ordinal") or 1),
                thread_id=str(existing.get("active_thread_id") or ""),
                reused=reused,
                rotation_reason=rotation_reason,
            )

    def bind_thread(
        self,
        *,
        worker_session_id: str,
        thread_id: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """Record a transport thread binding or replacement for a session."""

        with self._lock:
            data = self._load_locked()
            session = self._session_by_id(data["sessions"], worker_session_id)
            if session is None:
                raise KeyError(f"site worker session not found: {worker_session_id}")
            normalized_thread = str(thread_id or "").strip()
            previous = str(session.get("active_thread_id") or "")
            if normalized_thread and normalized_thread != previous:
                session.setdefault("thread_bindings", []).append(
                    {
                        "thread_id": normalized_thread,
                        "bound_at": now_iso(),
                        "reason": str(reason or ("thread_replacement" if previous else "thread_started")),
                    }
                )
            session["active_thread_id"] = normalized_thread
            session["updated_at"] = now_iso()
            self._save_locked(data)
            return dict(session)

    def record_batch_outcome(
        self,
        *,
        worker_session_id: str,
        batch_id: str,
        batch_status: str,
    ) -> dict[str, Any] | None:
        """Record terminal batch facts; cancellation is deliberately non-effective."""

        normalized_status = str(batch_status or "").strip()
        with self._lock:
            data = self._load_locked()
            session = self._session_by_id(data["sessions"], worker_session_id)
            if session is None:
                return None
            binding = next(
                (row for row in session.get("batch_bindings", []) if str(row.get("batch_id") or "") == str(batch_id or "")),
                None,
            )
            if binding is None:
                return None
            binding["outcome"] = normalized_status
            binding["updated_at"] = now_iso()
            effective_ids = [str(value) for value in session.get("effective_batch_ids", []) if str(value)]
            if normalized_status in _TERMINAL_EFFECTIVE_STATUSES and str(batch_id) not in effective_ids:
                effective_ids.append(str(batch_id))
            session["effective_batch_ids"] = effective_ids
            limit = max(1, int(session.get("max_effective_batches") or 1))
            if len(effective_ids) >= limit:
                session["status"] = "review_pending"
                session["review_due_at"] = now_iso()
            session["updated_at"] = now_iso()
            self._save_locked(data)
            return dict(session)

    def site_evidence(self, site_key: str, *, backend: str = "") -> dict[str, Any]:
        """Return compact persisted session facts for reports/review cards."""

        with self._lock:
            rows = []
            for session in self._load_locked()["sessions"]:
                if str(session.get("site_key") or "") != str(site_key or ""):
                    continue
                if backend and str(session.get("backend") or "") != str(backend):
                    continue
                rows.append(
                    {
                        "worker_session_id": str(session.get("worker_session_id") or ""),
                        "backend": str(session.get("backend") or ""),
                        "status": str(session.get("status") or ""),
                        "effective_batch_count": len(session.get("effective_batch_ids") or []),
                        "max_effective_batches": int(session.get("max_effective_batches") or 0),
                        "active_thread_id": str(session.get("active_thread_id") or ""),
                        "batch_bindings": list(session.get("batch_bindings") or []),
                        "thread_bindings": list(session.get("thread_bindings") or []),
                    }
                )
            return {"site_key": str(site_key or ""), "sessions": rows}

    def _load_locked(self) -> dict[str, Any]:
        payload = read_json(self.path) if self.path.exists() else {}
        if not isinstance(payload, dict):
            payload = {}
        sessions = payload.get("sessions") if isinstance(payload.get("sessions"), list) else []
        return {"sessions": [dict(row) for row in sessions if isinstance(row, dict)]}

    def _save_locked(self, payload: dict[str, Any]) -> None:
        write_json(self.path, {"updated_at": now_iso(), "sessions": list(payload.get("sessions") or [])})

    @staticmethod
    def _session_by_id(sessions: list[dict[str, Any]], worker_session_id: str) -> dict[str, Any] | None:
        return next((row for row in sessions if str(row.get("worker_session_id") or "") == str(worker_session_id or "")), None)

    @staticmethod
    def _find_batch_session(
        sessions: list[dict[str, Any]],
        *,
        site_key: str,
        backend: str,
        batch_id: str,
    ) -> dict[str, Any] | None:
        return next(
            (
                row
                for row in reversed(sessions)
                if str(row.get("site_key") or "") == site_key
                and str(row.get("backend") or "") == backend
                and any(str(binding.get("batch_id") or "") == batch_id for binding in row.get("batch_bindings", []))
            ),
            None,
        )

    @staticmethod
    def _find_reusable_session(
        sessions: list[dict[str, Any]],
        *,
        site_key: str,
        backend: str,
        max_effective_batches: int,
    ) -> dict[str, Any] | None:
        return next(
            (
                row
                for row in reversed(sessions)
                if str(row.get("site_key") or "") == site_key
                and str(row.get("backend") or "") == backend
                and str(row.get("status") or "active") == "active"
                and len(row.get("effective_batch_ids") or []) < max_effective_batches
            ),
            None,
        )

    @staticmethod
    def _new_session(*, site_key: str, backend: str, max_effective_batches: int) -> dict[str, Any]:
        now = now_iso()
        return {
            "worker_session_id": make_id("site_worker"),
            "site_key": site_key,
            "backend": backend,
            "status": "active",
            "max_effective_batches": max(1, int(max_effective_batches or 1)),
            "active_thread_id": "",
            "thread_bindings": [],
            "batch_bindings": [],
            "effective_batch_ids": [],
            "created_at": now,
            "updated_at": now,
        }
