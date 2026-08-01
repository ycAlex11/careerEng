"""Append-only technical diagnostics for LLM-directed execution recovery."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.platform.persistence import JSONLStore
from careereng.utils import now_iso

from .agent_transport_trace import _compact


class ExecutionDiagnosticStore:
    """Persist objective execution facts without classifying their business meaning."""

    def __init__(self, workspace: Path | str | None):
        self.workspace = Path(workspace).resolve() if workspace else None

    @property
    def path(self) -> Path | None:
        return self.workspace / "metrics" / "execution_diagnostics.jsonl" if self.workspace else None

    def record(self, *, kind: str, **details: Any) -> dict[str, Any]:
        row = {"ts": now_iso(), "kind": str(kind or "execution"), **{key: _compact(value) for key, value in details.items()}}
        path = self.path
        if path is not None:
            JSONLStore(path).append(row)
        return row

    def latest(self, *, site_key: str, batch_id: str = "", limit: int = 20) -> list[dict[str, Any]]:
        path = self.path
        if path is None or not path.is_file():
            return []
        rows = JSONLStore(path).read_all()
        matched = [
            row for row in rows
            if isinstance(row, dict)
            and str(row.get("site_key") or "") == str(site_key or "")
            and (not batch_id or str(row.get("batch_id") or "") == str(batch_id))
        ]
        return matched[-max(1, int(limit or 1)):]
