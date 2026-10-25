"""Decide per file whether to copy, delete, or treat the pair as a conflict.

Content hashes are the source of truth. Modification time picks a winner
when both copies changed, and it is also copied when the bytes already
match, so a file left behind with an older timestamp catches up. A gap of
`MTIME_TIE_SECONDS` or less is not a difference. Sync state stores the hash
both sides agreed on last time, which is how a one-sided edit is
distinguished from both sides editing the same file.
"""

from __future__ import annotations

from d2rsync.model import Decision, Entry, Resolved

# Filesystems disagree about sub-second timestamps (FAT vs ext4 vs NTFS).
# A gap this small is not evidence that one copy is newer.
MTIME_TIE_SECONDS = 2.0

ACTIONS = ("none", "push", "pull", "delete_local", "delete_remote", "conflict")
POLICIES = ("keep-both", "ask", "newest")


def plan(
    local_files: dict[str, Entry],
    remote_files: dict[str, Entry],
    local_state: dict[str, str],
    remote_state: dict[str, str],
) -> list[Decision]:
    """Plan a two-way sync. Keys are flat save-file names."""
    names = set(local_files) | set(remote_files) | set(local_state) | set(remote_state)
    return [
        plan_file(
            rel,
            local_files.get(rel),
            remote_files.get(rel),
            local_state.get(rel),
            remote_state.get(rel),
        )
        for rel in sorted(names)
    ]


def plan_file(
    rel: str,
    local: Entry | None,
    remote: Entry | None,
    local_hash: str | None,
    remote_hash: str | None,
) -> Decision:
    if local is None and remote is None:
        return _decision(rel, "none", "absent", "no file on either side", local, remote)

    if local is not None and remote is not None and local.sha256 == remote.sha256:
        side = preferred_side(local, remote)
        if side == "local":
            return _decision(
                rel,
                "push",
                "mtime",
                "same contents; this copy was modified later",
                local,
                remote,
            )
        if side == "remote":
            return _decision(
                rel,
                "pull",
                "mtime",
                "same contents; the other copy was modified later",
                local,
                remote,
            )
        return _decision(rel, "none", "match", "contents match", local, remote)

    if local is not None and remote is None:
        return _only_local(rel, local, remote, local_hash, remote_hash)

    if local is None and remote is not None:
        return _only_remote(rel, local, remote, local_hash, remote_hash)

    return _both_differ(rel, local, remote, local_hash, remote_hash)


def resolve(decision: Decision, policy: str, choice: str | None = None) -> Resolved:
    """Apply a conflict policy. Non-conflicts pass through unchanged.

    `choice` is used when policy is `ask`: local, remote, both, or skip.
    When policy is `ask` and choice is omitted, the result stays unresolved
    so a dry run can report it without prompting.
    """
    if policy not in POLICIES:
        raise ValueError(f"unknown conflict policy: {policy}")
    if decision.action not in ACTIONS:
        raise ValueError(f"unknown action: {decision.action}")

    if decision.action != "conflict":
        return Resolved(
            rel=decision.rel,
            op=decision.action,
            reason_code=decision.reason_code,
            reason=decision.reason,
            conflict=False,
            archive_loser=False,
            local=decision.local,
            remote=decision.remote,
        )

    if policy == "ask":
        if choice is None:
            return _resolved(
                decision,
                "unresolved",
                "needs_choice",
                "conflict needs a choice",
                conflict=True,
                archive_loser=False,
            )
        return _resolve_choice(decision, choice)

    if policy == "newest":
        side = preferred_side(decision.local, decision.remote)
        if side is None:
            return _keep_both(decision, tie=True)
        return _copy_winner(decision, side, archive_loser=False, reason_code="newest")

    return _keep_both(decision, tie=preferred_side(decision.local, decision.remote) is None)


def preferred_side(local: Entry | None, remote: Entry | None) -> str | None:
    """Return local, remote, or None when mtimes tie or a side is missing equally.

    A missing file has no mtime. The side that still has bytes wins, because
    a deletion does not carry a trustworthy timestamp. None means both sides
    exist and their mtimes are too close to order.
    """
    if local is None and remote is None:
        return None
    if local is None:
        return "remote"
    if remote is None:
        return "local"
    delta = local.mtime - remote.mtime
    if abs(delta) <= MTIME_TIE_SECONDS:
        return None
    return "local" if delta > 0 else "remote"


def _only_local(
    rel: str,
    local: Entry,
    remote: Entry | None,
    local_hash: str | None,
    remote_hash: str | None,
) -> Decision:
    # The other side removed a file we have not edited since the shared baseline.
    if remote_hash is not None and local.sha256 == remote_hash and (
        local_hash is None or local_hash == remote_hash
    ):
        return _decision(
            rel,
            "delete_local",
            "delete_local",
            "deleted on the other side; this copy is unchanged",
            local,
            remote,
        )
    if remote_hash is not None and local.sha256 != remote_hash:
        return _decision(
            rel,
            "conflict",
            "conflict_delete",
            "one side deleted the file and the other side changed it",
            local,
            remote,
        )
    if local_hash is not None and remote_hash is not None and local_hash != remote_hash:
        return _decision(
            rel,
            "conflict",
            "conflict_history",
            "sync history disagrees and the file is only on one side",
            local,
            remote,
        )
    return _decision(
        rel,
        "push",
        "push_new",
        "only present on this machine",
        local,
        remote,
    )


def _only_remote(
    rel: str,
    local: Entry | None,
    remote: Entry,
    local_hash: str | None,
    remote_hash: str | None,
) -> Decision:
    if local_hash is not None and remote.sha256 == local_hash and (
        remote_hash is None or remote_hash == local_hash
    ):
        return _decision(
            rel,
            "delete_remote",
            "delete_remote",
            "deleted on this machine; the other copy is unchanged",
            local,
            remote,
        )
    if local_hash is not None and remote.sha256 != local_hash:
        return _decision(
            rel,
            "conflict",
            "conflict_delete",
            "one side deleted the file and the other side changed it",
            local,
            remote,
        )
    if local_hash is not None and remote_hash is not None and local_hash != remote_hash:
        return _decision(
            rel,
            "conflict",
            "conflict_history",
            "sync history disagrees and the file is only on one side",
            local,
            remote,
        )
    return _decision(
        rel,
        "pull",
        "pull_new",
        "only present on the other machine",
        local,
        remote,
    )


def _both_differ(
    rel: str,
    local: Entry | None,
    remote: Entry | None,
    local_hash: str | None,
    remote_hash: str | None,
) -> Decision:
    assert local is not None and remote is not None
    if local_hash is not None and remote_hash is not None and local_hash != remote_hash:
        return _decision(
            rel,
            "conflict",
            "conflict_history",
            "sync history disagrees and the two copies differ",
            local,
            remote,
        )

    local_changed, remote_changed = _change_flags(local, remote, local_hash, remote_hash)
    if local_changed and remote_changed:
        code = "conflict_untracked" if local_hash is None and remote_hash is None else "conflict_both"
        reason = (
            "both sides have this file and there is no previous sync"
            if code == "conflict_untracked"
            else "both sides changed since the last sync"
        )
        return _decision(rel, "conflict", code, reason, local, remote)
    if local_changed and not remote_changed:
        return _decision(
            rel,
            "push",
            "push_changed",
            "changed on this machine since the last sync",
            local,
            remote,
        )
    if remote_changed and not local_changed:
        return _decision(
            rel,
            "pull",
            "pull_changed",
            "changed on the other machine since the last sync",
            local,
            remote,
        )
    return _decision(
        rel,
        "conflict",
        "conflict_both",
        "files differ",
        local,
        remote,
    )


def _change_flags(
    local: Entry,
    remote: Entry,
    local_hash: str | None,
    remote_hash: str | None,
) -> tuple[bool, bool]:
    if local_hash is not None and remote_hash is not None and local_hash == remote_hash:
        base = local_hash
        return local.sha256 != base, remote.sha256 != base
    if local_hash is not None and remote_hash is None:
        return local.sha256 != local_hash, remote.sha256 != local_hash
    if remote_hash is not None and local_hash is None:
        return local.sha256 != remote_hash, remote.sha256 != remote_hash
    return True, True


def _resolve_choice(decision: Decision, choice: str) -> Resolved:
    normalized = choice.strip().lower()
    if normalized in ("l", "local"):
        normalized = "local"
    elif normalized in ("r", "remote"):
        normalized = "remote"
    elif normalized in ("b", "both", "keep-both", "keep_both"):
        normalized = "both"
    elif normalized in ("s", "skip"):
        normalized = "skip"
    else:
        raise ValueError(f"unknown conflict choice: {choice}")

    if normalized == "skip":
        return _resolved(
            decision,
            "skip",
            "skipped",
            "left both copies alone",
            conflict=True,
            archive_loser=False,
        )
    if normalized == "both":
        return _keep_both(decision, tie=preferred_side(decision.local, decision.remote) is None)
    if normalized == "local":
        return _copy_winner(decision, "local", archive_loser=False, reason_code="chose_local")
    return _copy_winner(decision, "remote", archive_loser=False, reason_code="chose_remote")


def _keep_both(decision: Decision, *, tie: bool) -> Resolved:
    """Converge on one live file and archive the other copy.

    Save folders only load one shared stash name, and a second .d2s with a
    tweaked filename still shows up as another character. The losing bytes
    are stored under the backup conflicts directory on both machines instead
    of being left in the live folder. When mtimes tie, this machine's copy
    becomes the live file so the choice is stable.
    """
    if decision.local is None or decision.remote is None:
        side = "local" if decision.remote is None else "remote"
        return _copy_winner(
            decision,
            side,
            archive_loser=False,
            reason_code="keep_edit",
            reason="kept the edited file and did not apply the deletion",
        )
    side = "local" if tie else preferred_side(decision.local, decision.remote)
    assert side is not None
    if tie:
        reason = "modification times tie; kept this machine's copy and archived the other"
        code = "keep_both_tie"
    else:
        winner = "this machine" if side == "local" else "the other machine"
        reason = f"kept both copies; live file is the newer one from {winner}"
        code = "keep_both"
    return _copy_winner(
        decision,
        side,
        archive_loser=True,
        reason_code=code,
        reason=reason,
    )


def _copy_winner(
    decision: Decision,
    side: str,
    *,
    archive_loser: bool,
    reason_code: str,
    reason: str | None = None,
) -> Resolved:
    if side == "local":
        if decision.local is None:
            op = "delete_remote"
            text = reason or "using the deletion from this machine"
        else:
            op = "push"
            text = reason or "using the copy from this machine"
    elif side == "remote":
        if decision.remote is None:
            op = "delete_local"
            text = reason or "using the deletion from the other machine"
        else:
            op = "pull"
            text = reason or "using the copy from the other machine"
    else:
        raise ValueError(side)
    # Archiving only makes sense when the side being overwritten still has bytes.
    if archive_loser:
        if op == "push" and decision.remote is None:
            archive_loser = False
        if op == "pull" and decision.local is None:
            archive_loser = False
    return _resolved(
        decision,
        op,
        reason_code,
        text,
        conflict=True,
        archive_loser=archive_loser,
    )


def _decision(
    rel: str,
    action: str,
    reason_code: str,
    reason: str,
    local: Entry | None,
    remote: Entry | None,
) -> Decision:
    return Decision(rel, action, reason_code, reason, local, remote)


def _resolved(
    decision: Decision,
    op: str,
    reason_code: str,
    reason: str,
    *,
    conflict: bool,
    archive_loser: bool,
) -> Resolved:
    return Resolved(
        rel=decision.rel,
        op=op,
        reason_code=reason_code,
        reason=reason,
        conflict=conflict,
        archive_loser=archive_loser,
        local=decision.local,
        remote=decision.remote,
    )
