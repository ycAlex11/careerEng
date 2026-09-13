"""Explicit resume references without language or job-matching policy."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from careereng.career.resume.export import default_apply_resume_pdf_path
from careereng.utils import read_json


def variant_key(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("resume variant must be a non-empty name")
    name = value.strip()
    if Path(name).name != name or name in {".", ".."} or "\\" in name:
        raise ValueError(f"invalid resume variant: {name}")
    return name


def normalize_selection(value: dict[str, Any] | None) -> dict[str, Any]:
    raw = value if value is not None else {}
    if not isinstance(raw, dict) or set(raw) - {"default", "sites", "jobs"}:
        raise ValueError("resume_selection accepts only default, sites and jobs")
    sites = raw.get("sites", {})
    jobs = raw.get("jobs", {})
    if not isinstance(sites, dict) or not isinstance(jobs, dict):
        raise ValueError("resume sites and jobs must be mappings")
    normalized_jobs = {}
    for site, mapping in jobs.items():
        if not isinstance(mapping, dict) or any(not isinstance(key, str) or not key.strip() for key in mapping):
            raise ValueError("job resume mapping requires non-empty job IDs or URLs")
        normalized_jobs[variant_key(site)] = {key.strip(): variant_key(name) for key, name in mapping.items()}
    return {
        "default": variant_key(raw.get("default", "default")),
        "sites": {variant_key(site): variant_key(name) for site, name in sites.items()},
        "jobs": normalized_jobs,
    }


def resume_sources(workspace: Path, name: str) -> tuple[Path, Path]:
    name = variant_key(name)
    root = Path(workspace).resolve() / "cv"
    if name == "default":
        pdf = default_apply_resume_pdf_path(Path(workspace))
        markdown = root / "current" / "cv.md"
    else:
        directory = root / "variants" / name
        directory.resolve().relative_to((root / "variants").resolve())
        pdfs = sorted(path for path in (directory / "exports").glob("*.pdf") if path.is_file())
        if len(pdfs) != 1:
            raise ValueError(f"resume variant {name} requires exactly one PDF in {directory / 'exports'}")
        pdf = pdfs[0]
        markdown = directory / "cv.md"
        if not markdown.is_file():
            raise FileNotFoundError(f"resume Markdown not found: {markdown}")
    if not pdf.is_file():
        raise FileNotFoundError(f"resume PDF not found: {pdf}")
    return pdf.resolve(), markdown.resolve()


def validate_selection_sources(workspace: Path, selection: dict[str, Any]) -> None:
    names = {selection["default"], *selection["sites"].values()}
    for mapping in selection["jobs"].values():
        names.update(mapping.values())
    for name in names:
        _, markdown = resume_sources(workspace, name)
        if not markdown.is_file():
            raise FileNotFoundError(f"resume Markdown not found: {markdown}")


def job_keys(row: dict[str, Any] | None) -> list[str]:
    return [str((row or {}).get(key) or "").strip() for key in ("job_id", "site_job_id", "url") if (row or {}).get(key)]


def selected_snapshot(snapshot: dict[str, Any], site_key: str, row: dict[str, Any] | None = None) -> dict[str, Any]:
    overrides = snapshot.get("jobs", {}).get(site_key, {})
    matches = [overrides[key] for key in job_keys(row) if key in overrides]
    if matches and len({item.get("variant", item.get("version")) for item in matches}) > 1:
        raise ValueError("conflicting resume selections for the same job")
    return dict(matches[0] if matches else snapshot.get("sites", {}).get(site_key, {}))


def batch_job_resume(workspace: Path, batch_id: str, site_key: str, row: dict[str, Any] | None = None) -> dict[str, Any]:
    if not batch_id:
        return {}
    variant_key(batch_id)
    batch = read_json(Path(workspace) / "jobs" / "batches" / f"{batch_id}.json")
    return selected_snapshot(batch.get("resume_snapshot", {}), site_key, row)


def resume_context_versions(versions: dict[str, Any], resume: dict[str, Any]) -> dict[str, Any]:
    result = dict(versions)
    if resume.get("matching_hash"):
        result["cv_hash"] = resume["matching_hash"]
    return result


def read_snapshot_markdown(snapshot: dict[str, Any]) -> str:
    path = Path(snapshot["markdown_path"])
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != snapshot.get("markdown_sha256"):
        raise RuntimeError("resume Markdown snapshot hash mismatch")
    return content.decode("utf-8")


def snapshot_for_upload_path(workspace: Path, batch_id: str, site_key: str, path: str) -> dict[str, Any]:
    if not batch_id or not path:
        return {}
    variant_key(batch_id)
    batch = read_json(Path(workspace) / "jobs" / "batches" / f"{batch_id}.json")
    snapshot = batch.get("resume_snapshot", {})
    candidates = [snapshot.get("sites", {}).get(site_key, {}), *snapshot.get("jobs", {}).get(site_key, {}).values()]
    return next((dict(item) for item in candidates if item.get("path") == path), {})
