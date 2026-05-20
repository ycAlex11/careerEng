"""Safe cleanup planner for old runtime/debug artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import time
from typing import Iterable


PROTECTED_PARTS = (
    ("browser", "user_data"),
    ("jobs", "history_jobs.json"),
    ("applications", "reviews"),
    ("reports",),
    ("cv",),
    ("profile",),
    ("intent",),
    ("memory",),
)


@dataclass(frozen=True)
class CleanupCandidate:
    path: Path
    reason: str
    size_bytes: int
    mtime: float


@dataclass(frozen=True)
class CleanupPlan:
    workspace: Path
    days: int
    cutoff_ts: float
    candidates: tuple[CleanupCandidate, ...]

    @property
    def total_bytes(self) -> int:
        return sum(candidate.size_bytes for candidate in self.candidates)


def _is_protected(path: Path, workspace: Path) -> bool:
    try:
        rel_parts = path.resolve().relative_to(workspace.resolve()).parts
    except ValueError:
        return True
    for protected in PROTECTED_PARTS:
        if len(rel_parts) >= len(protected) and rel_parts[: len(protected)] == protected:
            return True
        for idx in range(0, max(0, len(rel_parts) - len(protected) + 1)):
            if rel_parts[idx : idx + len(protected)] == protected:
                return True
    return False


def _file_candidate(path: Path, *, reason: str, cutoff_ts: float, workspace: Path) -> CleanupCandidate | None:
    if not path.is_file() or _is_protected(path, workspace):
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    if stat.st_mtime >= cutoff_ts:
        return None
    return CleanupCandidate(path=path, reason=reason, size_bytes=int(stat.st_size), mtime=float(stat.st_mtime))


def _iter_old_files(root: Path, *, reason: str, cutoff_ts: float, workspace: Path) -> Iterable[CleanupCandidate]:
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        candidate = _file_candidate(path, reason=reason, cutoff_ts=cutoff_ts, workspace=workspace)
        if candidate is not None:
            yield candidate


def _site_roots(workspace: Path, site: str = "") -> list[Path]:
    sites_dir = workspace / "sites"
    if site.strip():
        return [sites_dir / site.strip()]
    if not sites_dir.exists():
        return []
    return [path for path in sorted(sites_dir.iterdir()) if path.is_dir()]


def build_cleanup_plan(
    *,
    workspace: Path | str,
    days: int = 30,
    site: str = "",
    include_profile_backups: bool = False,
    now_ts: float | None = None,
) -> CleanupPlan:
    workspace_path = Path(workspace).resolve()
    days_value = max(0, int(days))
    cutoff_ts = float(now_ts if now_ts is not None else time.time()) - (days_value * 86400)
    candidates: list[CleanupCandidate] = []

    for site_root in _site_roots(workspace_path, site):
        candidates.extend(
            _iter_old_files(
                site_root / "events" / "traces",
                reason="old site step trace",
                cutoff_ts=cutoff_ts,
                workspace=workspace_path,
            )
        )
        event_log = site_root / "events" / "all.jsonl"
        candidate = _file_candidate(event_log, reason="old site event log", cutoff_ts=cutoff_ts, workspace=workspace_path)
        if candidate is not None:
            candidates.append(candidate)
        candidates.extend(
            _iter_old_files(
                site_root / "browser" / "runtime",
                reason="old browser runtime log",
                cutoff_ts=cutoff_ts,
                workspace=workspace_path,
            )
        )
        for smoke_path in sorted(site_root.glob("smoke-*")) + sorted(site_root.glob("smoke_*")):
            candidates.extend(
                _iter_old_files(
                    smoke_path,
                    reason="old smoke/debug artifact",
                    cutoff_ts=cutoff_ts,
                    workspace=workspace_path,
                )
            )
        if include_profile_backups:
            for backup_path in sorted((site_root / "browser").glob("user_data.backup.*")):
                candidates.extend(
                    _iter_old_files(
                        backup_path,
                        reason="old browser profile backup",
                        cutoff_ts=cutoff_ts,
                        workspace=workspace_path,
                    )
                )

    snapshots_dir = workspace_path / "search" / "company_snapshots"
    if snapshots_dir.exists():
        for history_dir in sorted(snapshots_dir.glob("*/history")):
            candidates.extend(
                _iter_old_files(
                    history_dir,
                    reason="old company snapshot history",
                    cutoff_ts=cutoff_ts,
                    workspace=workspace_path,
                )
            )

    unique: dict[Path, CleanupCandidate] = {}
    for candidate in candidates:
        unique[candidate.path.resolve()] = candidate
    return CleanupPlan(
        workspace=workspace_path,
        days=days_value,
        cutoff_ts=cutoff_ts,
        candidates=tuple(sorted(unique.values(), key=lambda item: str(item.path))),
    )


def _prune_empty_dirs(root: Path, *, stop_at: Path) -> None:
    current = root
    stop = stop_at.resolve()
    while current.exists() and current.is_dir():
        try:
            current.resolve().relative_to(stop)
        except ValueError:
            return
        if current.resolve() == stop:
            return
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def execute_cleanup_plan(plan: CleanupPlan) -> dict[str, int]:
    deleted = 0
    deleted_bytes = 0
    for candidate in plan.candidates:
        path = candidate.path
        try:
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            else:
                continue
        except OSError:
            continue
        deleted += 1
        deleted_bytes += candidate.size_bytes
        _prune_empty_dirs(path.parent, stop_at=plan.workspace)
    return {"deleted": deleted, "deleted_bytes": deleted_bytes}
