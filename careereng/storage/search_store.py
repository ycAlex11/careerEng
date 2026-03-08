"""Search workflow storage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.storage.jsonl import JSONLStore
from careereng.utils import ensure_dir, make_id, now_iso


class SearchStore:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.search_dir = ensure_dir(workspace / "search")
        self.queries = JSONLStore(self.search_dir / "queries.jsonl")
        self.web_results = JSONLStore(self.search_dir / "web_results.jsonl")
        self.company_candidates = JSONLStore(self.search_dir / "company_candidates.jsonl")
        self.company_decisions = JSONLStore(self.search_dir / "company_decisions.jsonl")

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
        for row in candidates:
            self.company_candidates.append(
                {
                    "query_id": query_id,
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
