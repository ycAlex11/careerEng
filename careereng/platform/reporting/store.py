"""Generic report artifact storage without domain report semantics."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from careereng.platform.persistence import JSONLStore
from careereng.utils import ensure_dir, make_id, now_iso, safe_file_stem, write_json


class ReportArtifactError(ValueError):
    """Raised when a report artifact path is outside its workspace."""


class ReportArtifactStore:
    """Write report artifacts and maintain generic report metadata indexes."""

    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace).resolve()
        self.root = ensure_dir(self.workspace / "reports")
        self.history_root = ensure_dir(self.root / "history")
        self.index_store = JSONLStore(self.root / "index.jsonl")
        self.events_store = JSONLStore(self.root / "events.jsonl")

    def write_json(
        self,
        *,
        artifact_id: str,
        domain: str,
        report_type: str,
        json_path: Path | str,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        snapshot_existing: bool = False,
    ) -> dict[str, Any]:
        return self._write(
            artifact_id=artifact_id,
            domain=domain,
            report_type=report_type,
            json_path=json_path,
            markdown_path=None,
            payload=payload,
            markdown="",
            metadata=metadata,
            snapshot_existing=snapshot_existing,
        )

    def write_json_markdown(
        self,
        *,
        artifact_id: str,
        domain: str,
        report_type: str,
        json_path: Path | str,
        markdown_path: Path | str,
        payload: dict[str, Any],
        markdown: str,
        metadata: dict[str, Any] | None = None,
        snapshot_existing: bool = False,
    ) -> dict[str, Any]:
        return self._write(
            artifact_id=artifact_id,
            domain=domain,
            report_type=report_type,
            json_path=json_path,
            markdown_path=markdown_path,
            payload=payload,
            markdown=markdown,
            metadata=metadata,
            snapshot_existing=snapshot_existing,
        )

    def _write(
        self,
        *,
        artifact_id: str,
        domain: str,
        report_type: str,
        json_path: Path | str,
        markdown_path: Path | str | None,
        payload: dict[str, Any],
        markdown: str,
        metadata: dict[str, Any] | None,
        snapshot_existing: bool,
    ) -> dict[str, Any]:
        normalized_id = str(artifact_id or "").strip()
        if not normalized_id:
            raise ReportArtifactError("artifact_id is required")
        normalized_domain = str(domain or "").strip() or "unknown"
        normalized_type = str(report_type or "").strip() or "report"
        resolved_json = self._resolve_path(json_path)
        resolved_markdown = self._resolve_path(markdown_path) if markdown_path else None
        snapshot_paths = self._snapshot_existing(
            artifact_id=normalized_id,
            paths=[path for path in (resolved_json, resolved_markdown) if path is not None],
        ) if snapshot_existing else []

        ensure_dir(resolved_json.parent)
        write_json(resolved_json, dict(payload or {}))
        if resolved_markdown is not None:
            ensure_dir(resolved_markdown.parent)
            resolved_markdown.write_text(str(markdown or "").rstrip() + "\n", encoding="utf-8")

        now = now_iso()
        row = {
            "artifact_id": normalized_id,
            "domain": normalized_domain,
            "report_type": normalized_type,
            "updated_at": now,
            "json_path": self._relative_path(resolved_json),
            "markdown_path": self._relative_path(resolved_markdown) if resolved_markdown else "",
            "metadata": dict(metadata or {}),
        }
        previous = self._upsert_index(row)
        event = {
            "event_id": make_id("report_artifact_event"),
            "created_at": now,
            "event_type": "report_artifact.written",
            "artifact_id": normalized_id,
            "domain": normalized_domain,
            "report_type": normalized_type,
            "json_path": row["json_path"],
            "markdown_path": row["markdown_path"],
            "snapshot_paths": [self._relative_path(path) for path in snapshot_paths],
            "metadata": dict(metadata or {}),
        }
        self.events_store.append(event)
        return {**row, "created_at": previous.get("created_at") or now, "snapshot_paths": event["snapshot_paths"]}

    def _upsert_index(self, row: dict[str, Any]) -> dict[str, Any]:
        rows = self.index_store.read_all()
        updated_rows: list[dict[str, Any]] = []
        previous: dict[str, Any] = {}
        found = False
        for current in rows:
            if str(current.get("artifact_id") or "") != str(row.get("artifact_id") or ""):
                updated_rows.append(current)
                continue
            previous = dict(current)
            updated_rows.append({**current, **row, "created_at": current.get("created_at") or row.get("updated_at")})
            found = True
        if not found:
            updated_rows.append({**row, "created_at": row.get("updated_at")})
        self.index_store.write_all(updated_rows)
        return previous

    def _snapshot_existing(self, *, artifact_id: str, paths: list[Path]) -> list[Path]:
        snapshot_dir = ensure_dir(self.history_root / safe_file_stem(artifact_id))
        snapshots: list[Path] = []
        for path in paths:
            if not path.exists():
                continue
            stem = f"{safe_file_stem(now_iso().replace(':', '-'))}-{safe_file_stem(path.stem)}"
            target = self._unique_path(snapshot_dir, stem, path.suffix)
            target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            snapshots.append(target)
        return snapshots

    def _resolve_path(self, value: Path | str) -> Path:
        path = Path(value)
        resolved = path.resolve() if path.is_absolute() else (self.workspace / path).resolve()
        try:
            resolved.relative_to(self.workspace)
        except ValueError as exc:
            raise ReportArtifactError(f"report artifact path must stay within workspace: {path}") from exc
        return resolved

    def _relative_path(self, path: Path) -> str:
        return str(path.relative_to(self.workspace))

    @staticmethod
    def _unique_path(directory: Path, stem: str, suffix: str) -> Path:
        target = directory / f"{stem}{suffix}"
        counter = 2
        while target.exists():
            target = directory / f"{stem}-{counter}{suffix}"
            counter += 1
        return target
