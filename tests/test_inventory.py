"""Which names are saves, and how a folder is inventoried."""

from __future__ import annotations

import unittest
from pathlib import Path

from d2rsync.backup import BackupManager
from d2rsync.errors import SyncIOError
from d2rsync.names import is_syncable_name, validate_rel
from d2rsync.store import DirectorySave
from tests.support import write_file


class NameTests(unittest.TestCase):
    def test_character_sidecars_and_shared_stash_are_included(self):
        included = [
            "Hero.d2s",
            "Hero.key",
            "Hero.keyo",
            "Hero.ctl",
            "Hero.ctlo",
            "Hero.ma0",
            "Hero.ma3",
            "Hero.map",
            "SharedStashSoftCoreV2.d2i",
            "SharedStashHardCoreV2.d2i",
            "My Char.d2s",
        ]
        for name in included:
            self.assertTrue(is_syncable_name(name), name)
            self.assertEqual(validate_rel(name), name)

    def test_backups_temp_files_and_unrelated_names_are_excluded(self):
        excluded = [
            "Hero.bak",
            "Hero.d2s.bak",
            "Hero.d2s.d2rsync-tmp",
            "notes.txt",
            "desktop.ini",
            ".d2s",
            "Hero.d2s/evil",
            "../Hero.d2s",
            "sharedstash.txt",
        ]
        for name in excluded:
            self.assertFalse(is_syncable_name(name), name)
            with self.assertRaises(ValueError):
                validate_rel(name)


class InventoryTests(unittest.TestCase):
    def test_inventory_hashes_only_live_saves(self):
        import tempfile

        save = Path(tempfile.mkdtemp()) / "saves"
        save.mkdir()
        write_file(save, "Hero.d2s", b"hero", 100)
        write_file(save, "Hero.ctlo", b"pad", 101)
        write_file(save, "notes.txt", b"ignore", 102)
        write_file(save, "Hero.bak", b"old", 103)
        nested = save / ".d2r-sync-backups" / "snapshots" / "old"
        write_file(nested, "Hero.d2s", b"buried", 104)
        outside = save.parent / "outside.d2s"
        outside.write_bytes(b"secret")
        (save / "Linked.d2s").symlink_to(outside)
        found = DirectorySave(save, BackupManager(save)).inventory()
        self.assertEqual(set(found), {"Hero.d2s", "Hero.ctlo"})
        self.assertEqual(found["Hero.d2s"].size, 4)

    def test_write_refuses_a_symlink_and_does_not_touch_the_target(self):
        import tempfile

        base = Path(tempfile.mkdtemp())
        save = base / "saves"
        save.mkdir()
        outside = base / "outside.d2s"
        outside.write_bytes(b"secret")
        link = save / "Hero.d2s"
        link.symlink_to(outside)
        store = DirectorySave(save, BackupManager(save))
        with self.assertRaises(SyncIOError):
            store.write_bytes("Hero.d2s", b"overwrite", 50)
        self.assertEqual(outside.read_bytes(), b"secret")

    def test_write_backs_up_then_replaces_and_keeps_mtime(self):
        import tempfile

        save = Path(tempfile.mkdtemp()) / "saves"
        save.mkdir()
        write_file(save, "Hero.d2s", b"old", 10)
        store = DirectorySave(save, BackupManager(save))
        store.write_bytes("Hero.d2s", b"new", 80)
        self.assertEqual((save / "Hero.d2s").read_bytes(), b"new")
        self.assertAlmostEqual((save / "Hero.d2s").stat().st_mtime, 80, places=3)
        snapshots = list((save / ".d2r-sync-backups" / "snapshots").rglob("Hero.d2s"))
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].read_bytes(), b"old")
        self.assertFalse(list(save.glob("*.d2rsync-tmp")))

    def test_delete_backs_up_first(self):
        import tempfile

        save = Path(tempfile.mkdtemp()) / "saves"
        save.mkdir()
        write_file(save, "Hero.key", b"binds", 10)
        store = DirectorySave(save, BackupManager(save))
        store.remove("Hero.key")
        self.assertFalse((save / "Hero.key").exists())
        backed = list((save / ".d2r-sync-backups" / "snapshots").rglob("Hero.key"))
        self.assertEqual(backed[0].read_bytes(), b"binds")


if __name__ == "__main__":
    unittest.main()
