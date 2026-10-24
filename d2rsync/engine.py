"""Apply a resolved plan between a local folder and any peer store."""

from __future__ import annotations

from typing import Callable, Protocol

from d2rsync.errors import SyncIOError
from d2rsync.model import Outcome, Resolved


class SaveStore(Protocol):
    def read_bytes(self, rel: str) -> bytes: ...

    def write_bytes(self, rel: str, data: bytes, mtime: float) -> None: ...

    def remove(self, rel: str) -> None: ...

    def archive_conflict(self, rel: str, data: bytes, label: str) -> str: ...


OnSynced = Callable[[str, str | None], None]


def execute(
    local: SaveStore,
    remote: SaveStore,
    items: list[Resolved],
    *,
    on_synced: OnSynced,
    dry_run: bool,
    loser_label_for_local: str,
    loser_label_for_remote: str,
) -> list[Outcome]:
    """Run the plan. Stop on the first file that fails.

    `on_synced` records the canonical hash (or None if the file was deleted)
    on both machines. It is not called for dry runs or skipped conflicts.
    """
    outcomes: list[Outcome] = []
    for item in items:
        if item.op == "none":
            digest = item.local.sha256 if item.local is not None else None
            if item.local is None and item.remote is not None:
                digest = item.remote.sha256
            if not dry_run:
                on_synced(item.rel, digest)
            outcomes.append(Outcome(item.rel, "unchanged", item.reason, False))
            continue
        if item.op in ("skip", "unresolved"):
            outcomes.append(Outcome(item.rel, "skipped", item.reason, True))
            continue
        if dry_run:
            outcomes.append(Outcome(item.rel, item.op, item.reason, item.conflict))
            continue
        try:
            detail = _apply(
                local,
                remote,
                item,
                on_synced,
                loser_label_for_local,
                loser_label_for_remote,
            )
        except (OSError, SyncIOError) as exc:
            outcomes.append(Outcome(item.rel, "error", str(exc), item.conflict))
            break
        outcomes.append(Outcome(item.rel, item.op, item.reason, item.conflict, detail))
    return outcomes


def _apply(
    local: SaveStore,
    remote: SaveStore,
    item: Resolved,
    on_synced: OnSynced,
    loser_label_for_local: str,
    loser_label_for_remote: str,
) -> str:
    detail = ""
    if item.op == "push":
        if item.local is None:
            raise SyncIOError(f"cannot push {item.rel}; it is not on this machine")
        data = local.read_bytes(item.rel)
        if item.archive_loser and item.remote is not None:
            loser = remote.read_bytes(item.rel)
            local.archive_conflict(item.rel, loser, loser_label_for_remote)
            remote.archive_conflict(item.rel, loser, loser_label_for_remote)
            detail = "archived the other machine's copy before replacing it"
        remote.write_bytes(item.rel, data, item.local.mtime)
        on_synced(item.rel, item.local.sha256)
        return detail
    if item.op == "pull":
        if item.remote is None:
            raise SyncIOError(f"cannot pull {item.rel}; it is not on the other machine")
        data = remote.read_bytes(item.rel)
        if item.archive_loser and item.local is not None:
            loser = local.read_bytes(item.rel)
            local.archive_conflict(item.rel, loser, loser_label_for_local)
            remote.archive_conflict(item.rel, loser, loser_label_for_local)
            detail = "archived this machine's copy before replacing it"
        local.write_bytes(item.rel, data, item.remote.mtime)
        on_synced(item.rel, item.remote.sha256)
        return detail
    if item.op == "delete_local":
        local.remove(item.rel)
        on_synced(item.rel, None)
        return detail
    if item.op == "delete_remote":
        remote.remove(item.rel)
        on_synced(item.rel, None)
        return detail
    raise SyncIOError(f"unknown operation {item.op} for {item.rel}")
