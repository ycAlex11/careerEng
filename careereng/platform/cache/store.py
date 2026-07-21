"""Generic cache artifact persistence with no domain-level reuse decisions."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from careereng.platform.observability import PerformanceRecorder
from careereng.platform.persistence import JSONLStore, RevisionedStore, RunScopedView
from careereng.utils import ensure_dir, make_id, now_iso, read_json, safe_file_stem, write_json


CACHE_KINDS = frozenset({"runtime_capability", "context", "mapping", "sequence"})
CACHE_VALIDATION_STATUSES = frozenset({"candidate", "validated", "stale", "retired"})
_ACTIVE_STATUSES = frozenset({"candidate", "validated"})
_SCOPE_KEYS = ("site_key", "phase", "page_fingerprint")


class CacheArtifactError(ValueError):
    """Raised when an agent-supplied cache artifact violates the shared contract."""


class CacheArtifactStore:
    """Persist reusable runtime artifacts under one workspace cache root.

    The store performs only mechanical scope/version compatibility checks. It
    does not decide whether cached content is semantically safe for a live page.
    """

    def __init__(self, workspace: Path | str):
        self.workspace = Path(workspace)
        self.root = ensure_dir(self.workspace / "cache")
        self.artifacts_dir = ensure_dir(self.root / "artifacts")
        self.index_store = JSONLStore(self.root / "index.jsonl")
        self.events_store = JSONLStore(self.root / "events.jsonl")
        self._index_view = RunScopedView(RevisionedStore(self.index_store.path), self.index_store.read_all)
        self._performance = PerformanceRecorder(self.workspace)

    def lookup(
        self,
        *,
        scope: dict[str, Any],
        dependency_versions: dict[str, Any] | None = None,
        kinds: Iterable[str] | None = None,
        limit: int = 8,
        batch_id: str = "",
        turn_id: str = "",
    ) -> list[dict[str, Any]]:
        """Return compatible compact index rows without exposing full content."""

        normalized_scope = self._normalize_scope(scope)
        normalized_versions = self._normalize_versions(dependency_versions)
        allowed_kinds = self._normalize_kinds(kinds)
        candidates = [
            row
            for row in self._index_rows()
            if self._is_active(row)
            and (not allowed_kinds or str(row.get("kind") or "") in allowed_kinds)
            and self._scope_matches(row.get("scope"), normalized_scope)
            and self._versions_match(row.get("dependency_versions"), normalized_versions)
        ]
        candidates.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        selected = [self._index_payload(row) for row in candidates[: max(0, int(limit or 0))]]
        self._record_event(
            action="lookup",
            scope=normalized_scope,
            batch_id=batch_id,
            turn_id=turn_id,
            payload={"kinds": sorted(allowed_kinds), "candidate_count": len(selected)},
        )
        self._record_event(
            action="hit" if selected else "miss",
            scope=normalized_scope,
            batch_id=batch_id,
            turn_id=turn_id,
            payload={"candidate_count": len(selected)},
        )
        return selected

    def read(
        self,
        cache_id: str,
        *,
        scope: dict[str, Any],
        dependency_versions: dict[str, Any] | None = None,
        batch_id: str = "",
        turn_id: str = "",
    ) -> dict[str, Any]:
        """Read one compatible artifact and record that the agent requested it."""

        row = self._find_index_row(cache_id)
        normalized_scope = self._normalize_scope(scope)
        normalized_versions = self._normalize_versions(dependency_versions)
        if row is None:
            raise CacheArtifactError(f"cache artifact not found: {cache_id}")
        if not self._is_active(row):
            raise CacheArtifactError(f"cache artifact is not active: {cache_id}")
        if not self._scope_matches(row.get("scope"), normalized_scope):
            raise CacheArtifactError(f"cache artifact scope does not match: {cache_id}")
        if not self._versions_match(row.get("dependency_versions"), normalized_versions):
            raise CacheArtifactError(f"cache artifact dependency versions do not match: {cache_id}")
        artifact = read_json(self._artifact_path(cache_id))
        if not isinstance(artifact, dict):
            raise CacheArtifactError(f"cache artifact payload is unavailable: {cache_id}")
        self._increment_uses(cache_id, result="read")
        artifact = read_json(self._artifact_path(cache_id))
        if not isinstance(artifact, dict):
            raise CacheArtifactError(f"cache artifact payload is unavailable: {cache_id}")
        self._record_event(
            action="read",
            scope=normalized_scope,
            batch_id=batch_id,
            turn_id=turn_id,
            cache_id=cache_id,
            payload={"kind": str(row.get("kind") or "")},
        )
        return deepcopy(artifact)

    def propose(
        self,
        *,
        kind: str,
        scope: dict[str, Any],
        dependency_versions: dict[str, Any] | None,
        content: dict[str, Any],
        summary: str = "",
        source_refs: Iterable[str] | None = None,
        batch_id: str = "",
        turn_id: str = "",
    ) -> dict[str, Any]:
        """Persist an LLM-proposed cache candidate without promoting it."""

        normalized_kind = str(kind or "").strip()
        if normalized_kind not in CACHE_KINDS:
            raise CacheArtifactError(f"unsupported cache kind: {kind}")
        normalized_scope = self._normalize_scope(scope)
        if not normalized_scope.get("site_key") or not normalized_scope.get("phase"):
            raise CacheArtifactError("cache scope requires site_key and phase")
        if not isinstance(content, dict) or not content:
            raise CacheArtifactError("cache content must be a non-empty object")
        cache_id = make_id("cache")
        now = now_iso()
        artifact = {
            "cache_id": cache_id,
            "kind": normalized_kind,
            "scope": normalized_scope,
            "dependency_versions": self._normalize_versions(dependency_versions),
            "summary": str(summary or "").strip()[:1000],
            "source_refs": self._normalize_refs(source_refs),
            "content": deepcopy(content),
            "validation": {
                "status": "candidate",
                "uses": 0,
                "last_result": "",
                "last_validated_at": "",
            },
            "created_at": now,
            "updated_at": now,
        }
        write_json(self._artifact_path(cache_id), artifact)
        rows = self._index_rows()
        rows.append(self._index_row(artifact))
        self._write_index_rows(rows)
        self._record_event(
            action="proposed",
            scope=normalized_scope,
            batch_id=batch_id,
            turn_id=turn_id,
            cache_id=cache_id,
            payload={"kind": normalized_kind},
        )
        return self._index_payload(self._index_row(artifact))

    def validate(
        self,
        cache_id: str,
        *,
        status: str,
        summary: str = "",
        batch_id: str = "",
        turn_id: str = "",
    ) -> dict[str, Any]:
        """Record an agent-provided validation outcome for one artifact."""

        normalized_status = str(status or "").strip().lower()
        if normalized_status not in CACHE_VALIDATION_STATUSES - {"candidate"}:
            raise CacheArtifactError(f"unsupported cache validation status: {status}")
        row = self._find_index_row(cache_id)
        if row is None:
            raise CacheArtifactError(f"cache artifact not found: {cache_id}")
        artifact = read_json(self._artifact_path(cache_id))
        if not isinstance(artifact, dict):
            raise CacheArtifactError(f"cache artifact payload is unavailable: {cache_id}")
        now = now_iso()
        validation = artifact.get("validation") if isinstance(artifact.get("validation"), dict) else {}
        validation.update(
            {
                "status": normalized_status,
                "last_result": str(summary or "").strip()[:1000],
                "last_validated_at": now,
            }
        )
        artifact["validation"] = validation
        artifact["updated_at"] = now
        write_json(self._artifact_path(cache_id), artifact)
        self._replace_index_row(self._index_row(artifact))
        self._record_event(
            action="validated",
            scope=self._normalize_scope(artifact.get("scope")),
            batch_id=batch_id,
            turn_id=turn_id,
            cache_id=cache_id,
            payload={"status": normalized_status, "summary": validation["last_result"]},
        )
        return self._index_payload(self._index_row(artifact))

    def _increment_uses(self, cache_id: str, *, result: str) -> None:
        artifact = read_json(self._artifact_path(cache_id))
        if not isinstance(artifact, dict):
            return
        validation = artifact.get("validation") if isinstance(artifact.get("validation"), dict) else {}
        validation["uses"] = int(validation.get("uses") or 0) + 1
        validation["last_result"] = str(result or "")
        artifact["validation"] = validation
        artifact["updated_at"] = now_iso()
        write_json(self._artifact_path(cache_id), artifact)
        self._replace_index_row(self._index_row(artifact))

    def _index_rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._index_view.get() if isinstance(row, dict)]

    def _write_index_rows(self, rows: list[dict[str, Any]]) -> None:
        self.index_store.write_all(rows)
        self._index_view.replace([dict(row) for row in rows])

    def _replace_index_row(self, replacement: dict[str, Any]) -> None:
        cache_id = str(replacement.get("cache_id") or "")
        rows = self._index_rows()
        updated = False
        for index, row in enumerate(rows):
            if str(row.get("cache_id") or "") == cache_id:
                rows[index] = replacement
                updated = True
                break
        if not updated:
            rows.append(replacement)
        self._write_index_rows(rows)

    def _find_index_row(self, cache_id: str) -> dict[str, Any] | None:
        normalized_id = str(cache_id or "").strip()
        for row in reversed(self._index_rows()):
            if str(row.get("cache_id") or "") == normalized_id:
                return row
        return None

    def _artifact_path(self, cache_id: str) -> Path:
        return self.artifacts_dir / f"{safe_file_stem(cache_id)}.json"

    @staticmethod
    def _normalize_scope(scope: dict[str, Any] | None) -> dict[str, str]:
        raw = scope if isinstance(scope, dict) else {}
        return {key: str(raw.get(key) or "").strip() for key in _SCOPE_KEYS}

    @staticmethod
    def _normalize_versions(versions: dict[str, Any] | None) -> dict[str, str]:
        raw = versions if isinstance(versions, dict) else {}
        return {str(key): str(value or "").strip() for key, value in raw.items() if str(key).strip() and str(value or "").strip()}

    @staticmethod
    def _normalize_refs(source_refs: Iterable[str] | None) -> list[str]:
        return [str(item).strip() for item in source_refs or () if str(item).strip()][:20]

    @staticmethod
    def _normalize_kinds(kinds: Iterable[str] | None) -> set[str]:
        return {str(item).strip() for item in kinds or () if str(item).strip() in CACHE_KINDS}

    @staticmethod
    def _scope_matches(stored_scope: Any, requested_scope: dict[str, str]) -> bool:
        stored = stored_scope if isinstance(stored_scope, dict) else {}
        for key in _SCOPE_KEYS:
            expected = str(stored.get(key) or "").strip()
            actual = str(requested_scope.get(key) or "").strip()
            if expected and expected != actual:
                return False
        return True

    @staticmethod
    def _versions_match(stored_versions: Any, requested_versions: dict[str, str]) -> bool:
        stored = stored_versions if isinstance(stored_versions, dict) else {}
        for key, value in stored.items():
            expected = str(value or "").strip()
            if expected and requested_versions.get(str(key), "") != expected:
                return False
        return True

    @staticmethod
    def _is_active(row: dict[str, Any]) -> bool:
        validation = row.get("validation") if isinstance(row.get("validation"), dict) else {}
        return str(validation.get("status") or "candidate").strip().lower() in _ACTIVE_STATUSES

    @staticmethod
    def _index_row(artifact: dict[str, Any]) -> dict[str, Any]:
        validation = artifact.get("validation") if isinstance(artifact.get("validation"), dict) else {}
        return {
            "cache_id": str(artifact.get("cache_id") or ""),
            "kind": str(artifact.get("kind") or ""),
            "scope": dict(artifact.get("scope") or {}),
            "dependency_versions": dict(artifact.get("dependency_versions") or {}),
            "summary": str(artifact.get("summary") or "")[:1000],
            "source_refs": list(artifact.get("source_refs") or []),
            "validation": dict(validation),
            "created_at": str(artifact.get("created_at") or ""),
            "updated_at": str(artifact.get("updated_at") or ""),
        }

    @staticmethod
    def _index_payload(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "cache_id": str(row.get("cache_id") or ""),
            "kind": str(row.get("kind") or ""),
            "scope": dict(row.get("scope") or {}),
            "summary": str(row.get("summary") or ""),
            "source_refs": list(row.get("source_refs") or []),
            "validation": dict(row.get("validation") or {}),
            "updated_at": str(row.get("updated_at") or ""),
        }

    def _record_event(
        self,
        *,
        action: str,
        scope: dict[str, str],
        batch_id: str,
        turn_id: str,
        cache_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        row = {
            "event_id": make_id("cache_event"),
            "ts": now_iso(),
            "action": str(action or ""),
            "cache_id": str(cache_id or ""),
            "site_key": str(scope.get("site_key") or ""),
            "phase": str(scope.get("phase") or ""),
            "page_fingerprint": str(scope.get("page_fingerprint") or ""),
            "batch_id": str(batch_id or ""),
            "turn_id": str(turn_id or ""),
            "payload": dict(payload or {}),
        }
        self.events_store.append(row)
        self._performance.record(
            backend="platform_cache",
            operation="cache",
            cache_action=row["action"],
            cache_validation_status=str((row["payload"] or {}).get("status") or ""),
            site_key=row["site_key"],
            phase=row["phase"],
            batch_id=row["batch_id"],
            status="ok",
        )
