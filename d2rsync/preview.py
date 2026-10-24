"""Read-only look at the other machine, used to fill the dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from d2rsync.backup import BackupManager
from d2rsync.client import RemoteSave, connect
from d2rsync.discover import safe_service_name
from d2rsync.errors import ProtocolError
from d2rsync.model import Decision, Entry
from d2rsync.plan import plan
from d2rsync.state import load_state
from d2rsync.store import DirectorySave


@dataclass
class Preview:
    peer_name: str
    peer_host: str
    peer_port: int
    local_files: dict[str, Entry]
    remote_files: dict[str, Entry]
    decisions: list[Decision]
    remote_d2s: dict[str, bytes]
    peer_game_running: bool
    clock_skew: float


def preview_with_peer(
    *,
    local_root: Path,
    state_file: Path,
    secret: str,
    host: str,
    port: int,
    name: str,
    timeout: float = 20.0,
) -> Preview:
    """Fetch the peer manifest and remote .d2s bytes. Writes nothing."""
    import time

    local = DirectorySave(local_root, BackupManager(local_root))
    local_state = load_state(state_file, local_root)
    local_files = local.inventory()
    conn = connect(host, port, secret, name, timeout)
    remote = RemoteSave(conn)
    try:
        # force=True so a running game still lets us read. This path never writes.
        conn.send_msg({"t": "ready", "force": True, "game_running": False})
        ready = remote._ok(conn.recv_msg())
        if ready.get("t") != "ready_ok":
            raise ProtocolError("peer did not finish the handshake")
        peer_name = str(ready.get("name") or host)
        skew = abs(time.time() - float(ready.get("time", time.time())))
        remote_files, remote_state, _when = remote.manifest()
        decisions = plan(local_files, remote_files, local_state.as_dict(), remote_state)
        remote_d2s: dict[str, bytes] = {}
        for filename in remote_files:
            if filename.lower().endswith(".d2s"):
                remote_d2s[filename] = remote.read_bytes(filename)
        return Preview(
            peer_name=safe_service_name(peer_name),
            peer_host=host,
            peer_port=port,
            local_files=local_files,
            remote_files=remote_files,
            decisions=decisions,
            remote_d2s=remote_d2s,
            peer_game_running=bool(ready.get("game_running")),
            clock_skew=skew,
        )
    finally:
        remote.close()
