"""Generic file revision helpers for workspace-backed data."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileRevision:
    """A cheap filesystem revision; no domain semantics are inferred."""

    modified_ns: int = 0
    size: int = 0


class RevisionedStore:
    """Expose a stable revision token for one durable workspace file."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def revision(self) -> FileRevision:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return FileRevision()
        return FileRevision(modified_ns=int(stat.st_mtime_ns), size=int(stat.st_size))

    def changed_since(self, revision: FileRevision | None) -> bool:
        return self.revision() != (revision or FileRevision())
