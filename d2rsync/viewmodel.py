"""Pure view-model for the desktop window.

No Qt imports. The window renders these values and nothing else decides
what a row means.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from d2rsync.config import Config
from d2rsync.d2s import CharacterInfo, parse_d2s
from d2rsync.errors import ConfigError
from d2rsync.model import Decision, Entry, Outcome, Resolved
from d2rsync.plan import MTIME_TIE_SECONDS, preferred_side

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_SUFFIXES = (".d2s", ".key", ".keyo", ".ctl", ".ctlo", ".map")
_MA = re.compile(r"^(?P<stem>.+)\.ma\d+$", re.IGNORECASE)

MISSING_SAVE_FOLDER = "Save folder was not found. Choose it in Settings."
NO_PEER_FOUND = "No peer found"
DISCOVERY_MISS = "No single peer answered. Start the other machine, or set an IP in Settings."

STATUS_LABELS = {
    "send": "↑ Send",
    "get": "↓ Receive",
    "conflict": "⚔ Conflict",
    "ok": "✓ Up to date",
    "mixed": "↕ Mixed",
    "delete": "Delete",
    "unknown": "–",
}


@dataclass(frozen=True)
class CharacterRow:
    title: str
    subtitle: str
    detail: str
    local_time: str
    remote_time: str
    local_newer: bool
    remote_newer: bool
    status: str
    status_label: str
    kind: str


@dataclass(frozen=True)
class DashboardView:
    pill_text: str
    pill_kind: str
    warning: str | None
    rows: list[CharacterRow]
    peer_header: str
    button_enabled: bool
    button_text: str
    footer: str
    empty_text: str


@dataclass(frozen=True)
class SideCard:
    badge: str
    badge_kind: str
    heading: str
    line: str
    hint: str


@dataclass(frozen=True)
class ConflictView:
    name: str
    class_name: str
    reason: str
    winner: SideCard
    loser: SideCard
    policy: str


@dataclass(frozen=True)
class FileResult:
    filename: str
    action: str
    action_kind: str
    backup: str


@dataclass(frozen=True)
class ResultView:
    title: str
    pill: str
    conflict: ConflictView | None
    files: list[FileResult]
    archive_dir: str
    log_text: str


@dataclass
class SettingsDraft:
    save_path: str = ""
    role: str = "client"
    peer_mode: str = "auto"
    peer_host: str = ""
    port: int = 41943
    secret: str = ""
    retention_days: int = 30
    block_game: bool = True


@dataclass(frozen=True)
class ButtonState:
    enabled: bool
    text: str


def format_when(mtime: float | None, now: datetime) -> str:
    if mtime is None:
        return "—"
    moment = datetime.fromtimestamp(mtime)
    clock = moment.strftime("%H:%M")
    if moment.date() == now.date():
        return f"today {clock}"
    if moment.date() == now.date() - timedelta(days=1):
        return f"yesterday {clock}"
    return f"{moment.day} {_MONTHS[moment.month - 1]} {clock}"


def format_footer(synced_at: float | None, now: datetime) -> str:
    if synced_at is None:
        when = "never"
    else:
        when = format_when(synced_at, now)
    return f"Last sync: {when} · Backup before overwrite: on"


def group_key(filename: str) -> tuple[str, str]:
    """Return (group id, kind) where kind is character or stash."""
    lower = filename.lower()
    if lower.startswith("sharedstash") and lower.endswith(".d2i"):
        return filename, "stash"
    for suffix in _SUFFIXES:
        if lower.endswith(suffix) and len(filename) > len(suffix):
            return filename[: -len(suffix)], "character"
    match = _MA.match(filename)
    if match:
        return match.group("stem"), "character"
    return filename, "file"


def build_rows(
    local_files: dict[str, Entry],
    remote_files: dict[str, Entry],
    decisions: list[Decision] | None,
    local_d2s: dict[str, bytes],
    remote_d2s: dict[str, bytes],
    now: datetime,
) -> list[CharacterRow]:
    by_file = {item.rel: item for item in decisions} if decisions is not None else {}
    groups: dict[tuple[str, str], list[str]] = {}
    for name in set(local_files) | set(remote_files) | set(by_file):
        key = group_key(name)
        groups.setdefault(key, []).append(name)

    rows: list[CharacterRow] = []
    for (group_id, kind), names in groups.items():
        names.sort()
        local_mtime, remote_mtime = _representative_mtime(names, local_files, remote_files)
        local_newer, remote_newer = _newer_flags(local_mtime, remote_mtime)
        status = _group_status(names, by_file, compared=decisions is not None)
        info = _info_for(names, local_d2s, remote_d2s, prefer_remote=False)
        title, subtitle, detail = _labels(group_id, kind, names, info)
        rows.append(
            CharacterRow(
                title=title,
                subtitle=subtitle,
                detail=detail,
                local_time=format_when(local_mtime, now),
                remote_time=format_when(remote_mtime, now),
                local_newer=local_newer,
                remote_newer=remote_newer,
                status=status,
                status_label=STATUS_LABELS[status],
                kind=kind,
            )
        )
    rows.sort(key=lambda row: (0 if row.kind == "character" else 1, row.title.casefold()))
    return rows


def sync_button_state(
    *,
    role: str,
    game_running: bool,
    block_game: bool,
    busy: bool,
    has_secret: bool,
    has_save: bool,
    hosting: bool,
) -> ButtonState:
    if busy:
        return ButtonState(False, "Working…")
    if role == "host":
        if hosting:
            return ButtonState(False, "Listening…")
        return ButtonState(has_save and has_secret, "Start host")
    enabled = has_save and has_secret and not (game_running and block_game)
    return ButtonState(enabled, "Sync now")


def make_dashboard_view(
    *,
    rows: list[CharacterRow],
    peer_name: str,
    peer_host: str,
    connected: bool,
    searching: bool,
    role: str,
    hosting: bool,
    game_running: bool,
    peer_game_running: bool,
    block_game: bool,
    busy: bool,
    has_secret: bool,
    has_save: bool,
    synced_at: float | None,
    now: datetime,
    status_error: str = "",
) -> DashboardView:
    button = sync_button_state(
        role=role,
        game_running=game_running,
        block_game=block_game,
        busy=busy,
        has_secret=has_secret,
        has_save=has_save,
        hosting=hosting,
    )
    if role == "host" and hosting:
        pill_text = "Host active – waiting for the other machine"
        pill_kind = "ok"
    elif connected and peer_host:
        pill_text = f"{peer_name} found – {peer_host}"
        pill_kind = "ok"
    elif searching:
        pill_text = "Looking for the other machine…"
        pill_kind = "wait"
    elif status_error and status_error not in (NO_PEER_FOUND, DISCOVERY_MISS):
        pill_text = status_error
        pill_kind = "bad"
    elif role == "host":
        pill_text = "Host is not started"
        pill_kind = "wait"
    else:
        pill_text = NO_PEER_FOUND
        pill_kind = "bad"
    warning = None
    if game_running:
        warning = (
            "Diablo II: Resurrected is running (D2R.exe). Quit the game before syncing, "
            "or saved files can be overwritten."
        )
    elif peer_game_running:
        warning = (
            "Diablo II: Resurrected is running on the other machine. Quit the game there before syncing."
        )
    header = peer_name if peer_name else "Peer"
    if has_save or searching:
        empty_text = "No characters in the save folder yet."
    else:
        empty_text = MISSING_SAVE_FOLDER
    return DashboardView(
        pill_text=pill_text,
        pill_kind=pill_kind,
        warning=warning,
        rows=rows,
        peer_header=header,
        button_enabled=button.enabled,
        button_text=button.text,
        footer=format_footer(synced_at, now),
        empty_text=empty_text,
    )


def build_result_view(
    *,
    decisions: list[Decision],
    resolved: list[Resolved],
    outcomes: list[Outcome],
    local_d2s: dict[str, bytes],
    remote_d2s: dict[str, bytes],
    peer_name: str,
    dry_run: bool,
    conflict_policy: str,
    now: datetime,
    log_text: str,
) -> ResultView:
    by_decision = {item.rel: item for item in decisions}
    by_resolved = {item.rel: item for item in resolved}
    featured = _featured_conflict(decisions)
    featured_ids: set[str] = set()
    conflict_view = None
    if featured is not None:
        group_id, _kind = group_key(featured.rel)
        featured_ids = {
            name
            for name, decision in by_decision.items()
            if group_key(name)[0] == group_id and decision.action == "conflict"
        }
        conflict_view = _conflict_card(
            featured,
            by_decision,
            local_d2s,
            remote_d2s,
            peer_name,
            conflict_policy,
            now,
            _archive_hint(outcomes, featured_ids, dry_run),
        )

    files: list[FileResult] = []
    for outcome in outcomes:
        if outcome.op in ("unchanged", "none", "error"):
            continue
        if outcome.rel in featured_ids and outcome.conflict:
            continue
        kind, label = _past_action(outcome, peer_name, dry_run)
        backup = _backup_label(outcome, dry_run)
        files.append(FileResult(outcome.rel, label, kind, backup))

    changed = [
        item
        for item in outcomes
        if item.op in ("push", "pull", "delete_local", "delete_remote") and not item.conflict
    ]
    conflict_count = len({group_key(item.rel)[0] for item in outcomes if item.conflict and item.op != "skipped"})
    if dry_run:
        title = "Dry run"
        pill = _count_label(len(changed), "file would sync", "files would sync")
    else:
        title = "Sync finished"
        pill = _count_label(len(changed), "file synced", "files synced")
    if conflict_count:
        if dry_run:
            word = "conflict would be resolved" if conflict_count == 1 else "conflicts would be resolved"
        else:
            word = "conflict resolved" if conflict_count == 1 else "conflicts resolved"
        pill = f"{pill} · {conflict_count} {word}"
    if any(item.op == "error" for item in outcomes):
        title = "Sync stopped"
    archive_dir = ""
    for item in outcomes:
        if item.archive_path:
            archive_dir = str(item.archive_path.rsplit("/", 1)[0])
            break
        if item.backup_path:
            archive_dir = str(item.backup_path.rsplit("/", 1)[0])
    return ResultView(title, pill, conflict_view, files, archive_dir, log_text)


def draft_from_config(cfg: Config) -> SettingsDraft:
    host = ""
    port = cfg.port
    if cfg.peer:
        try:
            from d2rsync.config import parse_peer

            host, port = parse_peer(cfg.peer, cfg.port)
        except ConfigError:
            host = cfg.peer
    return SettingsDraft(
        save_path=cfg.save_path or "",
        role=cfg.role if cfg.role in ("client", "host") else "client",
        peer_mode=cfg.peer_mode if cfg.peer_mode in ("auto", "manual") else "auto",
        peer_host=host,
        port=port,
        secret=cfg.secret or "",
        retention_days=cfg.backup_retention_days,
        block_game=cfg.refuse_if_game_running,
    )


def apply_draft(cfg: Config, draft: SettingsDraft) -> None:
    if draft.role not in ("client", "host"):
        raise ConfigError("role must be client or host")
    if draft.peer_mode not in ("auto", "manual"):
        raise ConfigError("peer_mode must be auto or manual")
    if not 1 <= int(draft.port) <= 65535:
        raise ConfigError("port must be between 1 and 65535")
    if int(draft.retention_days) < 1:
        raise ConfigError("backup retention must be at least 1 day")
    secret = draft.secret.strip()
    if secret and len(secret) < 8:
        raise ConfigError("shared secret must be at least 8 characters")
    cfg.save_path = draft.save_path.strip() or None
    cfg.role = draft.role
    cfg.peer_mode = draft.peer_mode
    cfg.peer = draft.peer_host.strip() or None
    cfg.port = int(draft.port)
    cfg.secret = secret or None
    cfg.backup_retention_days = int(draft.retention_days)
    cfg.refuse_if_game_running = bool(draft.block_game)


def gui_conflict_policy(policy: str) -> str:
    """The window never prompts. `ask` is resolved as keep-both."""
    if policy == "newest":
        return "newest"
    return "keep-both"


def conflict_character_d2s(decisions: list[Decision], filenames: list[str]) -> list[str]:
    """`.d2s` files whose character also has a conflict, so the result card can show both levels."""
    groups = {group_key(item.rel)[0].casefold() for item in decisions if item.action == "conflict"}
    if not groups:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for name in filenames:
        if not name.lower().endswith(".d2s"):
            continue
        if group_key(name)[0].casefold() not in groups or name in seen:
            continue
        seen.add(name)
        found.append(name)
    return found


def user_error(exc: BaseException) -> str:
    if isinstance(exc, ConfigError):
        return str(exc)
    name = type(exc).__name__
    text = str(exc)
    lowered = text.lower()
    if name == "GameRunningError" or "appears to be running" in lowered:
        return "Diablo II: Resurrected is running. Quit the game on both machines before syncing."
    if name == "AuthError" or "secret" in lowered:
        return "The shared secret was rejected. Check that both machines use the same one."
    if "no peer address" in lowered:
        return "No peer address is set. Enter the IP in Settings, or choose Find automatically."
    if "no peer answered" in lowered or "more than one peer" in lowered:
        return DISCOVERY_MISS
    if "could not connect" in lowered or "connection" in lowered:
        return "Could not connect. Check the IP, port, and that the host is running."
    if name == "SavePathError" or (
        "save" in lowered and ("not find" in lowered or "does not exist" in lowered or "could not find" in lowered)
    ):
        return MISSING_SAVE_FOLDER
    return text


def _representative_mtime(names, local_files, remote_files) -> tuple[float | None, float | None]:
    d2s = [name for name in names if name.lower().endswith(".d2s")]
    chosen = d2s or names
    local_times = [local_files[name].mtime for name in chosen if name in local_files]
    remote_times = [remote_files[name].mtime for name in chosen if name in remote_files]
    return (max(local_times) if local_times else None, max(remote_times) if remote_times else None)


def _newer_flags(local_mtime: float | None, remote_mtime: float | None) -> tuple[bool, bool]:
    if local_mtime is None and remote_mtime is None:
        return False, False
    if local_mtime is None:
        return False, True
    if remote_mtime is None:
        return True, False
    delta = local_mtime - remote_mtime
    if abs(delta) <= MTIME_TIE_SECONDS:
        return False, False
    return delta > 0, delta < 0


def _group_status(names: list[str], by_file: dict[str, Decision], *, compared: bool) -> str:
    if not compared:
        return "unknown"
    flags = set()
    for name in names:
        decision = by_file.get(name)
        if decision is None:
            continue
        action = decision.action
        if action == "conflict":
            flags.add("conflict")
        elif action == "push":
            flags.add("send")
        elif action == "pull":
            flags.add("get")
        elif action in ("delete_local", "delete_remote"):
            flags.add("delete")
        elif action == "none":
            flags.add("ok")
    if "conflict" in flags:
        return "conflict"
    if "send" in flags and "get" in flags:
        return "mixed"
    if "send" in flags:
        return "send"
    if "get" in flags:
        return "get"
    if "delete" in flags:
        return "delete"
    return "ok"


def _info_for(names, local_d2s, remote_d2s, *, prefer_remote: bool) -> CharacterInfo | None:
    d2s_names = [name for name in names if name.lower().endswith(".d2s")]
    order = (remote_d2s, local_d2s) if prefer_remote else (local_d2s, remote_d2s)
    for source in order:
        for name in d2s_names:
            data = source.get(name)
            if data:
                info = parse_d2s(data)
                if info:
                    return info
    return None


def _labels(group_id: str, kind: str, names: list[str], info: CharacterInfo | None):
    if kind == "stash":
        filename = names[0]
        low = filename.lower()
        title = "Shared stash (hardcore)" if "hard" in low else "Shared stash"
        return title, "Shared stash", filename
    title = info.name if info and info.name else group_id
    if info:
        return title, f"{info.class_name} · {info.level}", ""
    return title, "Character", ""


def _featured_conflict(decisions: list[Decision]) -> Decision | None:
    conflicts = [item for item in decisions if item.action == "conflict"]
    for item in conflicts:
        if item.rel.lower().endswith(".d2s"):
            return item
    return conflicts[0] if conflicts else None


def _conflict_card(
    featured: Decision,
    by_decision: dict[str, Decision],
    local_d2s: dict[str, bytes],
    remote_d2s: dict[str, bytes],
    peer_name: str,
    conflict_policy: str,
    now: datetime,
    archive_hint: str,
) -> ConflictView:
    group_id, _kind = group_key(featured.rel)
    names = [name for name in by_decision if group_key(name)[0] == group_id]
    local_entry = featured.local
    remote_entry = featured.remote
    side = preferred_side(local_entry, remote_entry)
    local_wins = side in (None, "local")
    local_info = parse_d2s(local_d2s.get(_d2s_name(names), b"")) if _d2s_name(names) else None
    remote_info = parse_d2s(remote_d2s.get(_d2s_name(names), b"")) if _d2s_name(names) else None
    if local_info is None and featured.rel in local_d2s:
        local_info = parse_d2s(local_d2s[featured.rel])
    if remote_info is None and featured.rel in remote_d2s:
        remote_info = parse_d2s(remote_d2s[featured.rel])
    class_name = ""
    if local_wins and remote_info is None:
        class_name = local_info.class_name if local_info else ""
    elif not local_wins and local_info is None:
        class_name = remote_info.class_name if remote_info else ""
    else:
        info = remote_info or local_info
        class_name = info.class_name if info else ""
    display = (local_info.name if local_info and local_info.name else None) or (
        remote_info.name if remote_info and remote_info.name else group_id
    )
    hint_files = _extension_hint(display, names)
    local_card = _side_card(
        "This PC",
        local_info,
        local_entry.mtime if local_entry else None,
        now,
        hint_files,
        newest=local_wins,
        archive_hint=archive_hint,
    )
    remote_card = _side_card(
        peer_name or "Peer",
        remote_info,
        remote_entry.mtime if remote_entry else None,
        now,
        hint_files,
        newest=not local_wins,
        archive_hint=archive_hint,
    )
    winner, loser = (local_card, remote_card) if local_wins else (remote_card, local_card)
    if conflict_policy == "newest":
        policy = "Policy: newest – the newest copy replaces the older one. The overwritten file is kept in the backup."
    else:
        policy = (
            "Policy: keep both – the newest becomes the active character, "
            "and the older one is archived so nothing is lost."
        )
    reason = "changed on both machines since the last sync"
    if featured.reason_code == "conflict_untracked":
        reason = "both machines have the file, and they have never synced"
    elif featured.reason_code == "conflict_delete":
        reason = "deleted on one machine and changed on the other"
    return ConflictView(display, class_name, reason, winner, loser, policy)


def _side_card(heading, info, mtime, now, files_hint, *, newest: bool, archive_hint: str) -> SideCard:
    level = f"Level {info.level}" if info else "Level unknown"
    when = format_when(mtime, now)
    line = f"{level} · changed {when}" if mtime is not None else level
    if newest:
        return SideCard("★ NEWEST – becomes active", "newest", heading, line, files_hint)
    hint = archive_hint or "Saved in .d2r-sync-backups/conflicts/"
    return SideCard("📦 OLDER – archived", "older", heading, line, hint)


def _d2s_name(names: list[str]) -> str | None:
    for name in names:
        if name.lower().endswith(".d2s"):
            return name
    return None


def _extension_hint(display: str, names: list[str]) -> str:
    extras = []
    d2s = _d2s_name(names)
    for name in sorted(names):
        if name == d2s:
            continue
        lower = name.lower()
        for suffix in (".key", ".keyo", ".ctl", ".ctlo", ".map"):
            if lower.endswith(suffix):
                extras.append(suffix)
                break
        else:
            match = _MA.match(name)
            if match:
                extras.append(name[len(match.group("stem")) :])
    base = d2s or display
    if not extras:
        return base
    return base + " + " + "/".join(extras)


def _past_action(outcome: Outcome, peer_name: str, dry_run: bool) -> tuple[str, str]:
    peer = peer_name or "peer"
    if outcome.op == "push":
        verb = "Will send to" if dry_run else "Sent to"
        return "send", f"↑ {verb} {peer}"
    if outcome.op == "pull":
        verb = "Will receive from" if dry_run else "Received from"
        return "get", f"↓ {verb} {peer}"
    if outcome.op == "delete_local":
        verb = "Will delete here" if dry_run else "Deleted here"
        return "delete", verb
    if outcome.op == "delete_remote":
        verb = f"Will delete on {peer}" if dry_run else f"Deleted on {peer}"
        return "delete", verb
    if outcome.op in ("skip", "unresolved"):
        return "conflict", "Skipped"
    return "ok", outcome.op


def _backup_label(outcome: Outcome, dry_run: bool) -> str:
    path = outcome.backup_path or outcome.archive_path
    if not path:
        return "(none yet)" if dry_run else "—"
    folder = path.rsplit("/", 1)[0]
    return folder + "/"


def _archive_hint(outcomes: list[Outcome], names: set[str], dry_run: bool) -> str:
    for item in outcomes:
        if item.rel in names and item.archive_path:
            return "Saved as " + item.archive_path
        if item.rel in names and item.backup_path:
            return "Saved in " + item.backup_path.rsplit("/", 1)[0] + "/"
    return "Saved in .d2r-sync-backups/conflicts/"


def _count_label(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"
