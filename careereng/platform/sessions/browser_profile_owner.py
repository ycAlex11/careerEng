"""Cross-process ownership locks for persistent browser profiles."""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading

from careereng.utils import now_iso


_GUARD_LOCK = threading.Lock()
_OWNED_RUNS: dict[str, str] = {}


class BrowserProfileOwnerError(RuntimeError):
    """Raised when a persistent browser profile is owned by another runtime."""


class BrowserProfileOwnerRegistry:
    """Acquire, refresh, and release a profile lock without site policy.

    The caller supplies the site label and profile path. This registry owns no
    browser runtime and does not decide whether a browser operation should run.
    """

    @staticmethod
    def lock_path(profile_dir: Path) -> Path:
        return Path(profile_dir).resolve().parent / "runtime_owner.json"

    @staticmethod
    def _key(lock_path: Path) -> str:
        return str(Path(lock_path).resolve())

    @staticmethod
    def _process_is_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    @staticmethod
    def _read(lock_path: Path) -> dict[str, object]:
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _write(lock_path: Path, payload: dict[str, object], *, exclusive: bool = False) -> None:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if exclusive:
            fd = os.open(str(lock_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            return
        lock_path.write_text(text, encoding="utf-8")

    @staticmethod
    def _payload(
        *,
        site_key: str,
        run_id: str,
        profile_dir: Path,
        output_dir: Path | None,
        status: str,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "site_key": str(site_key or ""),
            "run_id": str(run_id or ""),
            "owner_pid": os.getpid(),
            "profile_dir": str(Path(profile_dir).resolve()),
            "status": str(status or "running"),
            "updated_at": now_iso(),
        }
        if output_dir is not None:
            payload["output_dir"] = str(Path(output_dir).resolve())
        return payload

    def acquire(self, *, site_key: str, run_id: str, profile_dir: Path, output_dir: Path) -> Path:
        lock_path = self.lock_path(profile_dir)
        key = self._key(lock_path)
        payload = self._payload(
            site_key=site_key,
            run_id=run_id,
            profile_dir=profile_dir,
            output_dir=output_dir,
            status="starting",
        )
        with _GUARD_LOCK:
            for _ in range(2):
                try:
                    self._write(lock_path, payload, exclusive=True)
                except FileExistsError:
                    current = self._read(lock_path)
                    owner_pid = int(current.get("owner_pid") or 0)
                    owner_run_id = str(current.get("run_id") or "")
                    owner_alive = self._process_is_alive(owner_pid)
                    owner_is_current_process = owner_pid == os.getpid()
                    owner_known_in_process = _OWNED_RUNS.get(key) == owner_run_id
                    if owner_alive and (not owner_is_current_process or owner_known_in_process):
                        raise BrowserProfileOwnerError(
                            "browser_profile_in_use: "
                            f"site={site_key} profile={profile_dir} owner_pid={owner_pid} owner_run_id={owner_run_id}"
                        )
                    try:
                        lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                else:
                    _OWNED_RUNS[key] = run_id
                    return lock_path
        raise BrowserProfileOwnerError(f"browser_profile_owner_lock_unavailable: site={site_key} profile={profile_dir}")

    def refresh(
        self,
        *,
        site_key: str,
        run_id: str,
        profile_dir: Path,
        output_dir: Path,
        status: str = "running",
    ) -> None:
        lock_path = self.lock_path(profile_dir)
        key = self._key(lock_path)
        with _GUARD_LOCK:
            if _OWNED_RUNS.get(key) not in {None, run_id}:
                return
            self._write(
                lock_path,
                self._payload(
                    site_key=site_key,
                    run_id=run_id,
                    profile_dir=profile_dir,
                    output_dir=output_dir,
                    status=status,
                ),
            )
            _OWNED_RUNS[key] = run_id

    def release(self, *, profile_dir: Path, run_id: str = "") -> None:
        lock_path = self.lock_path(profile_dir)
        key = self._key(lock_path)
        with _GUARD_LOCK:
            current = self._read(lock_path)
            current_run_id = str(current.get("run_id") or "")
            current_pid = int(current.get("owner_pid") or 0)
            if run_id and current_run_id and current_run_id != run_id:
                return
            if current_pid and current_pid != os.getpid():
                return
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
            if not run_id or _OWNED_RUNS.get(key) == run_id:
                _OWNED_RUNS.pop(key, None)

    def release_if_reclaimable(self, *, profile_dir: Path) -> bool:
        """Remove a lock only when this process owns it or its owner is gone."""

        lock_path = self.lock_path(profile_dir)
        key = self._key(lock_path)
        with _GUARD_LOCK:
            current = self._read(lock_path)
            if not current:
                return False
            owner_pid = int(current.get("owner_pid") or 0)
            if owner_pid and owner_pid != os.getpid() and self._process_is_alive(owner_pid):
                return False
            try:
                lock_path.unlink()
            except FileNotFoundError:
                return False
            _OWNED_RUNS.pop(key, None)
            return True

    def can_reclaim(self, *, profile_dir: Path) -> bool:
        """Whether a caller may safely reclaim this dedicated profile runtime."""

        lock_path = self.lock_path(profile_dir)
        current = self._read(lock_path)
        if not current:
            return True
        owner_pid = int(current.get("owner_pid") or 0)
        return not owner_pid or owner_pid == os.getpid() or not self._process_is_alive(owner_pid)
