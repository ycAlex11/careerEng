"""Shared profile/intent document store logic."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .jsonl import JSONLStore
from .versioned_document import VersionedDocumentStore
from careereng.utils import dump_front_matter, ensure_dir, make_id, now_iso, parse_front_matter, deep_merge


class DomainStore:
    def __init__(
        self,
        workspace: Path,
        *,
        domain: str,
        doc_name: str,
        events_name: str,
        default_doc: dict[str, Any],
    ):
        self.workspace = workspace
        self.domain = domain
        self.domain_dir = ensure_dir(workspace / domain)
        self.doc_path = self.domain_dir / doc_name
        self.history_dir = ensure_dir(self.domain_dir / "history")
        self.reports_dir = ensure_dir(self.domain_dir / "reports")
        self.events_store = JSONLStore(self.domain_dir / events_name)
        self.documents = VersionedDocumentStore(
            current_path=self.doc_path,
            history_dir=self.history_dir,
            events_path=None,
            artifact_type=f"{domain}_document",
        )
        self.default_doc = default_doc
        self.ensure_initialized()

    def ensure_initialized(self) -> None:
        if not self.doc_path.exists():
            self.doc_path.write_text(dump_front_matter(self.default_doc), encoding="utf-8")

    def load_doc(self) -> dict[str, Any]:
        data, _ = parse_front_matter(self.doc_path.read_text(encoding="utf-8"))
        if not data:
            return self.default_doc.copy()
        return data

    def save_doc(self, data: dict[str, Any], reason: str) -> None:
        data = dict(data)
        data["updated_at"] = now_iso()[:10]
        self.documents.replace_current(
            dump_front_matter(data),
            artifact_id=self.domain,
            event_type="document.updated",
            summary="Updated domain document.",
            reason=reason,
            snapshot_existing=True,
        )
        self.append_event(
            {
                "name": "doc.updated",
                "related": True,
                "status": "applied",
                "reason": reason,
                "patch": {},
            }
        )

    def apply_patch(self, patch: dict[str, Any], reason: str) -> dict[str, Any]:
        current = self.load_doc()
        merged = deep_merge(current, patch)
        self.save_doc(merged, reason=reason)
        return merged

    def append_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = {
            "id": make_id(f"{self.domain}_evt"),
            "ts": now_iso(),
            "domain": self.domain,
            "name": payload.get("name", "candidate"),
            "message": payload.get("message", ""),
            "message_id": payload.get("message_id", ""),
            "session_id": payload.get("session_id", ""),
            "related": bool(payload.get("related", False)),
            "confidence": float(payload.get("confidence", 0.0) or 0.0),
            "reason": payload.get("reason", ""),
            "patch": payload.get("patch", {}),
            "status": payload.get("status", "candidate"),
            "in_report": bool(payload.get("in_report", False)),
            "few_shot_version": payload.get("few_shot_version", "v1"),
            "evaluator_version": payload.get("evaluator_version", "v1"),
            "metadata": payload.get("metadata", {}),
        }
        self.events_store.append(row)
        return row

    def list_events(self) -> list[dict[str, Any]]:
        return self.events_store.read_all()

    def update_event(self, event_id: str, **updates: Any) -> None:
        rows = self.events_store.read_all()
        changed = False
        for row in rows:
            if row.get("id") == event_id:
                row.update(updates)
                changed = True
                break
        if changed:
            self.events_store.write_all(rows)

    def generate_report_if_ready(self, threshold: int = 20) -> dict[str, Any] | None:
        rows = self.events_store.read_all()
        candidates = [r for r in rows if r.get("related") and not r.get("in_report") and r.get("status") == "candidate"]
        if len(candidates) < threshold:
            return None
        batch = candidates[:threshold]
        report = {
            "id": make_id(f"{self.domain}_report"),
            "domain": self.domain,
            "created_at": now_iso(),
            "status": "pending_review",
            "items": [
                {
                    "event_id": e.get("id"),
                    "message": e.get("message"),
                    "reason": e.get("reason"),
                    "patch": e.get("patch", {}),
                    "user_relevant": None,
                }
                for e in batch
            ],
            "apply_decision": None,
            "applied": False,
        }
        (self.reports_dir / f"{report['id']}.json").write_text(__import__('json').dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        for event in rows:
            if any(event.get("id") == item["event_id"] for item in report["items"]):
                event["in_report"] = True
                event["status"] = "in_report"
        self.events_store.write_all(rows)
        return report

    def list_reports(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for path in sorted(self.reports_dir.glob("*.json")):
            try:
                data = __import__('json').loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict):
                out.append(data)
        return out

    def load_report(self, report_id: str) -> dict[str, Any] | None:
        path = self.reports_dir / f"{report_id}.json"
        if not path.exists():
            return None
        try:
            data = __import__('json').loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def save_report(self, report: dict[str, Any]) -> None:
        path = self.reports_dir / f"{report['id']}.json"
        path.write_text(__import__('json').dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
