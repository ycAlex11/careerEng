"""Search workflow storage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.platform.persistence import JSONLStore
from careereng.utils import ensure_dir, make_id, now_iso, read_json, safe_file_stem, write_json


class SearchStore:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.search_dir = ensure_dir(workspace / "search")
        self.queries = JSONLStore(self.search_dir / "queries.jsonl")
        self.web_results = JSONLStore(self.search_dir / "web_results.jsonl")
        self.company_candidates = JSONLStore(self.search_dir / "company_candidates.jsonl")
        self.company_decisions = JSONLStore(self.search_dir / "company_decisions.jsonl")
        self.company_snapshot_dir = ensure_dir(self.search_dir / "company_snapshots")

    def _snapshot_session_key(self, session_id: str) -> str:
        return safe_file_stem(str(session_id or "default").replace(":", "-"))

    def _snapshot_session_dir(self, session_id: str) -> Path:
        return ensure_dir(self.company_snapshot_dir / self._snapshot_session_key(session_id))

    def _snapshot_history_dir(self, session_id: str) -> Path:
        return ensure_dir(self._snapshot_session_dir(session_id) / "history")

    def latest_company_snapshot_path(self, session_id: str) -> Path:
        return self._snapshot_session_dir(session_id) / "latest.json"

    def _normalize_snapshot_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for idx, row in enumerate(candidates, 1):
            if not isinstance(row, dict):
                continue
            item = dict(row)
            item["candidate_index"] = idx
            out.append(item)
        return out

    def save_company_snapshot(
        self,
        *,
        session_id: str,
        query_id: str,
        turn_id: str,
        user_message: str,
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        latest_path = self.latest_company_snapshot_path(session_id)
        existing = read_json(latest_path)
        if existing:
            history_dir = self._snapshot_history_dir(session_id)
            archive_name = (
                f"{str(existing.get('created_at') or now_iso()).replace(':', '-')}_"
                f"{safe_file_stem(str(existing.get('query_id') or 'snapshot'))}.json"
            )
            archive_path = history_dir / archive_name
            counter = 1
            while archive_path.exists():
                archive_path = history_dir / archive_name.replace(".json", f"-{counter}.json")
                counter += 1
            latest_path.replace(archive_path)

        payload = {
            "snapshot_id": make_id("company_snapshot"),
            "session_id": session_id,
            "query_id": query_id,
            "turn_id": turn_id,
            "created_at": now_iso(),
            "user_message": user_message,
            "candidates": self._normalize_snapshot_candidates(candidates),
        }
        write_json(latest_path, payload)
        return payload

    def load_latest_company_snapshot(self, session_id: str) -> dict[str, Any]:
        return read_json(self.latest_company_snapshot_path(session_id))

    def load_company_snapshot_candidates(self, session_id: str) -> list[dict[str, Any]]:
        payload = self.load_latest_company_snapshot(session_id)
        rows = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
        out: list[dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict):
                out.append(row)
        out.sort(key=lambda row: int(row.get("candidate_index") or 0))
        return out

    def start_query(
        self,
        *,
        session_id: str,
        turn_id: str,
        user_message: str,
        query_spec: dict[str, Any],
    ) -> dict[str, Any]:
        row = {
            "query_id": make_id("qry"),
            "ts": now_iso(),
            "session_id": session_id,
            "turn_id": turn_id,
            "user_message": user_message,
            "query_spec": query_spec,
        }
        self.queries.append(row)
        return row

    def append_web_results(
        self,
        *,
        query_id: str,
        query_text: str,
        source: str,
        items: list[dict[str, Any]],
    ) -> None:
        for item in items:
            self.web_results.append(
                {
                    "query_id": query_id,
                    "ts": now_iso(),
                    "query_text": query_text,
                    "source": source,
                    **item,
                }
            )

    def append_company_candidates(
        self,
        *,
        query_id: str,
        candidates: list[dict[str, Any]],
    ) -> None:
        for idx, row in enumerate(candidates, 1):
            if not isinstance(row, dict):
                continue
            self.company_candidates.append(
                {
                    "query_id": query_id,
                    "candidate_index": idx,
                    "ts": now_iso(),
                    **row,
                }
            )

    def append_company_decision(
        self,
        *,
        query_id: str,
        session_id: str,
        company: str,
        site_id: str,
        decision: str,
        reason_tag: str = "",
        reason_text: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.company_decisions.append(
            {
                "decision_id": make_id("cmp_dec"),
                "query_id": query_id,
                "ts": now_iso(),
                "session_id": session_id,
                "company": company,
                "site_id": site_id,
                "decision": decision,
                "reason_tag": reason_tag,
                "reason_text": reason_text,
                "metadata": metadata or {},
            }
        )
