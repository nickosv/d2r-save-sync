"""Find a peer on the LAN.

The always-on method is a UDP broadcast probe. On Linux, if avahi-publish
and avahi-browse are installed (they often are on SteamOS), the server also
advertises `_d2rsync._tcp` and the client looks there. Port 5353 is not
bound directly, because Avahi already owns it on a Deck.
"""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
import threading
from dataclasses import dataclass

from d2rsync.errors import D2RSyncError

MAGIC = "D2RSYNC1"
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")


@dataclass(frozen=True)
class Peer:
    host: str
    port: int
    name: str

    def label(self) -> str:
        return f"{self.name} ({self.host}:{self.port})"


def safe_service_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "-", name).strip("-")
    return (cleaned or "d2r")[:40]


def encode_discover(nonce: str) -> bytes:
    return f"{MAGIC} DISCOVER {nonce}".encode("ascii")


def encode_announce(port: int, nonce: str, name: str) -> bytes:
    return f"{MAGIC} ANNOUNCE {int(port)} {nonce} {safe_service_name(name)}".encode("ascii")


def parse_datagram(data: bytes) -> dict | None:
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        return None
    parts = text.split(" ")
    if len(parts) < 3 or parts[0] != MAGIC:
        return None
    if parts[1] == "DISCOVER" and len(parts) == 3 and _nonce_ok(parts[2]):
        return {"kind": "discover", "nonce": parts[2]}
    if (
        parts[1] == "ANNOUNCE"
        and len(parts) == 5
        and parts[2].isdigit()
        and _nonce_ok(parts[3])
        and _NAME_RE.match(parts[4])
    ):
        port = int(parts[2])
        if not 1 <= port <= 65535:
            return None
        return {"kind": "announce", "port": port, "nonce": parts[3], "name": parts[4]}
    return None


class DiscoveryServer:
    """Reply to UDP probes while the TCP server is running."""

    def __init__(self, tcp_port: int, name: str, discover_port: int):
        self.tcp_port = tcp_port
        self.name = safe_service_name(name)
        self.discover_port = discover_port
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._avahi: subprocess.Popen | None = None
        self._sock: socket.socket | None = None

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", self.discover_port))
            sock.settimeout(0.4)
        except OSError:
            sock.close()
            raise
        self._sock = sock
        self._thread = threading.Thread(target=self._loop, name="d2rsync-discover", daemon=True)
        self._thread.start()
        self._start_avahi()

    def stop(self) -> None:
        self._stop.set()
        sock = getattr(self, "_sock", None)
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._avahi is not None:
            self._avahi.terminate()
            try:
                self._avahi.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._avahi.kill()
            self._avahi = None

    def _loop(self) -> None:
        sock = self._sock
        try:
            while not self._stop.is_set():
                try:
                    data, addr = sock.recvfrom(512)
                except socket.timeout:
                    continue
                except OSError:
                    break
                parsed = parse_datagram(data)
                if not parsed or parsed["kind"] != "discover":
                    continue
                reply = encode_announce(self.tcp_port, parsed["nonce"], self.name)
                try:
                    sock.sendto(reply, addr)
                except OSError:
                    continue
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _start_avahi(self) -> None:
        binary = shutil.which("avahi-publish-service")
        if not binary:
            return
        try:
            self._avahi = subprocess.Popen(
                [
                    binary,
                    "--no-fail",
                    self.name,
                    "_d2rsync._tcp",
                    str(self.tcp_port),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            self._avahi = None


def discover(
    discover_port: int,
    *,
    timeout: float = 2.0,
    nonce: str | None = None,
    extra_targets: list[tuple[str, int]] | None = None,
    broadcast: bool = True,
) -> list[Peer]:
    """Collect announcements that echo our nonce.

    By default this also broadcasts on the LAN. A directed probe passes
    ``broadcast=False`` and puts the one host in ``extra_targets``, so a
    reply from some other address cannot be mistaken for that host.
    """
    import secrets
    import time

    token = nonce or secrets.token_hex(8)
    if not _nonce_ok(token):
        raise D2RSyncError("discovery nonce is invalid")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peers: dict[tuple[str, int], Peer] = {}
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.2)
        sock.bind(("", 0))
        payload = encode_discover(token)
        deadline = time.monotonic() + timeout
        targets: list[tuple[str, int]] = []
        if broadcast:
            targets.append(("255.255.255.255", discover_port))
            targets.extend(broadcast_targets(discover_port))
        if extra_targets:
            targets.extend(extra_targets)
        next_send = 0.0
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_send:
                for target in targets:
                    try:
                        sock.sendto(payload, target)
                    except OSError:
                        continue
                next_send = now + 0.4
            try:
                data, addr = sock.recvfrom(512)
            except socket.timeout:
                continue
            parsed = parse_datagram(data)
            if not parsed or parsed["kind"] != "announce" or parsed["nonce"] != token:
                continue
            peer = Peer(addr[0], parsed["port"], parsed["name"])
            peers[(peer.host, peer.port)] = peer
    finally:
        sock.close()
    avahi_peers = browse_avahi(timeout=min(1.0, timeout))
    for peer in avahi_peers:
        peers.setdefault((peer.host, peer.port), peer)
    return sorted(peers.values(), key=lambda item: (item.name, item.host, item.port))


def probe(host: str, discover_port: int, *, timeout: float = 1.0, nonce: str | None = None) -> Peer | None:
    """Ask one host. The reply has to come from that host.

    A broadcast probe of this same machine is answered from the LAN address
    as well as from loopback. Those are different peers. Returning whichever
    reply sorts first would report the LAN address for a loopback probe on
    some networks (``10.1.0.54`` sorts before ``127.0.0.1``) and could also
    return a different PC. LAN discovery stays on :func:`discover`.
    """
    found = discover(
        discover_port,
        timeout=timeout,
        nonce=nonce,
        extra_targets=[(host, discover_port)],
        broadcast=False,
    )
    return _matching_peer(found, host)


def _matching_peer(peers: list[Peer], host: str) -> Peer | None:
    acceptable = _probe_sources(host)
    for peer in peers:
        if peer.host in acceptable:
            return peer
    return None


def _probe_sources(host: str) -> set[str]:
    """Addresses that count as a reply from ``host``.

    ``localhost`` and ``127.0.0.1`` are the same target: the packet comes
    back from the numeric address. A DNS name counts when the reply source
    is one of its IPv4 addresses.
    """
    if host in ("127.0.0.1", "localhost"):
        return {"127.0.0.1", "localhost"}
    found = {host}
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM)
    except socket.gaierror:
        return found
    for info in infos:
        found.add(info[4][0])
    return found


def broadcast_targets(discover_port: int) -> list[tuple[str, int]]:
    """Best-effort subnet broadcasts. 255.255.255.255 is always tried too."""
    targets: list[tuple[str, int]] = []
    route = _read_linux_routes()
    for address in route:
        targets.append((address, discover_port))
    return targets


def browse_avahi(*, timeout: float = 1.0) -> list[Peer]:
    binary = shutil.which("avahi-browse")
    if not binary:
        return []
    try:
        completed = subprocess.run(
            [binary, "-rpt", "-f", "_d2rsync._tcp"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_avahi_browse(completed.stdout)


def parse_avahi_browse(text: str) -> list[Peer]:
    """Parse `avahi-browse -rpt` lines. Resolved services start with `=`."""
    peers: list[Peer] = []
    seen: set[tuple[str, int]] = set()
    for line in text.splitlines():
        # =;iface;proto;name;type;domain;host;address;port;txt
        if not line.startswith("="):
            continue
        parts = line.split(";")
        if len(parts) < 9:
            continue
        name = safe_service_name(parts[3])
        host = parts[7]
        if not host or host == "":
            continue
        try:
            port = int(parts[8])
        except ValueError:
            continue
        if not 1 <= port <= 65535:
            continue
        key = (host, port)
        if key in seen:
            continue
        seen.add(key)
        peers.append(Peer(host, port, name))
    return peers


def _read_linux_routes() -> list[str]:
    """Read IPv4 broadcast addresses from /proc/net/route when present.

    The file is little-endian hex. A default route is skipped; connected
    subnets contribute their broadcast address.
    """
    path = "/proc/net/route"
    try:
        with open(path, encoding="ascii") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    found: list[str] = []
    for line in lines[1:]:
        cols = line.split()
        if len(cols) < 8:
            continue
        destination = _hex_ip(cols[1])
        mask = _hex_ip(cols[7])
        if destination is None or mask is None or mask == "0.0.0.0":
            continue
        broadcast = _broadcast(destination, mask)
        if broadcast and broadcast not in found:
            found.append(broadcast)
    return found


def _hex_ip(value: str) -> str | None:
    try:
        raw = bytes.fromhex(value)
    except ValueError:
        return None
    if len(raw) != 4:
        return None
    # /proc/net/route stores addresses little-endian.
    return ".".join(str(part) for part in raw[::-1])


def _broadcast(ip: str, mask: str) -> str | None:
    try:
        ip_n = _ip_int(ip)
        mask_n = _ip_int(mask)
    except ValueError:
        return None
    network = ip_n & mask_n
    broadcast = network | (~mask_n & 0xFFFFFFFF)
    return _int_ip(broadcast)


def _ip_int(ip: str) -> int:
    parts = [int(part) for part in ip.split(".")]
    if len(parts) != 4 or any(part < 0 or part > 255 for part in parts):
        raise ValueError(ip)
    return (parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8) | parts[3]


def _int_ip(value: int) -> str:
    return ".".join(str((value >> shift) & 0xFF) for shift in (24, 16, 8, 0))


def _nonce_ok(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{8,64}", value))
