"""Config files, state files, and process-list parsing."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from d2rsync.config import ConfigError, init_config, load_config, parse_peer
from d2rsync.game import game_is_running
from d2rsync.state import load_state


class ConfigTests(unittest.TestCase):
    def test_init_writes_a_private_config_and_does_not_replace_it(self):
        directory = Path(tempfile.mkdtemp())
        path = directory / "config.json"
        cfg, created = init_config(path)
        self.assertTrue(created)
        self.assertGreaterEqual(len(cfg.secret or ""), 8)
        mode = stat.S_IMODE(path.stat().st_mode)
        self.assertEqual(mode & 0o077, 0)
        again, created_again = init_config(path)
        self.assertFalse(created_again)
        self.assertEqual(again.secret, cfg.secret)
        loaded = load_config(path)
        self.assertEqual(loaded.conflict, "keep-both")
        self.assertEqual(loaded.port, 41943)

    def test_short_secret_is_rejected(self):
        path = Path(tempfile.mkdtemp()) / "config.json"
        with self.assertRaises(ConfigError):
            init_config(path, secret="short")

    def test_environment_secret_overrides_the_file(self):
        path = Path(tempfile.mkdtemp()) / "config.json"
        cfg, _created = init_config(path, secret="file-secret-value")
        os.environ["D2R_SYNC_SECRET"] = "env-secret-value"
        try:
            self.assertEqual(load_config(path).secret_value(), "env-secret-value")
            self.assertNotEqual(cfg.fingerprint(), "")
        finally:
            del os.environ["D2R_SYNC_SECRET"]

    def test_peer_parsing(self):
        self.assertEqual(parse_peer("192.168.1.20", 41943), ("192.168.1.20", 41943))
        self.assertEqual(parse_peer("deck.local:5000", 41943), ("deck.local", 5000))


class StateTests(unittest.TestCase):
    def test_roundtrip_is_scoped_to_the_save_folder(self):
        directory = Path(tempfile.mkdtemp())
        save = directory / "saves"
        other = directory / "other"
        save.mkdir()
        other.mkdir()
        path = directory / "state.json"
        state = load_state(path, save)
        state.set_hash("Hero.d2s", "a" * 64)
        state.save()
        self.assertEqual(load_state(path, save).hash_for("Hero.d2s"), "a" * 64)
        self.assertIsNone(load_state(path, other).hash_for("Hero.d2s"))
        state.set_hash("Hero.d2s", None)
        state.save()
        self.assertIsNone(load_state(path, save).hash_for("Hero.d2s"))

    def test_corrupt_state_is_not_silently_replaced(self):
        directory = Path(tempfile.mkdtemp())
        save = directory / "saves"
        save.mkdir()
        path = directory / "state.json"
        path.write_text("{", encoding="utf-8")
        with self.assertRaises(Exception):
            load_state(path, save)
        self.assertEqual(path.read_text(encoding="utf-8"), "{")

    def test_non_save_names_in_state_are_ignored(self):
        directory = Path(tempfile.mkdtemp())
        save = directory / "saves"
        save.mkdir()
        path = directory / "state.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "folders": {
                        os.path.normcase(str(save.resolve())): {
                            "files": {"notes.txt": "abc", "Hero.d2s": "def"}
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        state = load_state(path, save)
        self.assertIsNone(state.hash_for("notes.txt"))
        self.assertEqual(state.hash_for("Hero.d2s"), "def")


class GameTests(unittest.TestCase):
    def test_markers_match_windows_and_proton_command_lines(self):
        self.assertTrue(game_is_running(["explorer.exe", "D2R.exe"]))
        self.assertTrue(
            game_is_running(
                ["/home/deck/.local/share/Steam/steamapps/common/Proton/proton /path/D2R.exe"]
            )
        )
        self.assertFalse(game_is_running(["steam", "python3"]))
        self.assertFalse(game_is_running([]))


if __name__ == "__main__":
    unittest.main()
