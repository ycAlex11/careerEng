"""Reentrant process/thread exclusion for workspace file transactions."""

from __future__ import annotations

import os
from pathlib import Path
import threading


class WorkspaceMutex:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.local = threading.local()

    def __enter__(self):
        self.lock.acquire()
        depth = getattr(self.local, "depth", 0)
        if depth == 0:
            handle = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                handle = self.path.open("a+b")
                if os.name == "nt":
                    import msvcrt
                    if self.path.stat().st_size == 0:
                        handle.write(b"0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX)
                self.local.handle = handle
            except BaseException:
                if handle is not None:
                    handle.close()
                self.lock.release()
                raise
        self.local.depth = depth + 1
        return self

    def __exit__(self, *args):
        self.local.depth -= 1
        try:
            if self.local.depth == 0:
                handle = self.local.handle
                try:
                    if os.name == "nt":
                        import msvcrt
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle, fcntl.LOCK_UN)
                finally:
                    handle.close()
        finally:
            self.lock.release()


_MUTEXES: dict[Path, WorkspaceMutex] = {}
_GUARD = threading.Lock()


def workspace_mutex(path: Path) -> WorkspaceMutex:
    normalized = path.resolve().with_name(path.name + ".lock")
    with _GUARD:
        return _MUTEXES.setdefault(normalized, WorkspaceMutex(normalized))
