"""CLI commands against a temporary save folder and a local peer."""

from __future__ import annotations

import io
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from d2rsync.backup import BackupManager
from d2rsync.cli import main
from d2rsync.server import Server
from d2rsync.state import load_state
from d2rsync.store import DirectorySave
from tests.support import write_file

SECRET = "cli-test-secret"


class CliTests(unittest.TestCase):
    def test_init_path_status_and_sync(self):
        tmp = Path(tempfile.mkdtemp())
        pc = tmp / "pc"
        deck = tmp / "deck"
        pc.mkdir()
        deck.mkdir()
        write_file(pc, "Hero.d2s", b"from-pc", 30)
        write_file(pc, "Hero.d2s.bak", b"editor-backup", 30)
        write_file(deck, "notes.txt", b"leave-me", 1)
        config = tmp / "deck-config.json"
        state = tmp / "pc-state.json"
        server = Server(
            DirectorySave(pc, BackupManager(pc)),
            load_state(state, pc),
            SECRET,
            host="127.0.0.1",
            port=0,
            name="pc",
            discover=False,
            fail_delay=0,
            game_check=lambda: False,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        server.wait_until_listening()
        try:
            self.assertEqual(self._run(["init", "--config", str(config), "--secret", SECRET]), 0)
            self.assertEqual(self._run(["path", "--save-path", str(deck)]), 0)
            dry = self._run(
                [
                    "sync",
                    "--config",
                    str(config),
                    "--save-path",
                    str(deck),
                    "--peer",
                    "127.0.0.1",
                    "--port",
                    str(server.bound_port),
                    "--dry-run",
                ]
            )
            self.assertEqual(dry, 0)
            self.assertFalse((deck / "Hero.d2s").exists())
            synced = self._run(
                [
                    "sync",
                    "--config",
                    str(config),
                    "--save-path",
                    str(deck),
                    "--peer",
                    f"127.0.0.1:{server.bound_port}",
                    "--conflict",
                    "newest",
                ]
            )
            self.assertEqual(synced, 0)
            self.assertEqual((deck / "Hero.d2s").read_bytes(), b"from-pc")
            self.assertFalse((deck / "Hero.d2s.bak").exists())
            self.assertEqual((deck / "notes.txt").read_bytes(), b"leave-me")
            status = self._run(["status", "--config", str(config), "--save-path", str(deck)])
            self.assertEqual(status, 0)
        finally:
            server.stop()
            thread.join(timeout=3)

    def test_sync_exits_3_when_the_peer_is_in_game(self):
        tmp = Path(tempfile.mkdtemp())
        pc = tmp / "pc"
        deck = tmp / "deck"
        pc.mkdir()
        deck.mkdir()
        write_file(pc, "Hero.d2s", b"x", 1)
        config = tmp / "config.json"
        server = Server(
            DirectorySave(pc, BackupManager(pc)),
            load_state(tmp / "state.json", pc),
            SECRET,
            host="127.0.0.1",
            port=0,
            discover=False,
            fail_delay=0,
            game_check=lambda: True,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        server.wait_until_listening()
        try:
            self._run(["init", "--config", str(config), "--secret", SECRET])
            code = self._run(
                [
                    "sync",
                    "--config",
                    str(config),
                    "--save-path",
                    str(deck),
                    "--peer",
                    "127.0.0.1",
                    "--port",
                    str(server.bound_port),
                ]
            )
            self.assertEqual(code, 3)
            self.assertFalse((deck / "Hero.d2s").exists())
        finally:
            server.stop()
            thread.join(timeout=3)

    def _run(self, argv: list[str]) -> int:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            return main(argv)


if __name__ == "__main__":
    unittest.main()
