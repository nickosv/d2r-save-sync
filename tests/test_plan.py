"""Sync decisions: hashes and saved state, not just which file looks newer."""

from __future__ import annotations

import unittest

from d2rsync.plan import plan_file, preferred_side, resolve
from tests.support import entry


class PlanTests(unittest.TestCase):
    def test_identical_content_copies_the_newer_modification_time(self):
        newer_remote = plan_file("Hero.d2s", entry(b"same", 10), entry(b"same", 99), None, None)
        self.assertEqual(newer_remote.action, "pull")
        self.assertEqual(newer_remote.reason_code, "mtime")
        newer_local = plan_file("Hero.d2s", entry(b"same", 99), entry(b"same", 10), None, None)
        self.assertEqual(newer_local.action, "push")
        self.assertEqual(newer_local.reason_code, "mtime")

    def test_identical_content_within_the_tie_window_stays_put(self):
        decision = plan_file("Hero.d2s", entry(b"same", 10), entry(b"same", 11), None, None)
        self.assertEqual(decision.action, "none")
        self.assertEqual(decision.reason_code, "match")

    def test_absent_on_both_sides_clears_state(self):
        decision = plan_file("Hero.d2s", None, None, "abc", "abc")
        self.assertEqual(decision.action, "none")
        self.assertEqual(decision.reason_code, "absent")

    def test_first_sync_copies_files_that_exist_on_only_one_side(self):
        local = entry(b"pc", 10)
        remote = entry(b"deck", 20)
        pushed = plan_file("Hero.d2s", local, None, None, None)
        pulled = plan_file("Alt.d2s", None, remote, None, None)
        self.assertEqual(pushed.action, "push")
        self.assertEqual(pushed.reason_code, "push_new")
        self.assertEqual(pulled.action, "pull")
        self.assertEqual(pulled.reason_code, "pull_new")

    def test_first_sync_of_different_copies_is_a_conflict(self):
        decision = plan_file(
            "Hero.d2s",
            entry(b"pc", 10),
            entry(b"deck", 20),
            None,
            None,
        )
        self.assertEqual(decision.action, "conflict")
        self.assertEqual(decision.reason_code, "conflict_untracked")

    def test_only_local_edit_pushes_even_when_its_mtime_is_older(self):
        base = entry(b"base", 50).sha256
        local = entry(b"edited-on-pc", 10)
        remote = entry(b"base", 80)
        decision = plan_file("Hero.d2s", local, remote, base, base)
        self.assertEqual(decision.action, "push")
        self.assertEqual(decision.reason_code, "push_changed")

    def test_only_remote_edit_pulls(self):
        base = entry(b"base", 50).sha256
        decision = plan_file(
            "SharedStashSoftCoreV2.d2i",
            entry(b"base", 80),
            entry(b"deck-stash", 90),
            base,
            base,
        )
        self.assertEqual(decision.action, "pull")
        self.assertEqual(decision.reason_code, "pull_changed")

    def test_both_sides_edited_since_baseline_conflict(self):
        base = entry(b"base", 50).sha256
        decision = plan_file(
            "Hero.d2s",
            entry(b"pc", 60),
            entry(b"deck", 70),
            base,
            base,
        )
        self.assertEqual(decision.action, "conflict")
        self.assertEqual(decision.reason_code, "conflict_both")

    def test_remote_state_missing_still_uses_local_baseline(self):
        base = entry(b"base", 50).sha256
        changed = plan_file("Hero.d2s", entry(b"pc", 10), entry(b"base", 80), base, None)
        remote_changed = plan_file("Hero.d2s", entry(b"base", 10), entry(b"deck", 80), base, None)
        self.assertEqual(changed.action, "push")
        self.assertEqual(remote_changed.action, "pull")

    def test_disagreed_history_is_a_conflict_when_bytes_differ(self):
        decision = plan_file(
            "Hero.d2s",
            entry(b"pc", 10),
            entry(b"deck", 20),
            entry(b"pc", 10).sha256,
            entry(b"older", 1).sha256,
        )
        self.assertEqual(decision.action, "conflict")
        self.assertEqual(decision.reason_code, "conflict_history")

    def test_delete_propagates_when_the_surviving_copy_was_not_edited(self):
        base = entry(b"base", 50).sha256
        delete_remote = plan_file("Hero.d2s", None, entry(b"base", 50), base, base)
        delete_local = plan_file("Hero.key", entry(b"base", 50), None, base, base)
        self.assertEqual(delete_remote.action, "delete_remote")
        self.assertEqual(delete_local.action, "delete_local")

    def test_delete_versus_edit_is_a_conflict(self):
        base = entry(b"base", 50).sha256
        decision = plan_file("Hero.d2s", None, entry(b"edited", 90), base, base)
        other = plan_file("Hero.d2s", entry(b"edited", 90), None, base, base)
        self.assertEqual(decision.reason_code, "conflict_delete")
        self.assertEqual(other.reason_code, "conflict_delete")

    def test_missing_remote_state_does_not_delete_the_only_copy(self):
        base = entry(b"base", 50).sha256
        decision = plan_file("Hero.d2s", entry(b"base", 50), None, base, None)
        self.assertEqual(decision.action, "push")

    def test_plan_sorts_and_includes_stale_state_keys(self):
        from d2rsync.plan import plan

        decisions = plan(
            {"B.d2s": entry(b"b", 1)},
            {},
            {"A.d2s": "abc"},
            {},
        )
        self.assertEqual([item.rel for item in decisions], ["A.d2s", "B.d2s"])


class ResolveTests(unittest.TestCase):
    def _conflict(self, local_mtime=10, remote_mtime=30, local=b"pc", remote=b"deck"):
        return plan_file(
            "Hero.d2s",
            entry(local, local_mtime) if local is not None else None,
            entry(remote, remote_mtime) if remote is not None else None,
            None,
            None,
        )

    def test_newest_uses_the_later_mtime_and_does_not_archive(self):
        resolved = resolve(self._conflict(), "newest")
        self.assertEqual(resolved.op, "pull")
        self.assertTrue(resolved.conflict)
        self.assertFalse(resolved.archive_loser)
        self.assertEqual(resolved.reason_code, "newest")

    def test_newest_tie_keeps_both_and_prefers_local(self):
        resolved = resolve(self._conflict(local_mtime=10, remote_mtime=11), "newest")
        self.assertEqual(resolved.op, "push")
        self.assertTrue(resolved.archive_loser)
        self.assertEqual(resolved.reason_code, "keep_both_tie")

    def test_keep_both_archives_the_older_copy(self):
        resolved = resolve(self._conflict(), "keep-both")
        self.assertEqual(resolved.op, "pull")
        self.assertTrue(resolved.archive_loser)
        self.assertEqual(resolved.reason_code, "keep_both")

    def test_keep_both_on_a_delete_keeps_the_edited_file(self):
        base = entry(b"base", 1).sha256
        decision = plan_file("Hero.d2s", entry(b"edited", 5), None, base, base)
        resolved = resolve(decision, "keep-both")
        self.assertEqual(resolved.op, "push")
        self.assertEqual(resolved.reason_code, "keep_edit")
        self.assertFalse(resolved.archive_loser)

    def test_ask_without_a_choice_stays_unresolved(self):
        resolved = resolve(self._conflict(), "ask", None)
        self.assertEqual(resolved.op, "unresolved")

    def test_ask_choices(self):
        decision = self._conflict()
        self.assertEqual(resolve(decision, "ask", "local").op, "push")
        self.assertEqual(resolve(decision, "ask", "remote").op, "pull")
        self.assertEqual(resolve(decision, "ask", "skip").op, "skip")
        both = resolve(decision, "ask", "both")
        self.assertTrue(both.archive_loser)
        self.assertIn(both.op, ("push", "pull"))

    def test_non_conflict_passes_through(self):
        decision = plan_file("Hero.d2s", entry(b"only", 1), None, None, None)
        resolved = resolve(decision, "newest")
        self.assertEqual(resolved.op, "push")
        self.assertFalse(resolved.conflict)

    def test_preferred_side_ignores_a_two_second_gap(self):
        self.assertIsNone(preferred_side(entry(b"a", 10), entry(b"b", 12)))
        self.assertEqual(preferred_side(entry(b"a", 10), entry(b"b", 12.1)), "remote")
        self.assertEqual(preferred_side(None, entry(b"b", 1)), "remote")
        self.assertEqual(preferred_side(entry(b"a", 1), None), "local")


if __name__ == "__main__":
    unittest.main()
