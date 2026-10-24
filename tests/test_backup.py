"""Backups are taken before a live save is replaced, and old ones are pruned."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from d2rsync.backup import BackupManager
from tests.support import write_file


class BackupTests(unittest.TestCase):
    def test_one_sync_shares_one_snapshot_folder(self):
        root = Path(self._tmp())
        save = root / "saves"
        save.mkdir()
        first = write_file(save, "Hero.d2s", b"one", 10)
        second = write_file(save, "Hero.key", b"two", 11)
        fixed = datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc)
        manager = BackupManager(save, clock=lambda: fixed)
        manager.backup(first, "Hero.d2s")
        manager.backup(second, "Hero.key")
        snapshots = list((save / ".d2r-sync-backups" / "snapshots").iterdir())
        self.assertEqual(len(snapshots), 1)
        self.assertEqual((snapshots[0] / "Hero.d2s").read_bytes(), b"one")
        self.assertEqual((snapshots[0] / "Hero.key").read_bytes(), b"two")

    def test_conflict_copy_is_outside_the_live_folder(self):
        root = Path(self._tmp())
        save = root / "saves"
        save.mkdir()
        manager = BackupManager(save)
        dest = manager.archive_conflict("Hero.d2s", b"older-hero", "from deck")
        self.assertEqual(dest.read_bytes(), b"older-hero")
        self.assertIn(".d2r-sync-backups", dest.parts)
        self.assertIn("conflicts", dest.parts)
        self.assertFalse((save / "Hero.d2s").exists())

    def test_retention_count_keeps_the_newest_snapshots(self):
        root = Path(self._tmp())
        save = root / "saves"
        snapshots = save / ".d2r-sync-backups" / "snapshots"
        snapshots.mkdir(parents=True)
        now = datetime(2026, 10, 24, tzinfo=timezone.utc)
        for index in range(4):
            folder = snapshots / f"snap-{index}"
            folder.mkdir()
            stamped = (now - timedelta(days=index + 1)).timestamp()
            os.utime(folder, (stamped, stamped))
        removed = BackupManager(
            save,
            retention_count=2,
            retention_days=30,
            clock=lambda: now,
        ).prune()
        remaining = sorted(path.name for path in snapshots.iterdir())
        self.assertEqual(remaining, ["snap-0", "snap-1"])
        self.assertEqual(len(removed), 2)

    def test_retention_days_drops_expired_conflicts(self):
        root = Path(self._tmp())
        save = root / "saves"
        conflicts = save / ".d2r-sync-backups" / "conflicts"
        conflicts.mkdir(parents=True)
        now = datetime(2026, 10, 24, tzinfo=timezone.utc)
        old = conflicts / "old"
        fresh = conflicts / "fresh"
        old.mkdir()
        fresh.mkdir()
        os.utime(old, ((now - timedelta(days=40)).timestamp(),) * 2)
        os.utime(fresh, ((now - timedelta(days=1)).timestamp(),) * 2)
        BackupManager(save, retention_count=20, retention_days=30, clock=lambda: now).prune()
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_unreadable_snapshot_does_not_stop_pruning(self):
        root = Path(self._tmp())
        save = root / "saves"
        snapshots = save / ".d2r-sync-backups" / "snapshots"
        snapshots.mkdir(parents=True)
        now = datetime(2026, 10, 24, tzinfo=timezone.utc)
        old = snapshots / "old"
        locked = snapshots / "locked"
        fresh = snapshots / "fresh"
        old.mkdir()
        locked.mkdir()
        fresh.mkdir()
        os.utime(old, ((now - timedelta(days=40)).timestamp(),) * 2)
        os.utime(locked, ((now - timedelta(days=40)).timestamp(),) * 2)
        os.utime(fresh, ((now - timedelta(days=1)).timestamp(),) * 2)
        locked.chmod(0)
        try:
            removed = BackupManager(
                save, retention_count=20, retention_days=30, clock=lambda: now
            ).prune()
        finally:
            locked.chmod(0o700)
        self.assertFalse(old.exists())
        self.assertTrue(locked.exists())
        self.assertTrue(fresh.exists())
        self.assertEqual([path.name for path in removed], ["old"])

    def _tmp(self) -> str:
        import tempfile

        return tempfile.mkdtemp(prefix="d2r-backup-")


if __name__ == "__main__":
    unittest.main()
