"""Apply a plan between two folders, including backups and conflict copies."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from d2rsync.backup import BackupManager
from d2rsync.engine import execute
from d2rsync.plan import plan, resolve
from d2rsync.state import load_state
from d2rsync.store import DirectorySave
from tests.support import write_file


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.local_root = self.tmp / "local"
        self.remote_root = self.tmp / "remote"
        self.local_root.mkdir()
        self.remote_root.mkdir()
        self.local_state_path = self.tmp / "local-state.json"
        self.remote_state_path = self.tmp / "remote-state.json"

    def test_one_sided_edit_copies_and_records_state(self):
        write_file(self.local_root, "Hero.d2s", b"base", 10)
        write_file(self.remote_root, "Hero.d2s", b"base", 10)
        self._sync("keep-both")
        write_file(self.local_root, "Hero.d2s", b"leveled", 11)
        outcomes = self._sync("keep-both")
        self.assertEqual((self.remote_root / "Hero.d2s").read_bytes(), b"leveled")
        self.assertIn("push", [item.op for item in outcomes])
        self.assertEqual(
            load_state(self.local_state_path, self.local_root).hash_for("Hero.d2s"),
            load_state(self.remote_state_path, self.remote_root).hash_for("Hero.d2s"),
        )

    def test_second_sync_is_a_no_op(self):
        write_file(self.local_root, "Hero.d2s", b"hero", 10)
        write_file(self.local_root, "SharedStashSoftCoreV2.d2i", b"stash", 12)
        self._sync("keep-both")
        before = list((self.remote_root / ".d2r-sync-backups").rglob("*")) if (
            self.remote_root / ".d2r-sync-backups"
        ).exists() else []
        outcomes = self._sync("keep-both")
        self.assertTrue(all(item.op == "unchanged" for item in outcomes))
        after = list((self.remote_root / ".d2r-sync-backups").rglob("*")) if (
            self.remote_root / ".d2r-sync-backups"
        ).exists() else []
        self.assertEqual(before, after)

    def test_conflict_keep_both_archives_the_older_bytes_on_both_sides(self):
        write_file(self.local_root, "Hero.d2s", b"pc-hero", 10)
        write_file(self.remote_root, "Hero.d2s", b"deck-hero", 40)
        outcomes = self._sync("keep-both")
        self.assertEqual((self.local_root / "Hero.d2s").read_bytes(), b"deck-hero")
        self.assertEqual((self.remote_root / "Hero.d2s").read_bytes(), b"deck-hero")
        local_conflicts = list((self.local_root / ".d2r-sync-backups" / "conflicts").rglob("Hero.d2s"))
        remote_conflicts = list((self.remote_root / ".d2r-sync-backups" / "conflicts").rglob("Hero.d2s"))
        self.assertEqual(local_conflicts[0].read_bytes(), b"pc-hero")
        self.assertEqual(remote_conflicts[0].read_bytes(), b"pc-hero")
        snapshots = list((self.local_root / ".d2r-sync-backups" / "snapshots").rglob("Hero.d2s"))
        self.assertEqual(snapshots[0].read_bytes(), b"pc-hero")
        self.assertTrue(any(item.conflict for item in outcomes))
        # The live folder must not gain a second character file.
        self.assertEqual(list(self.local_root.glob("*.d2s")), [self.local_root / "Hero.d2s"])

    def test_newest_overwrites_with_a_backup_and_no_conflict_folder(self):
        write_file(self.local_root, "Hero.d2s", b"pc-hero", 10)
        write_file(self.remote_root, "Hero.d2s", b"deck-hero", 40)
        self._sync("newest")
        self.assertEqual((self.local_root / "Hero.d2s").read_bytes(), b"deck-hero")
        self.assertFalse((self.local_root / ".d2r-sync-backups" / "conflicts").exists())
        backed = list((self.local_root / ".d2r-sync-backups" / "snapshots").rglob("Hero.d2s"))
        self.assertEqual(backed[0].read_bytes(), b"pc-hero")

    def test_skip_leaves_both_copies_and_state_untouched(self):
        write_file(self.local_root, "Hero.d2s", b"pc-hero", 10)
        write_file(self.remote_root, "Hero.d2s", b"deck-hero", 40)
        outcomes = self._sync("ask", choices={"Hero.d2s": "skip"})
        self.assertEqual((self.local_root / "Hero.d2s").read_bytes(), b"pc-hero")
        self.assertEqual((self.remote_root / "Hero.d2s").read_bytes(), b"deck-hero")
        self.assertEqual(outcomes[0].op, "skipped")
        self.assertIsNone(load_state(self.local_state_path, self.local_root).hash_for("Hero.d2s"))

    def test_deletion_propagates_and_keeps_a_backup(self):
        write_file(self.local_root, "Hero.d2s", b"hero", 10)
        write_file(self.remote_root, "Hero.d2s", b"hero", 10)
        self._sync("keep-both")
        (self.local_root / "Hero.d2s").unlink()
        self._sync("keep-both")
        self.assertFalse((self.remote_root / "Hero.d2s").exists())
        backed = list((self.remote_root / ".d2r-sync-backups" / "snapshots").rglob("Hero.d2s"))
        self.assertEqual(backed[0].read_bytes(), b"hero")
        self.assertIsNone(load_state(self.local_state_path, self.local_root).hash_for("Hero.d2s"))

    def test_dry_run_does_not_write(self):
        write_file(self.local_root, "Hero.d2s", b"only-local", 10)
        outcomes = self._sync("keep-both", dry_run=True)
        self.assertEqual(outcomes[0].op, "push")
        self.assertFalse((self.remote_root / "Hero.d2s").exists())
        self.assertFalse(self.local_state_path.exists())

    def test_unrelated_files_are_left_alone(self):
        write_file(self.local_root, "Hero.d2s", b"hero", 10)
        write_file(self.local_root, "notes.txt", b"keep", 10)
        write_file(self.remote_root, "readme.txt", b"remote-note", 10)
        self._sync("keep-both")
        self.assertEqual((self.local_root / "notes.txt").read_bytes(), b"keep")
        self.assertEqual((self.remote_root / "readme.txt").read_bytes(), b"remote-note")
        self.assertFalse((self.remote_root / "notes.txt").exists())

    def test_sidecar_files_sync_with_the_character(self):
        write_file(self.local_root, "Hero.d2s", b"hero", 10)
        write_file(self.local_root, "Hero.key", b"keys", 10)
        write_file(self.local_root, "Hero.ma0", b"map", 10)
        write_file(self.local_root, "Hero.ctl", b"pad", 10)
        self._sync("newest")
        for name in ("Hero.d2s", "Hero.key", "Hero.ma0", "Hero.ctl"):
            self.assertEqual((self.remote_root / name).read_bytes(), (self.local_root / name).read_bytes())

    def _sync(self, policy: str, *, dry_run: bool = False, choices: dict | None = None):
        local_state = load_state(self.local_state_path, self.local_root)
        remote_state = load_state(self.remote_state_path, self.remote_root)
        local = DirectorySave(self.local_root, BackupManager(self.local_root))
        remote = DirectorySave(self.remote_root, BackupManager(self.remote_root))
        decisions = plan(
            local.inventory(),
            remote.inventory(),
            local_state.as_dict(),
            remote_state.as_dict(),
        )
        resolved = []
        for decision in decisions:
            choice = None if not choices else choices.get(decision.rel)
            resolved.append(resolve(decision, policy, choice))

        def on_synced(rel: str, digest: str | None) -> None:
            local_state.set_hash(rel, digest)
            remote_state.set_hash(rel, digest)
            local_state.save()
            remote_state.save()

        return execute(
            local,
            remote,
            resolved,
            on_synced=on_synced,
            dry_run=dry_run,
            loser_label_for_local="from-pc",
            loser_label_for_remote="from-deck",
        )


if __name__ == "__main__":
    unittest.main()
