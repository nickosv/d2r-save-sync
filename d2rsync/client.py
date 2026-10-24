"""Connect to a peer and run one sync."""

from __future__ import annotations

import secrets
import socket
import time
from pathlib import Path

from d2rsync.auth import client_proof, proofs_match, server_proof
from d2rsync.backup import BackupManager
from d2rsync.crypto import session_keys
from d2rsync.discover import safe_service_name
from d2rsync.engine import execute
from d2rsync.errors import AuthError, GameRunningError, ProtocolError, SyncIOError
from d2rsync.game import game_is_running
from d2rsync.hashing import sha256_bytes
from d2rsync.model import Decision, Entry, Outcome, Resolved
from d2rsync.plan import plan, resolve
from d2rsync.protocol import PROTOCOL_VERSION, Conn
from d2rsync.state import load_state
from d2rsync.store import MAX_FILE_BYTES, DirectorySave

CLOCK_SKEW_WARN_SECONDS = 120.0


class RemoteSave:
    """SaveStore backed by an authenticated connection."""

    def __init__(self, conn: Conn):
        self.conn = conn

    def read_bytes(self, rel: str) -> bytes:
        self.conn.send_msg({"t": "get", "rel": rel})
        meta = self._ok(self.conn.recv_msg())
        if meta.get("t") != "file" or meta.get("rel") != rel:
            raise ProtocolError("peer did not send the requested file")
        data = self.conn.recv_bytes(MAX_FILE_BYTES)
        if len(data) != int(meta["size"]) or sha256_bytes(data) != meta["sha256"]:
            raise ProtocolError(f"peer sent damaged bytes for {rel}")
        return data

    def write_bytes(self, rel: str, data: bytes, mtime: float) -> None:
        digest = sha256_bytes(data)
        self.conn.send_msg(
            {"t": "put", "rel": rel, "size": len(data), "mtime": mtime, "sha256": digest}
        )
        self.conn.send_bytes(data)
        reply = self._ok(self.conn.recv_msg())
        if reply.get("t") != "put_ok" or reply.get("sha256") != digest:
            raise ProtocolError(f"peer did not accept {rel}")

    def remove(self, rel: str) -> None:
        self.conn.send_msg({"t": "delete", "rel": rel})
        reply = self._ok(self.conn.recv_msg())
        if reply.get("t") != "delete_ok":
            raise ProtocolError(f"peer did not delete {rel}")

    def archive_conflict(self, rel: str, data: bytes, label: str) -> str:
        digest = sha256_bytes(data)
        self.conn.send_msg(
            {
                "t": "archive",
                "rel": rel,
                "label": label,
                "size": len(data),
                "sha256": digest,
            }
        )
        self.conn.send_bytes(data)
        reply = self._ok(self.conn.recv_msg())
        if reply.get("t") != "archive_ok":
            raise ProtocolError(f"peer did not archive {rel}")
        return str(reply.get("path", ""))

    def commit(self, rel: str, digest: str | None) -> None:
        self.conn.send_msg({"t": "commit", "rel": rel, "sha256": digest})
        reply = self._ok(self.conn.recv_msg())
        if reply.get("t") != "commit_ok":
            raise ProtocolError(f"peer did not record state for {rel}")

    def manifest(self) -> tuple[dict[str, Entry], dict[str, str], float]:
        self.conn.send_msg({"t": "manifest"})
        reply = self._ok(self.conn.recv_msg())
        if reply.get("t") != "manifest":
            raise ProtocolError("peer did not send a manifest")
        files: dict[str, Entry] = {}
        raw_files = reply.get("files") or {}
        if not isinstance(raw_files, dict):
            raise ProtocolError("manifest files are malformed")
        for name, payload in raw_files.items():
            files[str(name)] = Entry.from_json(payload)
        raw_state = reply.get("state") or {}
        if not isinstance(raw_state, dict):
            raise ProtocolError("manifest state is malformed")
        state = {str(name): str(digest) for name, digest in raw_state.items()}
        return files, state, float(reply.get("time", time.time()))

    def close(self) -> None:
        try:
            self.conn.send_msg({"t": "bye"})
            self.conn.recv_msg()
        except (ProtocolError, OSError):
            pass
        try:
            self.conn.sock.close()
        except OSError:
            pass

    def _ok(self, message: dict) -> dict:
        if message.get("t") == "error":
            code = message.get("code")
            text = str(message.get("message") or "peer rejected the request")
            if code == "game_running":
                raise GameRunningError(text)
            if code == "auth":
                raise AuthError(text)
            raise SyncIOError(text)
        return message


def connect(host: str, port: int, secret: str, name: str, timeout: float) -> Conn:
    try:
        raw = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        raise SyncIOError(f"could not connect to {host}:{port}: {exc}") from exc
    raw.settimeout(timeout)
    conn = Conn(raw)
    try:
        client_nonce = secrets.token_bytes(16)
        conn.send_msg(
            {
                "t": "hello",
                "v": PROTOCOL_VERSION,
                "name": safe_service_name(name),
                "nonce": client_nonce.hex(),
            }
        )
        challenge = conn.recv_msg()
        if challenge.get("t") == "error":
            raise ProtocolError(str(challenge.get("message") or "handshake failed"))
        if challenge.get("t") != "challenge" or challenge.get("v") != PROTOCOL_VERSION:
            raise ProtocolError(
                "protocol version mismatch; update d2r-save-sync on both machines"
            )
        try:
            server_nonce = bytes.fromhex(str(challenge["nonce"]))
        except (KeyError, ValueError) as exc:
            raise ProtocolError("peer sent a bad challenge") from exc
        conn.send_msg({"t": "auth", "proof": client_proof(secret, client_nonce, server_nonce)})
        reply = conn.recv_msg()
        if reply.get("t") == "error":
            raise AuthError(str(reply.get("message") or "shared secret rejected"))
        expected = server_proof(secret, client_nonce, server_nonce)
        if reply.get("t") != "auth" or not proofs_match(expected, str(reply.get("proof", ""))):
            raise AuthError("the other machine rejected the secret, or its secret does not match")
        enc_key, mac_key = session_keys(secret, client_nonce, server_nonce)
        conn.enable_encryption(enc_key, mac_key)
    except Exception:
        try:
            raw.close()
        except OSError:
            pass
        raise
    return conn


def sync_with_peer(
    *,
    local_root: Path,
    state_file: Path,
    secret: str,
    host: str,
    port: int,
    conflict: str,
    dry_run: bool,
    force: bool,
    refuse_if_game_running: bool,
    retention_count: int,
    retention_days: int,
    name: str,
    timeout: float = 60.0,
    chooser=None,
    game_check=None,
    log=None,
) -> tuple[list[Resolved], list[Outcome], dict]:
    """Sync `local_root` with the peer. Returns the plan, outcomes, and notes."""
    log = log or _NullLog()
    check = game_check or game_is_running
    running = check()
    if running is None:
        log.warn("could not check whether the game is running on this machine")
    elif running and refuse_if_game_running and not force:
        raise GameRunningError(
            "Diablo II: Resurrected appears to be running. Close it and sync again, "
            "or pass --force to sync anyway."
        )
    elif running:
        log.warn("the game appears to be running; continuing because --force was set")

    backups = BackupManager(
        local_root,
        retention_count=retention_count,
        retention_days=retention_days,
    )
    local = DirectorySave(local_root, backups)
    local_state = load_state(state_file, local_root)
    local_files = local.inventory()
    conn = connect(host, port, secret, name, timeout)
    remote = RemoteSave(conn)
    notes: dict = {"peer_name": host}
    try:
        conn.send_msg({"t": "ready", "force": force, "game_running": bool(running)})
        ready = remote._ok(conn.recv_msg())
        if ready.get("t") != "ready_ok":
            raise ProtocolError("peer did not finish the handshake")
        notes["peer_name"] = str(ready.get("name") or host)
        notes["peer_time"] = float(ready.get("time", time.time()))
        skew = abs(time.time() - notes["peer_time"])
        notes["clock_skew"] = skew
        if skew > CLOCK_SKEW_WARN_SECONDS:
            log.warn(
                f"clocks differ by {int(skew)} seconds. "
                "Newest-file conflict choices use modification times and may be wrong."
            )
        if ready.get("game_running") and not force:
            raise GameRunningError(
                "Diablo II: Resurrected appears to be running on the other machine. "
                "Close it and sync again, or pass --force."
            )
        if ready.get("game_running"):
            log.warn("the game appears to be running on the other machine; continuing")
        remote_files, remote_state, _remote_time = remote.manifest()
        decisions = plan(local_files, remote_files, local_state.as_dict(), remote_state)
        resolved = [_resolve_one(item, conflict, dry_run, chooser) for item in decisions]

        def on_synced(rel: str, digest: str | None) -> None:
            local_state.set_hash(rel, digest)
            local_state.save()
            remote.commit(rel, digest)

        outcomes = execute(
            local,
            remote,
            resolved,
            on_synced=on_synced,
            dry_run=dry_run,
            loser_label_for_local=f"from-{safe_service_name(name)}",
            loser_label_for_remote=f"from-{notes['peer_name']}",
        )
        if not dry_run and not any(item.op == "error" for item in outcomes):
            backups.prune()
        return resolved, outcomes, notes
    finally:
        remote.close()


def _resolve_one(decision: Decision, conflict: str, dry_run: bool, chooser) -> Resolved:
    if decision.action == "conflict" and conflict == "ask" and not dry_run:
        if chooser is None:
            raise SyncIOError(
                "a conflict needs a choice, but there is no terminal to ask. "
                "Re-run with --conflict keep-both or --conflict newest."
            )
        choice = chooser(decision)
        return resolve(decision, conflict, choice)
    return resolve(decision, conflict, None)


class _NullLog:
    def info(self, message: str) -> None:
        return None

    def warn(self, message: str) -> None:
        return None

    def error(self, message: str) -> None:
        return None
