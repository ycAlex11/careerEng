"""Immutable, batch-scoped resume artifacts for browser application runs."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
from typing import Any, Iterable

from careereng.career.resume.selection import (
    normalize_selection,
    read_snapshot_markdown,
    resume_sources,
    selected_snapshot,
    validate_selection_sources,
)
from careereng.utils import ensure_dir, now_iso


SNAPSHOT_SCHEMA_VERSION = 2


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


def _stage_single_resume_snapshot(
    *,
    workspace: Path,
    batch_id: str,
    site_keys: Iterable[str],
    existing: dict[str, Any] | None = None,
    variant: str = "default",
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
        source_path, markdown_source = resume_sources(workspace, variant)
        if not source_path.is_file():
            raise FileNotFoundError(f"resume source not found: {source_path}")
        expected_sha256 = _sha256(source_path)
        canonical_path = (
            workspace
            / "tmp"
            / "browser_controls"
            / "batches"
            / normalized_batch_id
            / variant
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
            "variant": variant,
        }
        if markdown_source.is_file():
            markdown_hash = _sha256(markdown_source)
            markdown_path = _copy_verified(markdown_source, canonical_path.parent / "cv.md", expected_sha256=markdown_hash)
            matching_digest = hashlib.sha256(str(markdown_source).encode() + b"\0" + markdown_path.read_bytes()).hexdigest()
            current.update(markdown_path=str(markdown_path), markdown_sha256=markdown_hash, matching_hash=matching_digest)

    if current.get("markdown_path"):
        read_snapshot_markdown(current)

    filename = str(current.get("filename") or canonical_path.name)
    site_snapshots = dict(current.get("sites") or {})
    for site_key in normalized_sites:
        staged_path = workspace / "tmp" / "browser_controls" / site_key / normalized_batch_id / variant / filename
        staged_path = _copy_verified(canonical_path, staged_path, expected_sha256=expected_sha256)
        site_snapshots[site_key] = {
            "site_key": site_key,
            "batch_id": normalized_batch_id,
            "filename": filename,
            "path": str(staged_path),
            "sha256": expected_sha256,
            "version": str(current.get("version") or f"sha256:{expected_sha256}"),
            **{key: current[key] for key in ("variant", "markdown_path", "markdown_sha256", "matching_hash") if key in current},
        }
    current["sites"] = site_snapshots
    return current


def _clone_single_resume_snapshot(
    *,
    workspace: Path,
    source: dict[str, Any],
    target_batch_id: str,
    site_keys: Iterable[str],
) -> dict[str, Any]:
    """Copy one immutable resume version into an isolated recovery batch."""

    workspace = Path(workspace).resolve()
    normalized_batch_id = _safe_scope_key(target_batch_id, field="batch_id")
    source_path = Path(str(source.get("canonical_path") or ""))
    expected_sha256 = str(source.get("sha256") or "").strip()
    if not source_path.is_file() or not expected_sha256:
        raise FileNotFoundError("source batch resume snapshot is unavailable")
    if _sha256(source_path) != expected_sha256:
        raise RuntimeError("source batch resume snapshot hash mismatch")
    filename = str(source.get("filename") or source_path.name)
    canonical_path = _copy_verified(
        source_path,
        workspace / "tmp" / "browser_controls" / "batches" / normalized_batch_id / str(source.get("variant") or "default") / filename,
        expected_sha256=expected_sha256,
    )
    seed = {
        **source,
        "batch_id": normalized_batch_id,
        "canonical_path": str(canonical_path),
        "created_at": now_iso(),
        "sites": {},
        "recovered_from_batch_id": str(source.get("batch_id") or ""),
    }
    if source.get("markdown_path"):
        read_snapshot_markdown(source)
        seed["markdown_path"] = str(_copy_verified(Path(source["markdown_path"]), canonical_path.parent / "cv.md", expected_sha256=source["markdown_sha256"]))
    return _stage_single_resume_snapshot(
        workspace=workspace,
        batch_id=normalized_batch_id,
        site_keys=site_keys,
        existing=seed,
        variant=str(source.get("variant") or "default"),
    )


def _assemble_snapshot(variants: dict[str, Any], selection: dict[str, Any], site_keys: Iterable[str]) -> dict[str, Any]:
    result = {**variants[selection["default"]], "schema_version": SNAPSHOT_SCHEMA_VERSION, "selection": selection, "variants": variants}
    result["sites"] = {}
    result["jobs"] = {}
    for site in site_keys:
        name = selection["sites"].get(site, selection["default"])
        result["sites"][site] = variants[name]["sites"][site]
        result["jobs"][site] = {key: variants[variant]["sites"][site] for key, variant in selection["jobs"].get(site, {}).items()}
    return result


def stage_batch_resume_snapshot(
    *, workspace: Path, batch_id: str, site_keys: Iterable[str],
    existing: dict[str, Any] | None = None, selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = dict(existing or {})
    if current and not current.get("variants"):
        if selection:
            raise ValueError("cannot change the resume of an existing legacy batch")
        return _stage_single_resume_snapshot(workspace=workspace, batch_id=batch_id, site_keys=site_keys, existing=current)
    choices = normalize_selection(selection if selection is not None else current.get("selection"))
    if not current and selection is not None:
        validate_selection_sources(workspace, choices)
    if current and choices != current.get("selection"):
        raise ValueError("resume selection is immutable for an existing batch")
    sites = list(dict.fromkeys([*current.get("sites", {}), *site_keys]))
    names = {choices["default"], *choices["sites"].values()}
    for mapping in choices["jobs"].values():
        names.update(mapping.values())
    variants = {
        name: _stage_single_resume_snapshot(
            workspace=workspace, batch_id=batch_id, site_keys=sites,
            existing=current.get("variants", {}).get(name), variant=name,
        ) for name in sorted(names)
    }
    return _assemble_snapshot(variants, choices, sites)


def clone_batch_resume_snapshot(
    *, workspace: Path, source: dict[str, Any], target_batch_id: str, site_keys: Iterable[str],
) -> dict[str, Any]:
    sites = list(site_keys)
    if not source.get("variants"):
        return _clone_single_resume_snapshot(workspace=workspace, source=source, target_batch_id=target_batch_id, site_keys=sites)
    variants = {
        name: _clone_single_resume_snapshot(workspace=workspace, source=artifact, target_batch_id=target_batch_id, site_keys=sites)
        for name, artifact in source["variants"].items()
    }
    return _assemble_snapshot(variants, source["selection"], sites)


def site_resume_snapshot(snapshot: dict[str, Any] | None, site_key: str, job: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the persisted site-scoped resume artifact, if available."""

    payload = snapshot if isinstance(snapshot, dict) else {}
    return selected_snapshot(payload, site_key, job)


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
    if payload.get("markdown_path"):
        read_snapshot_markdown(payload)
    return {**payload, "path": str(path.resolve())}
