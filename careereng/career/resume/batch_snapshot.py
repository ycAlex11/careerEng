"""Immutable, batch-scoped resume artifacts for browser application runs."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
from typing import Any, Iterable

from careereng.career.resume.export import default_apply_resume_pdf_path
from careereng.utils import ensure_dir, now_iso


SNAPSHOT_SCHEMA_VERSION = 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_scope_key(value: str, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or Path(normalized).name != normalized or normalized in {".", ".."}:
        raise ValueError(f"invalid {field}: {value!r}")
    return normalized


def _copy_verified(source: Path, target: Path, *, expected_sha256: str) -> Path:
    ensure_dir(target.parent)
    if not target.is_file() or _sha256(target) != expected_sha256:
        temporary = target.with_name(f".{target.name}.tmp")
        shutil.copy2(source, temporary)
        if _sha256(temporary) != expected_sha256:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(f"resume snapshot copy verification failed: {target}")
        temporary.replace(target)
    return target.resolve()


def stage_batch_resume_snapshot(
    *,
    workspace: Path,
    batch_id: str,
    site_keys: Iterable[str],
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or extend one immutable resume snapshot for an apply batch."""

    workspace = Path(workspace).resolve()
    normalized_batch_id = _safe_scope_key(batch_id, field="batch_id")
    normalized_sites = list(dict.fromkeys(_safe_scope_key(key, field="site_key") for key in site_keys))
    current = dict(existing or {})

    canonical_path = Path(str(current.get("canonical_path") or ""))
    expected_sha256 = str(current.get("sha256") or "").strip()
    if current:
        if str(current.get("batch_id") or "") != normalized_batch_id:
            raise ValueError("resume snapshot batch identity mismatch")
        if not canonical_path.is_file() or not expected_sha256:
            raise FileNotFoundError("persisted batch resume snapshot is unavailable")
        if _sha256(canonical_path) != expected_sha256:
            raise RuntimeError("persisted batch resume snapshot hash mismatch")
    else:
        source_path = default_apply_resume_pdf_path(workspace)
        if not source_path.is_file():
            raise FileNotFoundError(f"resume source not found: {source_path}")
        expected_sha256 = _sha256(source_path)
        canonical_path = (
            workspace
            / "tmp"
            / "browser_controls"
            / "batches"
            / normalized_batch_id
            / source_path.name
        )
        canonical_path = _copy_verified(source_path, canonical_path, expected_sha256=expected_sha256)
        current = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "batch_id": normalized_batch_id,
            "filename": source_path.name,
            "source_path": str(source_path.resolve()),
            "canonical_path": str(canonical_path),
            "sha256": expected_sha256,
            "version": f"sha256:{expected_sha256}",
            "created_at": now_iso(),
            "sites": {},
        }

    filename = str(current.get("filename") or canonical_path.name)
    site_snapshots = dict(current.get("sites") or {})
    for site_key in normalized_sites:
        staged_path = workspace / "tmp" / "browser_controls" / site_key / normalized_batch_id / filename
        staged_path = _copy_verified(canonical_path, staged_path, expected_sha256=expected_sha256)
        site_snapshots[site_key] = {
            "site_key": site_key,
            "batch_id": normalized_batch_id,
            "filename": filename,
            "path": str(staged_path),
            "sha256": expected_sha256,
            "version": str(current.get("version") or f"sha256:{expected_sha256}"),
        }
    current["sites"] = site_snapshots
    return current


def site_resume_snapshot(snapshot: dict[str, Any] | None, site_key: str) -> dict[str, Any]:
    """Return the persisted site-scoped resume artifact, if available."""

    payload = snapshot if isinstance(snapshot, dict) else {}
    sites = payload.get("sites") if isinstance(payload.get("sites"), dict) else {}
    site = sites.get(str(site_key or ""))
    return dict(site) if isinstance(site, dict) else {}


def validate_site_resume_snapshot(
    snapshot: dict[str, Any] | None,
    *,
    workspace: Path,
    site_key: str,
    batch_id: str,
) -> dict[str, Any]:
    """Validate that a site artifact belongs to this batch and has not changed."""

    payload = dict(snapshot or {})
    path = Path(str(payload.get("path") or ""))
    expected_root = (Path(workspace).resolve() / "tmp" / "browser_controls" / site_key / batch_id).resolve()
    if str(payload.get("site_key") or "") != site_key or str(payload.get("batch_id") or "") != batch_id:
        raise ValueError("site resume snapshot scope mismatch")
    if not path.is_file():
        raise FileNotFoundError(f"site resume snapshot is unavailable: {path}")
    try:
        path.resolve().relative_to(expected_root)
    except ValueError as exc:
        raise ValueError("site resume snapshot is outside the batch upload directory") from exc
    expected_sha256 = str(payload.get("sha256") or "").strip()
    if not expected_sha256 or _sha256(path) != expected_sha256:
        raise RuntimeError("site resume snapshot hash mismatch")
    return {**payload, "path": str(path.resolve())}
