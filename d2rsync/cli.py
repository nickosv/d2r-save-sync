"""Command line for d2rsync."""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

from d2rsync import __version__
from d2rsync.backup import BackupManager
from d2rsync.client import sync_with_peer
from d2rsync.config import (
    apply_overrides,
    config_path,
    init_config,
    load_config,
    parse_peer,
    state_path,
)
from d2rsync.discover import discover, safe_service_name
from d2rsync.errors import D2RSyncError, GameRunningError
from d2rsync.model import Decision
from d2rsync.paths import configured_save_folder
from d2rsync.server import Server
from d2rsync.state import load_state
from d2rsync.store import DirectorySave
from d2rsync.text import (
    exit_code,
    format_entry,
    format_outcomes,
    format_plan,
    format_time,
    summary_counts,
)


class Log:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    def info(self, message: str) -> None:
        print(message)

    def warn(self, message: str) -> None:
        print(f"warning: {message}", file=sys.stderr)

    def error(self, message: str) -> None:
        print(f"error: {message}", file=sys.stderr)

    def debug(self, message: str) -> None:
        if self.verbose:
            print(f"debug: {message}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list:
        return launch_gui()
    _ensure_windows_console(args_list)
    parser = argparse.ArgumentParser(
        prog="d2rsync",
        description="Sync Diablo II: Resurrected offline saves between a Windows PC and a Steam Deck.",
    )
    parser.add_argument("--version", action="version", version=f"d2rsync {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--verbose", action="store_true", help="Print extra detail")
    common.add_argument("--config", help="Config file (default: the per-user config.json)")
    common.add_argument("--save-path", help="Save folder, instead of auto-detect or the config")

    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gui", parents=[common], help="Open the graphical window")

    init_p = sub.add_parser("init", parents=[common], help="Create a config and a shared secret")
    init_p.add_argument("--secret", help="Use this shared secret instead of generating one")
    init_p.add_argument("--force", action="store_true", help="Replace an existing config")
    init_p.add_argument("--show-secret", action="store_true", help="Print the secret even if the config already existed")

    sub.add_parser("path", parents=[common], help="Print the save folder this machine would use")
    sub.add_parser("status", parents=[common], help="List local save files and sync state")
    sub.add_parser("discover", parents=[common], help="Look for a peer on the LAN")

    serve_p = sub.add_parser("serve", parents=[common], help="Wait for the other machine to connect")
    serve_p.add_argument("--port", type=int)
    serve_p.add_argument("--discover-port", type=int)
    serve_p.add_argument("--secret")
    serve_p.add_argument("--name", help="Name shown to the other machine")
    serve_p.add_argument("--once", action="store_true", help="Exit after one successful sync")
    serve_p.add_argument("--no-discover", action="store_true", help="Do not answer LAN discovery probes")

    sync_p = sub.add_parser("sync", parents=[common], help="Connect to the other machine and sync")
    sync_p.add_argument("--peer", help="Host or host:port. Defaults to discovery, then the config")
    sync_p.add_argument("--port", type=int)
    sync_p.add_argument("--discover-port", type=int)
    sync_p.add_argument("--secret")
    sync_p.add_argument("--name")
    sync_p.add_argument("--conflict", choices=("keep-both", "ask", "newest"))
    sync_p.add_argument("--dry-run", action="store_true", help="Show the plan and do not write files")
    sync_p.add_argument(
        "--force",
        action="store_true",
        help="Sync even if the game appears to be running",
    )

    args = parser.parse_args(args_list)
    log = Log(verbose=bool(getattr(args, "verbose", False)))
    try:
        if args.cmd == "gui":
            return launch_gui()
        if args.cmd == "init":
            return cmd_init(args, log)
        if args.cmd == "path":
            return cmd_path(args, log)
        if args.cmd == "status":
            return cmd_status(args, log)
        if args.cmd == "discover":
            return cmd_discover(args, log)
        if args.cmd == "serve":
            return cmd_serve(args, log)
        if args.cmd == "sync":
            return cmd_sync(args, log)
    except GameRunningError as exc:
        log.error(str(exc))
        return 3
    except (D2RSyncError, ValueError, OSError) as exc:
        log.error(str(exc))
        if log.verbose:
            raise
        return 1
    log.error(f"unknown command: {args.cmd}")
    return 1


def launch_gui() -> int:
    try:
        from d2rsync.ui.app import run
    except ImportError:
        print(
            "The graphical window needs PySide6. Install it with: pip install 'd2r-save-sync[gui]'",
            file=sys.stderr,
        )
        return 1
    return run()


def _ensure_windows_console(args_list: list[str]) -> None:
    """A windowed PyInstaller build has no console until a CLI command asks for one."""
    if not sys.platform.startswith("win") or not getattr(sys, "frozen", False):
        return
    if not args_list or args_list[0] in ("gui", "--help", "-h", "--version"):
        return
    try:
        import ctypes

        kernel = ctypes.windll.kernel32
        if kernel.GetConsoleWindow():
            return
        if not kernel.AllocConsole():
            return
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)  # noqa: SIM115
    except OSError:
        return


def cmd_init(args, log: Log) -> int:
    cfg, created = init_config(
        Path(args.config) if args.config else None,
        secret=args.secret,
        force=args.force,
    )
    if created:
        log.info(f"Wrote {cfg.path}")
        log.info("Shared secret (copy this to the other machine):")
        log.info(f"  {cfg.secret}")
        log.info("Set save_path in that file if auto-detect picks the wrong folder.")
        log.info(f"Secret fingerprint: {cfg.fingerprint()}")
        return 0
    log.info(f"Config already exists: {cfg.path}")
    log.info(f"Secret fingerprint: {cfg.fingerprint()}")
    if args.show_secret:
        log.info(f"Shared secret: {cfg.secret_value()}")
    else:
        log.info("Pass --show-secret to print it, or --force to generate a new config.")
    return 0


def cmd_path(args, log: Log) -> int:
    cfg = _cfg(args)
    chosen, locations = _save_choice(cfg)
    log.info(str(chosen))
    for location in locations:
        marker = "chosen" if location.path.resolve() == chosen.resolve() else "also"
        log.info(
            f"  {marker}: {location.path} [{location.kind}] "
            f"{location.file_count} files"
            + (f" appid {location.appid}" if location.appid else "")
        )
    if not locations and cfg.save_path:
        log.info("  pinned by save_path in the config")
    return 0


def cmd_status(args, log: Log) -> int:
    cfg = _cfg(args)
    chosen, _locations = _save_choice(cfg)
    store = DirectorySave(chosen, BackupManager(chosen))
    state = load_state(state_path() if not args.config else Path(args.config).parent / "state.json", chosen)
    files = store.inventory()
    log.info(f"Save folder: {chosen}")
    fingerprint = cfg.fingerprint()
    if fingerprint:
        log.info(f"Secret fingerprint: {fingerprint}")
    if not files:
        log.info("No character or shared-stash files yet.")
    for name in sorted(files):
        entry = files[name]
        recorded = state.hash_for(name)
        if recorded is None:
            flag = "not synced yet"
        elif recorded == entry.sha256:
            flag = "matches last sync"
        else:
            flag = "changed since last sync"
        log.info(
            f"  {name}  {format_time(entry.mtime)}  {entry.sha256[:12]}  {flag}"
        )
    return 0


def cmd_discover(args, log: Log) -> int:
    cfg = _cfg(args)
    peers = discover(cfg.discover_port, timeout=2.5)
    if not peers:
        log.error(
            "no peer answered. Start `d2rsync serve` on the other machine, "
            "or pass --peer with its IP address."
        )
        return 1
    for peer in peers:
        log.info(peer.label())
    return 0


def cmd_serve(args, log: Log) -> int:
    cfg = _cfg(args)
    cfg = apply_overrides(
        cfg,
        port=args.port,
        discover_port=args.discover_port,
        secret=args.secret,
        name=args.name,
    )
    chosen, locations = _save_choice(cfg)
    _note_alternates(log, chosen, locations, bool(cfg.save_path))
    secret = cfg.secret_value()
    name = safe_service_name(cfg.name or socket.gethostname() or "d2r")
    store = DirectorySave(
        chosen,
        BackupManager(
            chosen,
            retention_count=cfg.backup_retention_count,
            retention_days=cfg.backup_retention_days,
        ),
    )
    state = load_state(_state_file(args), chosen)
    server = Server(
        store,
        state,
        secret,
        host=cfg.listen_host,
        port=cfg.port,
        name=name,
        discover_port=cfg.discover_port,
        refuse_if_game_running=cfg.refuse_if_game_running,
        discover=not args.no_discover,
        once=args.once,
        log=log,
    )
    log.info(f"Save folder: {chosen}")
    log.info(f"This machine's name: {name}")
    ips = local_ipv4s()
    if ips:
        log.info("Other machine can use: " + ", ".join(f"{ip}:{cfg.port}" for ip in ips))
    else:
        log.info(f"Other machine can use: <this-ip>:{cfg.port}")
    if not args.no_discover:
        log.info(f"Answering discovery probes on UDP {cfg.discover_port}")
    log.info("Allow these ports on a private network if the OS firewall asks.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Stopped.")
    return 0


def cmd_sync(args, log: Log) -> int:
    cfg = _cfg(args)
    cfg = apply_overrides(
        cfg,
        peer=args.peer,
        port=args.port,
        discover_port=args.discover_port,
        secret=args.secret,
        conflict=args.conflict,
        name=args.name,
    )
    chosen, locations = _save_choice(cfg)
    _note_alternates(log, chosen, locations, bool(cfg.save_path))
    host, port = _peer_address(cfg, log)
    name = safe_service_name(cfg.name or socket.gethostname() or "d2r")
    log.info(f"Save folder: {chosen}")
    log.info(f"Peer: {host}:{port}")
    if args.dry_run:
        log.info("Dry run.")
    resolved, outcomes, notes = sync_with_peer(
        local_root=chosen,
        state_file=_state_file(args),
        secret=cfg.secret_value(),
        host=host,
        port=port,
        conflict=cfg.conflict,
        dry_run=args.dry_run,
        force=args.force,
        refuse_if_game_running=cfg.refuse_if_game_running,
        retention_count=cfg.backup_retention_count,
        retention_days=cfg.backup_retention_days,
        name=name,
        chooser=_chooser if cfg.conflict == "ask" and not args.dry_run else None,
        log=log,
    )
    peer_name = notes.get("peer_name") or host
    log.info(f"Connected to {peer_name}")
    log.info(format_plan(resolved, dry_run=args.dry_run))
    errors = format_outcomes(outcomes)
    if errors:
        log.error(errors)
    if not args.dry_run:
        log.info(summary_counts(outcomes))
    return exit_code(outcomes)


def _cfg(args):
    path = Path(args.config) if args.config else None
    cfg = load_config(path)
    if args.save_path:
        cfg.save_path = args.save_path
    return cfg


def _state_file(args) -> Path:
    if args.config:
        return Path(args.config).resolve().parent / "state.json"
    return state_path()


def _save_choice(cfg):
    return configured_save_folder(cfg.save_path)


def _note_alternates(log: Log, chosen: Path, locations, pinned: bool) -> None:
    if pinned:
        return
    others = []
    try:
        chosen_key = chosen.resolve()
    except OSError:
        chosen_key = chosen
    for location in locations:
        try:
            key = location.path.resolve()
        except OSError:
            key = location.path
        if key != chosen_key:
            others.append(location)
    if not others:
        return
    log.warn(f"using {chosen}")
    for location in others:
        log.warn(f"also found {location.path} ({location.file_count} files, {location.kind})")
    log.warn("Set save_path in the config if this is the wrong folder.")


def _peer_address(cfg, log: Log) -> tuple[str, int]:
    if cfg.peer:
        return parse_peer(cfg.peer, cfg.port)
    log.info("Looking for a peer on the LAN...")
    peers = discover(cfg.discover_port, timeout=2.5)
    if not peers:
        raise D2RSyncError(
            "no peer answered LAN discovery. Start `d2rsync serve` on the other "
            "machine, or set peer in the config / pass --peer <ip>."
        )
    if len(peers) > 1:
        listed = ", ".join(peer.label() for peer in peers)
        raise D2RSyncError(f"more than one peer answered ({listed}). Pass --peer to choose one.")
    return peers[0].host, peers[0].port


def _chooser(decision: Decision) -> str:
    if not sys.stdin.isatty():
        raise D2RSyncError(
            "a conflict needs a choice, but stdin is not a terminal. "
            "Re-run with --conflict keep-both or --conflict newest."
        )
    print()
    print(f"Conflict: {decision.rel}")
    print(decision.reason)
    print(format_entry("local", decision.local))
    print(format_entry("remote", decision.remote))
    if decision.local is None:
        print("Local is missing. Choosing local deletes it on the other machine.")
    elif decision.remote is None:
        print("Remote is missing. Choosing remote deletes it on this machine.")
    else:
        print("Both keeps the newer file live and archives the older copy.")
    while True:
        try:
            answer = input("Choose [L]ocal, [R]emote, [B]oth, [S]kip: ")
        except EOFError as exc:
            raise D2RSyncError("no input available for the conflict prompt") from exc
        key = answer.strip().lower()
        if key in ("l", "local", "r", "remote", "b", "both", "s", "skip"):
            return key
        print("Type L, R, B, or S.")


def local_ipv4s() -> list[str]:
    found: list[str] = []

    def add(ip: str) -> None:
        if ip and not ip.startswith("127.") and ip not in found:
            found.append(ip)

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        add(sock.getsockname()[0])
    except OSError:
        pass
    finally:
        sock.close()
    return found


if __name__ == "__main__":
    raise SystemExit(main())
