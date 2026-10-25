"""TCP listener. One authenticated peer at a time."""

from __future__ import annotations

import secrets
import socket
import threading
import time
from dataclasses import dataclass

from d2rsync.auth import client_proof, proofs_match, server_proof
from d2rsync.crypto import session_keys
from d2rsync.discover import DiscoveryServer, safe_service_name
from d2rsync.errors import GameRunningError, ProtocolError, SyncIOError
from d2rsync.game import game_is_running
from d2rsync.hashing import sha256_bytes
from d2rsync.model import Entry, Outcome
from d2rsync.names import (
    block_list,
    list_save_filenames,
    load_d2s_bytes,
    name_is_blocked,
    omit_blocked,
    online_character_names,
    protocol_names,
    validate_rel,
)
from d2rsync.protocol import PROTOCOL_VERSION, Conn
from d2rsync.state import SyncState
from d2rsync.store import MAX_FILE_BYTES, DirectorySave


@dataclass(frozen=True)
class HostNotice:
    """What the listener tells the host window about the peer it is serving."""

    phase: str
    peer_name: str = ""
    peer_host: str = ""
    detail: str = ""
    outcomes: tuple = ()
    synced_at: float | None = None
    show_result: bool = False
    dry_run: bool = False
    peer_files: dict | None = None


class Server:
    def __init__(
        self,
        save: DirectorySave,
        state: SyncState,
        secret: str,
        *,
        host: str = "0.0.0.0",
        port: int = 41943,
        name: str = "d2r",
        discover_port: int = 41942,
        refuse_if_game_running: bool = True,
        discover: bool = True,
        once: bool = False,
        timeout: float = 60.0,
        fail_delay: float = 1.0,
        game_check=None,
        on_session=None,
        on_notice=None,
        exclude_characters=None,
        log=None,
    ):
        self.save = save
        self.state = state
        self.secret = secret
        self.host = host
        self.port = port
        self.name = safe_service_name(name)
        self.discover_port = discover_port
        self.refuse_if_game_running = refuse_if_game_running
        self.discover = discover
        self.once = once
        self.timeout = timeout
        self.fail_delay = fail_delay
        self.game_check = game_check or game_is_running
        self.on_session = on_session
        self.on_notice = on_notice
        self.exclude_characters = block_list(list(exclude_characters or []))
        self.log = log or _NullLog()
        self._skip_names: list[str] = []
        self._outcomes: list[Outcome] = []
        self._session_failed = False
        self._mode = "sync"
        self.bound_port: int | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._sock: socket.socket | None = None

    def wait_until_listening(self, timeout: float = 5.0) -> None:
        if not self._ready.wait(timeout):
            raise SyncIOError("server did not start listening")

    def serve_forever(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(2)
        sock.settimeout(0.3)
        self._sock = sock
        self.bound_port = sock.getsockname()[1]
        discovery: DiscoveryServer | None = None
        if self.discover:
            discovery = DiscoveryServer(self.bound_port, self.name, self.discover_port)
            try:
                discovery.start()
            except OSError as exc:
                self.log.warn(f"LAN discovery is off ({exc})")
                discovery = None
        self._ready.set()
        self.log.info(
            f"Listening on {self.host}:{self.bound_port} for one peer at a time. Ctrl+C stops."
        )
        try:
            while not self._stop.is_set():
                try:
                    conn, addr = sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                completed = False
                try:
                    completed = self.handle(conn, addr)
                except Exception as exc:  # noqa: BLE001 — keep the listener up
                    self.log.error(f"{addr[0]}: {exc}")
                finally:
                    try:
                        conn.close()
                    except OSError:
                        pass
                if completed and self.on_session is not None:
                    try:
                        self.on_session()
                    except Exception as exc:  # noqa: BLE001 — listener stays up
                        self.log.error(f"session callback failed: {exc}")
                if self.once and completed:
                    break
        finally:
            if discovery is not None:
                discovery.stop()
            try:
                sock.close()
            except OSError:
                pass
            self._stop.set()

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass

    def handle(self, raw: socket.socket, addr) -> bool:
        raw.settimeout(self.timeout)
        conn = Conn(raw)
        hello = conn.recv_msg()
        if hello.get("t") != "hello":
            conn.send_msg({"t": "error", "code": "protocol", "message": "expected hello"})
            return False
        if hello.get("v") != PROTOCOL_VERSION:
            conn.send_msg(
                {
                    "t": "error",
                    "code": "version",
                    "message": "protocol version mismatch; update d2r-save-sync on both machines",
                }
            )
            return False
        try:
            client_nonce = bytes.fromhex(str(hello.get("nonce", "")))
        except ValueError:
            conn.send_msg({"t": "error", "code": "protocol", "message": "bad nonce"})
            return False
        if len(client_nonce) != 16:
            conn.send_msg({"t": "error", "code": "protocol", "message": "bad nonce"})
            return False
        server_nonce = secrets.token_bytes(16)
        conn.send_msg(
            {
                "t": "challenge",
                "v": PROTOCOL_VERSION,
                "name": self.name,
                "nonce": server_nonce.hex(),
            }
        )
        auth = conn.recv_msg()
        expected = client_proof(self.secret, client_nonce, server_nonce)
        if auth.get("t") != "auth" or not proofs_match(expected, str(auth.get("proof", ""))):
            time.sleep(self.fail_delay)
            conn.send_msg({"t": "error", "code": "auth", "message": "shared secret rejected"})
            self.log.warn(f"rejected {addr[0]}: shared secret did not match")
            return False
        conn.send_msg({"t": "auth", "proof": server_proof(self.secret, client_nonce, server_nonce)})
        enc_key, mac_key = session_keys(self.secret, client_nonce, server_nonce)
        conn.enable_encryption(enc_key, mac_key)
        peer_name = safe_service_name(str(hello.get("name") or "peer"))
        peer_host = str(addr[0])
        self.log.info(f"{peer_name} at {peer_host} authenticated")
        self._notice("connected", peer_name, peer_host, detail=f"{peer_name} connected.")
        return self._session(conn, peer_name, peer_host)

    def _session(self, conn: Conn, peer_name: str, peer_host: str) -> bool:
        self._outcomes = []
        self._session_failed = False
        self._mode = "sync"
        try:
            ready = conn.recv_msg()
        except ProtocolError as exc:
            self._notice(
                "finished",
                peer_name,
                peer_host,
                detail=f"{peer_name} disconnected.",
            )
            self.log.warn(f"{peer_name}: {exc}")
            return False
        if ready.get("t") != "ready":
            conn.send_msg({"t": "error", "code": "protocol", "message": "expected ready"})
            self._notice("finished", peer_name, peer_host, detail=f"{peer_name} disconnected.")
            return False
        force = bool(ready.get("force"))
        mode = str(ready.get("mode") or "sync")
        if mode not in ("sync", "preview", "dry-run"):
            mode = "sync"
        self._mode = mode
        filenames = list_save_filenames(self.save.root)
        online = online_character_names(filenames, load_d2s_bytes(self.save.root, filenames))
        client_excluded = protocol_names(ready.get("exclude"))
        self._skip_names = block_list(self.exclude_characters, online, client_excluded)
        running = self.game_check()
        if running is None:
            self.log.warn("could not check whether the game is running on this machine")
        if running and self.refuse_if_game_running and not force:
            conn.send_msg(
                {
                    "t": "error",
                    "code": "game_running",
                    "message": "Diablo II: Resurrected is running on this machine",
                }
            )
            self.log.warn(f"{peer_name}: refused because the game is running")
            self._notice(
                "finished",
                peer_name,
                peer_host,
                detail=f"Refused {peer_name} because Diablo II: Resurrected is running.",
            )
            return False
        if running:
            self.log.warn(f"{peer_name}: game is running; continuing because force was set")
        reported = block_list(self.exclude_characters, online)
        conn.send_msg(
            {
                "t": "ready_ok",
                "game_running": bool(running),
                "name": self.name,
                "time": time.time(),
                "exclude": reported,
            }
        )
        while not self._stop.is_set():
            try:
                message = conn.recv_msg()
            except ProtocolError as exc:
                self.log.warn(f"{peer_name}: {exc}")
                self._notice("finished", peer_name, peer_host, detail=f"{peer_name} disconnected.")
                return False
            kind = message.get("t")
            if kind == "bye":
                try:
                    self.save.backups.prune()
                except OSError as exc:
                    self.log.warn(f"could not prune backups: {exc}")
                self.log.info(f"{peer_name} finished")
                reported = _entries(message.get("files")) if "files" in message else None
                self._finish_session(peer_name, peer_host, reported)
                conn.send_msg({"t": "bye_ok"})
                return True
            try:
                self._dispatch(conn, message, force, peer_name)
            except GameRunningError as exc:
                self._session_failed = True
                conn.send_msg({"t": "error", "code": "game_running", "message": str(exc)})
            except (SyncIOError, ProtocolError, ValueError, OSError) as exc:
                self._session_failed = True
                conn.send_msg({"t": "error", "code": "rejected", "message": str(exc)})
        self._notice("finished", peer_name, peer_host, detail=f"{peer_name} disconnected.")
        return False

    def _finish_session(self, peer_name: str, peer_host: str, peer_files: dict | None = None) -> None:
        if self._mode == "preview":
            self._notice("finished", peer_name, peer_host, detail="")
            return
        if self._mode == "dry-run":
            self._notice(
                "finished",
                peer_name,
                peer_host,
                detail=f"Dry run from {peer_name}. Nothing was written.",
                dry_run=True,
            )
            return
        synced_at = None
        detail = f"Synced with {peer_name}."
        if self._session_failed:
            detail = f"Sync with {peer_name} stopped."
        else:
            try:
                self.state.mark_synced(peer=peer_name, peer_files=peer_files)
                synced_at = self.state.synced_at
            except OSError as exc:
                self.log.warn(f"could not record the sync time: {exc}")
                detail = f"Sync with {peer_name} stopped."
        self._notice(
            "finished",
            peer_name,
            peer_host,
            detail=detail,
            outcomes=tuple(self._outcomes),
            synced_at=synced_at,
            show_result=True,
            peer_files=self.state.peer_entries() if peer_files is not None else None,
        )

    def _dispatch(self, conn: Conn, message: dict, force: bool, peer_name: str) -> None:
        kind = message.get("t")
        if kind == "manifest":
            self._send_manifest(conn)
            return
        if kind == "get":
            self._send_file(conn, message, peer_name)
            return
        if kind in ("put", "archive"):
            self._receive_blob(conn, message, force, peer_name)
            return
        if kind == "delete":
            self._delete(conn, message, force, peer_name)
            return
        if kind == "commit":
            self._commit(conn, message)
            return
        raise ProtocolError(f"unknown request: {kind}")

    def _send_manifest(self, conn: Conn) -> None:
        files, state = omit_blocked(self.save.inventory(), self.state.as_dict(), self._skip_names)
        conn.send_msg(
            {
                "t": "manifest",
                "files": {name: entry.to_json() for name, entry in files.items()},
                "state": state,
                "time": time.time(),
            }
        )

    def _send_file(self, conn: Conn, message: dict, peer_name: str) -> None:
        rel = self._allowed(str(message.get("rel", "")))
        data = self.save.read_bytes(rel)
        entry = self.save.inventory()[rel]
        conn.send_msg(
            {
                "t": "file",
                "rel": rel,
                "size": len(data),
                "mtime": entry.mtime,
                "sha256": sha256_bytes(data),
            }
        )
        conn.send_bytes(data)
        if self._mode != "preview":
            self._outcomes.append(Outcome(rel, "push", f"sent to {peer_name}"))
            self._progress(peer_name, f"Sent {rel}")

    def _receive_blob(self, conn: Conn, message: dict, force: bool, peer_name: str) -> None:
        size = int(message.get("size", -1))
        if size < 0 or size > MAX_FILE_BYTES:
            raise ProtocolError("file is too large")
        data = conn.recv_bytes(MAX_FILE_BYTES)
        if len(data) != size:
            raise ProtocolError("file size does not match the header")
        digest = sha256_bytes(data)
        if digest != message.get("sha256"):
            raise ProtocolError("file hash does not match the header")
        rel = self._allowed(str(message.get("rel", "")))
        self._guard_game(force)
        if message.get("t") == "archive":
            label = str(message.get("label") or "peer")
            path = self.save.archive_conflict(rel, data, label)
            conn.send_msg({"t": "archive_ok", "rel": rel, "path": path})
            self._outcomes.append(
                Outcome(rel, "archive", "conflict copy", conflict=True, archive_path=path)
            )
            self._progress(peer_name, f"Archived {rel}")
            return
        mtime = float(message.get("mtime", time.time()))
        backup = self.save.write_bytes(rel, data, mtime)
        conn.send_msg({"t": "put_ok", "rel": rel, "sha256": digest, "backup": backup})
        self._outcomes.append(Outcome(rel, "pull", f"received from {peer_name}", backup_path=backup))
        self._progress(peer_name, f"Received {rel}")

    def _delete(self, conn: Conn, message: dict, force: bool, peer_name: str) -> None:
        rel = self._allowed(str(message.get("rel", "")))
        self._guard_game(force)
        backup = self.save.remove(rel)
        conn.send_msg({"t": "delete_ok", "rel": rel, "backup": backup})
        self._outcomes.append(Outcome(rel, "delete_local", "deleted here", backup_path=backup))
        self._progress(peer_name, f"Deleted {rel}")

    def _commit(self, conn: Conn, message: dict) -> None:
        rel = self._allowed(str(message.get("rel", "")))
        digest = message.get("sha256")
        if digest is not None:
            if not isinstance(digest, str) or len(digest) != 64:
                raise ProtocolError("commit hash is invalid")
        self.state.set_hash(rel, digest)
        self.state.save()
        conn.send_msg({"t": "commit_ok", "rel": rel})

    def _allowed(self, rel: str) -> str:
        checked = validate_rel(rel)
        if name_is_blocked(checked, self._skip_names):
            raise ProtocolError(f"{checked} is an online or excluded character and is not synced")
        return checked

    def _progress(self, peer_name: str, detail: str) -> None:
        self._notice("progress", peer_name, "", detail=detail)

    def _notice(self, phase: str, peer_name: str, peer_host: str, **kwargs) -> None:
        if self.on_notice is None:
            return
        try:
            self.on_notice(HostNotice(phase, peer_name, peer_host, **kwargs))
        except Exception as exc:  # noqa: BLE001 — listener stays up
            self.log.error(f"session notice failed: {exc}")

    def _guard_game(self, force: bool) -> None:
        if force or not self.refuse_if_game_running:
            return
        running = self.game_check()
        if running:
            raise GameRunningError("Diablo II: Resurrected is running on this machine")


def _entries(raw) -> dict:
    """File metadata from a bye message. Junk entries are ignored."""
    if not isinstance(raw, dict):
        return {}
    found = {}
    for name, payload in raw.items():
        if not isinstance(name, str) or not isinstance(payload, dict):
            continue
        try:
            found[name] = Entry.from_json(payload)
        except (KeyError, TypeError, ValueError):
            continue
    return found


class _NullLog:
    def info(self, message: str) -> None:
        return None

    def warn(self, message: str) -> None:
        return None

    def error(self, message: str) -> None:
        return None
