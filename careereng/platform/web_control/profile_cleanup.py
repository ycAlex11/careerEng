"""Generic cleanup for browser processes bound to a CareerEng profile."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import time


@dataclass(frozen=True)
class ProfileProcessCleanup:
    """Result of terminating processes that use one dedicated profile."""

    profile_dir: Path
    matched_pids: tuple[int, ...]
    terminated_pids: tuple[int, ...]
    removed_lock_paths: tuple[Path, ...]


_CHROME_PROFILE_LOCK_NAMES = ("SingletonLock", "SingletonSocket", "SingletonCookie")


def _profile_process_ids(profile_dir: Path) -> tuple[int, ...]:
    """Find only processes whose command line names this exact profile path."""

    try:
        result = subprocess.run(
            ["ps", "-ax", "-o", "pid=,command="],
            capture_output=True,
            check=False,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    if result.returncode != 0:
        return ()

    expected = str(profile_dir.resolve())
    current_pid = os.getpid()
    matched: list[int] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2 or expected not in parts[1]:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid > 0 and pid != current_pid:
            matched.append(pid)
    return tuple(sorted(set(matched), reverse=True))


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _remove_orphaned_chrome_locks(profile_dir: Path) -> tuple[Path, ...]:
    """Remove Chromium singleton entries only after the exact profile is idle."""

    if _profile_process_ids(profile_dir):
        return ()
    removed: list[Path] = []
    for name in _CHROME_PROFILE_LOCK_NAMES:
        path = profile_dir / name
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            continue
        removed.append(path)
    return tuple(removed)


def reclaim_profile_processes(profile_dir: Path | str, *, grace_seconds: float = 1.0) -> ProfileProcessCleanup:
    """Terminate only Playwright/Chrome processes using one dedicated profile.

    The caller decides that this profile is eligible for release. This function
    has no workflow knowledge; it simply prevents an orphan MCP child or Chrome
    process from retaining a CareerEng-owned ``user_data`` directory.
    """

    resolved_profile = Path(profile_dir).resolve()
    matched = _profile_process_ids(resolved_profile)
    terminated: list[int] = []
    for pid in matched:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            continue
        terminated.append(pid)

    deadline = time.monotonic() + max(0.0, float(grace_seconds))
    while terminated and time.monotonic() < deadline:
        if not any(_is_alive(pid) for pid in terminated):
            break
        time.sleep(0.05)
    for pid in terminated:
        if not _is_alive(pid):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    # Chrome can leave these symlinks after the MCP owner exits. They block a
    # later runtime even though no process still uses this dedicated profile.
    removed_lock_paths = _remove_orphaned_chrome_locks(resolved_profile)

    return ProfileProcessCleanup(
        profile_dir=resolved_profile,
        matched_pids=matched,
        terminated_pids=tuple(terminated),
        removed_lock_paths=removed_lock_paths,
    )
