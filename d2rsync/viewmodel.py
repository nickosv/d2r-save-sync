"""Pure view-model for the Danish desktop window.

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

_MONTHS = ("jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec")
_SUFFIXES = (".d2s", ".key", ".keyo", ".ctl", ".ctlo", ".map")
_MA = re.compile(r"^(?P<stem>.+)\.ma\d+$", re.IGNORECASE)

MISSING_SAVE_FOLDER = "Gemmemappen blev ikke fundet. Vælg den under Indstillinger."

STATUS_LABELS = {
    "send": "↑ Sendes",
    "get": "↓ Hentes",
    "conflict": "⚔ Konflikt",
    "ok": "✓ Opdateret",
    "mixed": "↕ Blandet",
    "delete": "Slettes",
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


def format_da_time(mtime: float | None, now: datetime) -> str:
    if mtime is None:
        return "—"
    moment = datetime.fromtimestamp(mtime)
    clock = moment.strftime("%H:%M")
    if moment.date() == now.date():
        return f"i dag {clock}"
    if moment.date() == now.date() - timedelta(days=1):
        return f"i går {clock}"
    return f"{moment.day}. {_MONTHS[moment.month - 1]} {clock}"


def format_footer(synced_at: float | None, now: datetime) -> str:
    if synced_at is None:
        when = "aldrig"
    else:
        when = format_da_time(synced_at, now)
    return f"Sidste synk: {when} · Backup før overskrivning: til"


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
                local_time=format_da_time(local_mtime, now),
                remote_time=format_da_time(remote_mtime, now),
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
        return ButtonState(False, "Arbejder…")
    if role == "host":
        if hosting:
            return ButtonState(False, "Lytter…")
        return ButtonState(has_save and has_secret, "Start vært")
    enabled = has_save and has_secret and not (game_running and block_game)
    return ButtonState(enabled, "Synkroniser nu")


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
        pill_text = "Vært aktiv – venter på modpart"
        pill_kind = "ok"
    elif connected and peer_host:
        pill_text = f"{peer_name} fundet – {peer_host}"
        pill_kind = "ok"
    elif searching:
        pill_text = "Leder efter modpart…"
        pill_kind = "wait"
    elif role == "host":
        pill_text = "Vært er ikke startet"
        pill_kind = "wait"
    else:
        pill_text = "Ingen modpart fundet"
        pill_kind = "bad"
    warning = None
    if game_running:
        warning = (
            "Diablo II: Resurrected kører (D2R.exe). Luk spillet før synkronisering "
            "– ellers kan gemte filer blive overskrevet."
        )
    elif peer_game_running:
        warning = (
            "Diablo II: Resurrected kører på den anden enhed. Luk spillet dér før synkronisering."
        )
    header = peer_name if peer_name else "Modpart"
    if has_save or searching:
        empty_text = "Ingen karakterer i gemmemappen endnu."
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
        title = "Prøvekørsel"
        pill = f"{len(changed)} filer ville synkroniseres"
    else:
        title = "Synkronisering fuldført"
        pill = f"{len(changed)} filer synkroniseret"
    if conflict_count:
        word = "konflikt" if conflict_count == 1 else "konflikter"
        done = "ville løses" if dry_run else "løst" if conflict_count == 1 else "løst"
        pill = f"{pill} · {conflict_count} {word} {done}"
    if any(item.op == "error" for item in outcomes):
        title = "Synkronisering stoppet"
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


def danish_error(exc: BaseException) -> str:
    name = type(exc).__name__
    text = str(exc)
    lowered = text.lower()
    if name == "GameRunningError" or "appears to be running" in lowered or "kører" in lowered:
        return (
            "Diablo II: Resurrected kører. Luk spillet på begge enheder før synkronisering."
        )
    if name == "AuthError" or "secret" in lowered or "hemmelighed" in lowered:
        return "Den delte hemmelighed blev afvist. Tjek at begge enheder bruger den samme."
    if "no peer address" in lowered:
        return "Ingen modpart er angivet. Skriv IP-adressen under Indstillinger, eller vælg Find automatisk."
    if "no peer answered" in lowered or "more than one peer" in lowered:
        return "Ingen entydig modpart svarede. Start den anden enhed, eller angiv IP under Indstillinger."
    if "could not connect" in lowered or "connection" in lowered:
        return "Kunne ikke forbinde. Tjek IP, port og at værten kører."
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
        title = "Fælles stash (hardcore)" if "hard" in low else "Fælles stash"
        return title, "Delt kiste", filename
    title = info.name if info and info.name else group_id
    if info:
        return title, f"{info.class_name} · {info.level}", ""
    return title, "Karakter", ""


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
        "Denne PC",
        local_info,
        local_entry.mtime if local_entry else None,
        now,
        hint_files,
        newest=local_wins,
        archive_hint=archive_hint,
    )
    remote_card = _side_card(
        peer_name or "Modpart",
        remote_info,
        remote_entry.mtime if remote_entry else None,
        now,
        hint_files,
        newest=not local_wins,
        archive_hint=archive_hint,
    )
    winner, loser = (local_card, remote_card) if local_wins else (remote_card, local_card)
    if conflict_policy == "newest":
        policy = "Politik: nyeste – den nyeste kopi erstatter den ældre. Den overskrevne fil ligger i backup."
    else:
        policy = (
            "Politik: behold begge – den nyeste bliver den aktive karakter, "
            "den ældre gemmes i arkivet så intet går tabt."
        )
    reason = "ændret på begge enheder siden sidste synk"
    if featured.reason_code == "conflict_untracked":
        reason = "begge enheder har filen, og der er ikke synkroniseret før"
    elif featured.reason_code == "conflict_delete":
        reason = "slettet på den ene enhed og ændret på den anden"
    return ConflictView(display, class_name, reason, winner, loser, policy)


def _side_card(heading, info, mtime, now, files_hint, *, newest: bool, archive_hint: str) -> SideCard:
    level = f"Niveau {info.level}" if info else "Niveau ukendt"
    when = format_da_time(mtime, now)
    line = f"{level} · ændret {when}" if mtime is not None else level
    if newest:
        return SideCard("★ NYESTE – bliver aktiv", "newest", heading, line, files_hint)
    hint = archive_hint or "Gemmes i .d2r-sync-backups/conflicts/"
    return SideCard("📦 ÆLDRE – arkiveres", "older", heading, line, hint)


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
    peer = peer_name or "modpart"
    if outcome.op == "push":
        verb = "Sendes til" if dry_run else "Sendt til"
        return "send", f"↑ {verb} {peer}"
    if outcome.op == "pull":
        verb = "Hentes fra" if dry_run else "Hentet fra"
        return "get", f"↓ {verb} {peer}"
    if outcome.op == "delete_local":
        verb = "Slettes her" if dry_run else "Slettet her"
        return "delete", verb
    if outcome.op == "delete_remote":
        verb = f"Slettes på {peer}" if dry_run else f"Slettet på {peer}"
        return "delete", verb
    if outcome.op in ("skip", "unresolved"):
        return "conflict", "Sprunget over"
    return "ok", outcome.op


def _backup_label(outcome: Outcome, dry_run: bool) -> str:
    path = outcome.backup_path or outcome.archive_path
    if not path:
        return "(ingen endnu)" if dry_run else "—"
    folder = path.rsplit("/", 1)[0]
    return folder + "/"


def _archive_hint(outcomes: list[Outcome], names: set[str], dry_run: bool) -> str:
    for item in outcomes:
        if item.rel in names and item.archive_path:
            return "Gemmes som " + item.archive_path
        if item.rel in names and item.backup_path:
            return "Gemmes i " + item.backup_path.rsplit("/", 1)[0] + "/"
    if dry_run:
        return "Gemmes i .d2r-sync-backups/conflicts/"
    return "Gemmes i .d2r-sync-backups/conflicts/"
