"""LAN transfer: auth, encryption, conflicts, and refusal while the game is open."""

from __future__ import annotations

import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path

from d2rsync.backup import BackupManager
from d2rsync.client import connect, sync_with_peer
from d2rsync.errors import AuthError, GameRunningError, ProtocolError
from d2rsync.hashing import sha256_bytes
from d2rsync.protocol import Conn
from d2rsync.server import Server
from d2rsync.state import load_state
from d2rsync.store import DirectorySave
from tests.support import write_file

SECRET = "lan-test-secret"


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.local_root = self.tmp / "deck"
        self.remote_root = self.tmp / "pc"
        self.local_root.mkdir()
        self.remote_root.mkdir()
        self.local_state = self.tmp / "deck-state.json"
        self.remote_state = self.tmp / "pc-state.json"
        self.server = None
        self.thread = None

    def tearDown(self):
        if self.server is not None:
            self.server.stop()
        if self.thread is not None:
            self.thread.join(timeout=3)

    def test_pull_push_and_a_second_sync_that_does_nothing(self):
        write_file(self.remote_root, "Hero.d2s", b"from-pc", 20)
        write_file(self.remote_root, "SharedStashSoftCoreV2.d2i", b"stash", 21)
        write_file(self.local_root, "Hero.key", b"deck-keys", 22)
        self._start()
        _resolved, outcomes, _notes = self._sync()
        self.assertEqual((self.local_root / "Hero.d2s").read_bytes(), b"from-pc")
        self.assertEqual((self.remote_root / "Hero.key").read_bytes(), b"deck-keys")
        self.assertAlmostEqual((self.local_root / "Hero.d2s").stat().st_mtime, 20, places=3)
        self.assertTrue(any(item.op == "pull" for item in outcomes))
        self.assertTrue(any(item.op == "push" for item in outcomes))
        remote_hash = load_state(self.remote_state, self.remote_root).hash_for("Hero.d2s")
        local_hash = load_state(self.local_state, self.local_root).hash_for("Hero.d2s")
        self.assertEqual(remote_hash, local_hash)
        _resolved, again, _notes = self._sync()
        self.assertTrue(all(item.op == "unchanged" for item in again))

    def test_dry_run_reads_the_manifest_and_writes_nothing(self):
        write_file(self.remote_root, "Hero.d2s", b"from-pc", 20)
        self._start()
        _resolved, outcomes, _notes = self._sync(dry_run=True)
        self.assertEqual(outcomes[0].op, "pull")
        self.assertFalse((self.local_root / "Hero.d2s").exists())
        self.assertFalse(self.local_state.exists())
        self.assertFalse(self.remote_state.exists())

    def test_conflict_over_the_network_keeps_the_older_copy(self):
        write_file(self.local_root, "Hero.d2s", b"deck-hero", 10)
        write_file(self.remote_root, "Hero.d2s", b"pc-hero", 50)
        self._start()
        self._sync(conflict="keep-both")
        self.assertEqual((self.local_root / "Hero.d2s").read_bytes(), b"pc-hero")
        self.assertEqual((self.remote_root / "Hero.d2s").read_bytes(), b"pc-hero")
        archived = list((self.local_root / ".d2r-sync-backups" / "conflicts").rglob("Hero.d2s"))
        self.assertEqual(archived[0].read_bytes(), b"deck-hero")
        remote_archived = list((self.remote_root / ".d2r-sync-backups" / "conflicts").rglob("Hero.d2s"))
        self.assertEqual(remote_archived[0].read_bytes(), b"deck-hero")

    def test_wrong_secret_is_rejected(self):
        self._start()
        with self.assertRaises(AuthError):
            connect("127.0.0.1", self.server.bound_port, "not-the-secret", "deck", 5)

    def test_game_running_refuses_unless_forced(self):
        write_file(self.remote_root, "Hero.d2s", b"mid-write", 20)
        self._start(game_check=lambda: True)
        with self.assertRaises(GameRunningError):
            self._sync()
        self.assertFalse((self.local_root / "Hero.d2s").exists())
        self._sync(force=True)
        self.assertEqual((self.local_root / "Hero.d2s").read_bytes(), b"mid-write")

    def test_server_rejects_a_path_that_escapes_the_save_folder(self):
        self._start()
        outside = self.remote_root.parent / "Hero.d2s"
        conn = connect("127.0.0.1", self.server.bound_port, SECRET, "deck", 5)
        try:
            conn.send_msg({"t": "ready", "force": False})
            ready = conn.recv_msg()
            self.assertEqual(ready["t"], "ready_ok")
            payload = b"nope"
            conn.send_msg(
                {
                    "t": "put",
                    "rel": "../Hero.d2s",
                    "size": len(payload),
                    "mtime": 1,
                    "sha256": sha256_bytes(payload),
                }
            )
            conn.send_bytes(payload)
            reply = conn.recv_msg()
            self.assertEqual(reply["t"], "error")
        finally:
            conn.sock.close()
        self.assertFalse(outside.exists())
        self.assertFalse((self.remote_root / "Hero.d2s").exists())

    def test_host_notices_follow_a_sync_and_the_listener_stays_up(self):
        notices = []
        write_file(self.remote_root, "Hero.d2s", b"from-pc", 20)
        write_file(self.local_root, "Hero.key", b"deck-keys", 22)
        self._start(on_notice=notices.append)
        self._sync()
        self.assertEqual(notices[0].phase, "connected")
        self.assertEqual(notices[0].peer_host, "127.0.0.1")
        self.assertTrue(any(item.phase == "progress" for item in notices))
        finished = [item for item in notices if item.phase == "finished" and item.show_result]
        self.assertEqual(len(finished), 1)
        self.assertIsNotNone(finished[0].synced_at)
        ops = {item.op for item in finished[0].outcomes}
        self.assertIn("push", ops)
        self.assertIn("pull", ops)
        self.assertIsNotNone(load_state(self.remote_state, self.remote_root).synced_at)
        _resolved, again, _notes = self._sync()
        self.assertTrue(all(item.op == "unchanged" for item in again))
        finished_again = [item for item in notices if item.phase == "finished" and item.show_result]
        self.assertEqual(len(finished_again), 2)
        self.assertTrue(self.thread.is_alive())

    def test_preview_connects_without_counting_as_a_sync(self):
        from d2rsync.preview import preview_with_peer

        notices = []
        write_file(self.remote_root, "Hero.d2s", b"from-pc", 20)
        self._start(on_notice=notices.append)
        preview = preview_with_peer(
            local_root=self.local_root,
            state_file=self.local_state,
            secret=SECRET,
            host="127.0.0.1",
            port=self.server.bound_port,
            name="deck",
        )
        self.assertIn("Hero.d2s", preview.remote_files)
        self.assertTrue(any(item.phase == "connected" for item in notices))
        self.assertFalse(any(item.show_result for item in notices))
        self.assertIsNone(load_state(self.remote_state, self.remote_root).synced_at)

    def test_online_character_files_are_not_synced_or_deleted(self):
        write_file(self.local_root, "Bloodoath.keyo", b"deck-keys", 10)
        write_file(self.local_root, "Bloodoath.ctlo", b"deck-pad", 11)
        write_file(self.local_root, "Bloodoath.map", b"deck-map", 12)
        write_file(self.remote_root, "Bloodoath.keyo", b"pc-keys", 30)
        write_file(self.remote_root, "Bloodoath.ctlo", b"pc-pad", 31)
        self._start()
        self._sync()
        self.assertEqual((self.local_root / "Bloodoath.keyo").read_bytes(), b"deck-keys")
        self.assertEqual((self.remote_root / "Bloodoath.keyo").read_bytes(), b"pc-keys")
        self.assertEqual((self.local_root / "Bloodoath.ctlo").read_bytes(), b"deck-pad")
        self.assertEqual((self.remote_root / "Bloodoath.ctlo").read_bytes(), b"pc-pad")
        self.assertEqual((self.local_root / "Bloodoath.map").read_bytes(), b"deck-map")
        self.assertFalse((self.remote_root / "Bloodoath.map").exists())
        self.assertFalse(list((self.local_root / ".d2r-sync-backups").rglob("Bloodoath.*")))
        self.assertFalse(list((self.remote_root / ".d2r-sync-backups").rglob("Bloodoath.*")))

    def test_offline_character_syncs_without_its_online_sidecar(self):
        write_file(self.local_root, "Hero.d2s", b"hero", 10)
        write_file(self.local_root, "Hero.keyo", b"online-keys", 11)
        self._start()
        self._sync()
        self.assertEqual((self.remote_root / "Hero.d2s").read_bytes(), b"hero")
        self.assertFalse((self.remote_root / "Hero.keyo").exists())
        self.assertEqual((self.local_root / "Hero.keyo").read_bytes(), b"online-keys")

    def test_a_name_excluded_on_only_one_side_is_not_copied_or_deleted(self):
        write_file(self.local_root, "Hero.d2s", b"deck", 10)
        write_file(self.remote_root, "Mage.d2s", b"pc", 20)
        write_file(self.local_root, "Both.d2s", b"same", 15)
        write_file(self.remote_root, "Both.d2s", b"changed", 40)
        self._start(exclude_characters=["Mage"])
        self._sync(exclude_characters=["Hero", "Both"])
        self.assertEqual((self.local_root / "Hero.d2s").read_bytes(), b"deck")
        self.assertFalse((self.remote_root / "Hero.d2s").exists())
        self.assertEqual((self.remote_root / "Mage.d2s").read_bytes(), b"pc")
        self.assertFalse((self.local_root / "Mage.d2s").exists())
        self.assertEqual((self.local_root / "Both.d2s").read_bytes(), b"same")
        self.assertEqual((self.remote_root / "Both.d2s").read_bytes(), b"changed")

    def test_server_rejects_a_file_for_an_online_character(self):
        write_file(self.remote_root, "Bloodoath.keyo", b"keys", 10)
        self._start()
        conn = connect("127.0.0.1", self.server.bound_port, SECRET, "deck", 5)
        try:
            conn.send_msg({"t": "ready", "force": True, "mode": "sync"})
            ready = conn.recv_msg()
            self.assertEqual(ready["t"], "ready_ok")
            self.assertIn("Bloodoath", ready["exclude"])
            payload = b"map-bytes"
            conn.send_msg(
                {
                    "t": "put",
                    "rel": "Bloodoath.map",
                    "size": len(payload),
                    "mtime": 1,
                    "sha256": sha256_bytes(payload),
                }
            )
            conn.send_bytes(payload)
            reply = conn.recv_msg()
            self.assertEqual(reply["t"], "error")
        finally:
            conn.sock.close()
        self.assertFalse((self.remote_root / "Bloodoath.map").exists())
        self.assertEqual((self.remote_root / "Bloodoath.keyo").read_bytes(), b"keys")

    def test_local_game_running_refuses_before_connect(self):
        self._start()
        with self.assertRaises(GameRunningError):
            sync_with_peer(
                local_root=self.local_root,
                state_file=self.local_state,
                secret=SECRET,
                host="127.0.0.1",
                port=self.server.bound_port,
                conflict="keep-both",
                dry_run=False,
                force=False,
                refuse_if_game_running=True,
                retention_count=20,
                retention_days=30,
                name="deck",
                game_check=lambda: True,
            )

    def _start(self, **server_kwargs):
        save = DirectorySave(self.remote_root, BackupManager(self.remote_root))
        state = load_state(self.remote_state, self.remote_root)
        kwargs = dict(
            host="127.0.0.1",
            port=0,
            name="pc",
            discover=False,
            once=False,
            fail_delay=0,
            game_check=lambda: False,
        )
        kwargs.update(server_kwargs)
        self.server = Server(save, state, SECRET, **kwargs)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.server.wait_until_listening()

    def _sync(self, **kwargs):
        options = dict(
            local_root=self.local_root,
            state_file=self.local_state,
            secret=SECRET,
            host="127.0.0.1",
            port=self.server.bound_port,
            conflict="newest",
            dry_run=False,
            force=False,
            refuse_if_game_running=True,
            retention_count=20,
            retention_days=30,
            name="deck",
            game_check=lambda: False,
        )
        options.update(kwargs)
        return sync_with_peer(**options)


class FrameTests(unittest.TestCase):
    def test_encrypted_frames_do_not_contain_the_filename(self):
        from d2rsync.crypto import session_keys

        left, right = socket.socketpair()
        enc, mac = session_keys(SECRET, b"c" * 16, b"s" * 16)
        try:
            Conn(left, enc, mac).send_msg({"t": "get", "rel": "Hero.d2s"})
            raw = right.recv(65535)
        finally:
            left.close()
            right.close()
        self.assertNotIn(b"Hero.d2s", raw)
        self.assertNotIn(b"get", raw)

    def test_huge_frame_is_rejected(self):
        left, right = socket.socketpair()
        try:
            left.sendall(struct.pack("!I", 0xFFFFFFFF))
            with self.assertRaises(ProtocolError):
                Conn(right).recv_msg()
        finally:
            left.close()
            right.close()


if __name__ == "__main__":
    unittest.main()
