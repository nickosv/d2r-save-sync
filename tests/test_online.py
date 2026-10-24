"""Online characters and the exclude list stay out of a sync."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from d2rsync.config import Config, load_config, write_config
from d2rsync.d2s import d2s_is_online
from d2rsync.names import (
    characters_in_folder,
    is_syncable_name,
    omit_blocked,
    online_character_names,
)
from d2rsync.state import SyncState, load_state, save_key
from tests.support import make_d2s, write_file


class OnlineDetectionTests(unittest.TestCase):
    def test_status_byte_is_not_treated_as_an_online_flag(self):
        data = bytearray(make_d2s("Hero", 3, 80))
        data[36] = 0xFF
        self.assertFalse(d2s_is_online(bytes(data)))
        self.assertFalse(d2s_is_online(b""))
        self.assertFalse(d2s_is_online(make_d2s("Bloodoath", 4, 90)))

    def test_keyo_without_a_d2s_is_an_online_character(self):
        names = ["Bloodoath.keyo", "Bloodoath.ctlo", "Bloodoath.map", "Hero.d2s", "Hero.keyo"]
        online = online_character_names(names, {"Hero.d2s": make_d2s("Hero", 0, 10)})
        self.assertEqual(online, ["Bloodoath"])
        files = {name: name for name in names}
        kept, _state = omit_blocked(files, {"Bloodoath.map": "abc", "Hero.d2s": "def"}, online)
        self.assertEqual(set(kept), {"Hero.d2s"})
        self.assertFalse(is_syncable_name("Bloodoath.keyo"))
        self.assertFalse(is_syncable_name("Hero.ctlo"))
        self.assertTrue(is_syncable_name("Hero.d2s"))

    def test_folder_lists_online_and_offline_characters(self):
        root = Path(tempfile.mkdtemp())
        write_file(root, "Bloodoath.keyo", b"keys")
        write_file(root, "Bloodoath.ctlo", b"pad")
        write_file(root, "Hero.d2s", make_d2s("Hero", 3, 80))
        write_file(root, "SharedStashSoftCoreV2.d2i", b"stash")
        choices = {item.name: item for item in characters_in_folder(root)}
        self.assertEqual(set(choices), {"Bloodoath", "Hero"})
        self.assertTrue(choices["Bloodoath"].online)
        self.assertEqual(choices["Bloodoath"].subtitle, "Online")
        self.assertFalse(choices["Hero"].online)
        self.assertIn("Paladin", choices["Hero"].subtitle)
        self.assertIn("80", choices["Hero"].subtitle)

    def test_saved_online_sidecar_hash_is_forgotten(self):
        root = Path(tempfile.mkdtemp())
        state_path = root / "state.json"
        state = SyncState(state_path, save_key(root))
        state.set_hash("Bloodoath.keyo", "a" * 64)
        state.set_hash("Hero.d2s", "b" * 64)
        state.save()
        loaded = load_state(state_path, root)
        self.assertIsNone(loaded.hash_for("Bloodoath.keyo"))
        self.assertEqual(loaded.hash_for("Hero.d2s"), "b" * 64)

    def test_exclude_list_roundtrips_through_the_config_file(self):
        directory = Path(tempfile.mkdtemp())
        path = directory / "config.json"
        cfg = Config(secret="12345678", exclude_characters=["Bloodoath", "Hero"])
        write_config(cfg, path)
        loaded = load_config(path)
        self.assertEqual(loaded.exclude_characters, ["Bloodoath", "Hero"])
        cfg.exclude_characters = []
        write_config(cfg, path)
        self.assertEqual(load_config(path).exclude_characters, [])
