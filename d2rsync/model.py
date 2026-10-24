"""Plain data used by the planner and the sync engine."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Entry:
    """One save file as it exists on a single machine."""

    sha256: str
    size: int
    mtime: float

    def to_json(self) -> dict:
        return {"sha256": self.sha256, "size": self.size, "mtime": self.mtime}

    @classmethod
    def from_json(cls, data: dict) -> "Entry":
        return cls(
            sha256=str(data["sha256"]),
            size=int(data["size"]),
            mtime=float(data["mtime"]),
        )


@dataclass(frozen=True)
class Decision:
    """What the planner wants to do before conflict policy is applied."""

    rel: str
    action: str
    reason_code: str
    reason: str
    local: Entry | None
    remote: Entry | None


@dataclass(frozen=True)
class Resolved:
    """A decision turned into a concrete file operation."""

    rel: str
    op: str
    reason_code: str
    reason: str
    conflict: bool
    archive_loser: bool
    local: Entry | None
    remote: Entry | None


@dataclass(frozen=True)
class Outcome:
    """What execute actually did, or would do in a dry run."""

    rel: str
    op: str
    reason: str
    conflict: bool = False
    detail: str = ""
    backup_path: str = ""
    archive_path: str = ""
