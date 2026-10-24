"""UDP discovery parsing and a directed probe against a local listener."""

from __future__ import annotations

import socket
import unittest

from d2rsync.discover import (
    DiscoveryServer,
    Peer,
    _broadcast,
    _hex_ip,
    _matching_peer,
    discover,
    encode_announce,
    encode_discover,
    parse_avahi_browse,
    parse_datagram,
    probe,
)


class DiscoverParseTests(unittest.TestCase):
    def test_probe_and_announce_roundtrip(self):
        nonce = "abcdef0123456789"
        parsed = parse_datagram(encode_discover(nonce))
        self.assertEqual(parsed["kind"], "discover")
        self.assertEqual(parsed["nonce"], nonce)
        announce = parse_datagram(encode_announce(41943, nonce, "steam deck"))
        self.assertEqual(announce["kind"], "announce")
        self.assertEqual(announce["port"], 41943)
        self.assertEqual(announce["name"], "steam-deck")
        self.assertIsNone(parse_datagram(b"hello"))
        self.assertIsNone(parse_datagram(encode_announce(0, nonce, "pc")))

    def test_route_helpers(self):
        self.assertEqual(_hex_ip("0101A8C0"), "192.168.1.1")
        self.assertEqual(_hex_ip("00FFFFFF"), "255.255.255.0")
        self.assertEqual(_broadcast("192.168.1.0", "255.255.255.0"), "192.168.1.255")

    def test_avahi_browse_output(self):
        text = "\n".join(
            [
                "+;eth0;IPv4;steamdeck;_d2rsync._tcp;local",
                "=;eth0;IPv4;steamdeck;_d2rsync._tcp;local;steamdeck.local;192.168.1.50;41943;",
                "=;eth0;IPv4;steamdeck;_d2rsync._tcp;local;steamdeck.local;192.168.1.50;41943;",
            ]
        )
        peers = parse_avahi_browse(text)
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0].host, "192.168.1.50")
        self.assertEqual(peers[0].port, 41943)
        self.assertEqual(peers[0].name, "steamdeck")

    def test_directed_match_uses_the_address_that_was_asked(self):
        # 10.1.0.54 sorts before 127.0.0.1. A loopback probe must not take it.
        peers = [
            Peer("10.1.0.54", 41943, "pc"),
            Peer("127.0.0.1", 41943, "pc"),
            Peer("192.168.1.20", 41943, "deck"),
        ]
        self.assertEqual(_matching_peer(peers, "127.0.0.1").host, "127.0.0.1")
        self.assertEqual(_matching_peer(peers, "localhost").host, "127.0.0.1")
        self.assertEqual(_matching_peer(peers, "10.1.0.54").host, "10.1.0.54")
        deck = _matching_peer(peers, "192.168.1.20")
        self.assertEqual(deck.host, "192.168.1.20")
        self.assertEqual(deck.name, "deck")
        self.assertIsNone(_matching_peer(peers, "192.168.1.50"))


def _free_discover_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _primary_ipv4() -> str | None:
    """Address this host would use on a LAN. None when only loopback exists."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        ip = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()
    if ip.startswith("127."):
        return None
    return ip


class DiscoverLiveTests(unittest.TestCase):
    def test_directed_probe_finds_the_server(self):
        port = _free_discover_port()
        server = DiscoveryServer(tcp_port=41943, name="pc", discover_port=port)
        server.start()
        try:
            found = probe("127.0.0.1", port, timeout=1.0, nonce="abc12345")
        finally:
            server.stop()
        self.assertIsNotNone(found)
        self.assertEqual(found.port, 41943)
        self.assertEqual(found.name, "pc")
        self.assertEqual(found.host, "127.0.0.1")

    def test_directed_probe_finds_the_server_on_its_lan_address(self):
        lan = _primary_ipv4()
        if lan is None:
            self.skipTest("no non-loopback IPv4 address")
        port = _free_discover_port()
        server = DiscoveryServer(tcp_port=41943, name="pc", discover_port=port)
        server.start()
        try:
            found = probe(lan, port, timeout=1.0, nonce="abc12345")
        finally:
            server.stop()
        self.assertIsNotNone(found)
        self.assertEqual(found.port, 41943)
        self.assertEqual(found.name, "pc")
        self.assertEqual(found.host, lan)

    def test_broadcast_discover_keeps_the_lan_address(self):
        lan = _primary_ipv4()
        if lan is None:
            self.skipTest("no non-loopback IPv4 address")
        port = _free_discover_port()
        server = DiscoveryServer(tcp_port=41943, name="deck", discover_port=port)
        server.start()
        try:
            peers = discover(port, timeout=1.0, nonce="abc12345")
        finally:
            server.stop()
        hosts = {peer.host for peer in peers}
        self.assertIn(lan, hosts)
        match = next(peer for peer in peers if peer.host == lan)
        self.assertEqual(match.port, 41943)
        self.assertEqual(match.name, "deck")


if __name__ == "__main__":
    unittest.main()
