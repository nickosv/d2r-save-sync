"""Danish view-model: dashboard rows, conflict card, and settings draft."""

from __future__ import annotations

import unittest
from datetime import datetime

from d2rsync.config import Config
from d2rsync.errors import ConfigError, D2RSyncError, GameRunningError, SavePathError
from d2rsync.model import Decision, Entry, Outcome
from d2rsync.viewmodel import (
    apply_draft,
    build_result_view,
    build_rows,
    conflict_character_d2s,
    MISSING_SAVE_FOLDER,
    danish_error,
    draft_from_config,
    format_da_time,
    format_footer,
    gui_conflict_policy,
    make_dashboard_view,
    sync_button_state,
)
from tests.support import make_d2s

NOW = datetime(2026, 10, 24, 12, 0)


def _ts(day: int, hour: int, minute: int) -> float:
    return datetime(2026, 10, day, hour, minute).timestamp()


def _entry(mtime: float, digest: str = "a") -> Entry:
    return Entry(digest * 64, 10, mtime)


def _decision(rel: str, action: str, local_m: float | None, remote_m: float | None) -> Decision:
    return Decision(
        rel,
        action,
        "conflict_both" if action == "conflict" else "changed",
        "changed on both sides" if action == "conflict" else "changed",
        _entry(local_m, "a") if local_m is not None else None,
        _entry(remote_m, "b") if remote_m is not None else None,
    )


def mockup_files():
    local = {
        "EksempelHelt.d2s": _entry(_ts(24, 10, 12), "1"),
        "TestPaladin.d2s": _entry(_ts(21, 19, 3), "2"),
        "DemoNekro.d2s": _entry(_ts(24, 8, 30), "3"),
        "DemoNekro.key": _entry(_ts(24, 8, 30), "4"),
        "DemoNekro.ma0": _entry(_ts(24, 8, 30), "5"),
        "PrøveAmazone.d2s": _entry(_ts(18, 21, 15), "6"),
        "SharedStashSoftCoreV2.d2i": _entry(_ts(24, 10, 12), "7"),
    }
    remote = {
        "EksempelHelt.d2s": _entry(_ts(23, 22, 40), "8"),
        "TestPaladin.d2s": _entry(_ts(24, 9, 55), "9"),
        "DemoNekro.d2s": _entry(_ts(24, 8, 41), "c"),
        "DemoNekro.key": _entry(_ts(24, 8, 41), "d"),
        "DemoNekro.ma0": _entry(_ts(24, 8, 41), "e"),
        "PrøveAmazone.d2s": _entry(_ts(18, 21, 15), "6"),
        "SharedStashSoftCoreV2.d2i": _entry(_ts(23, 22, 40), "f"),
    }
    decisions = [
        _decision("EksempelHelt.d2s", "push", _ts(24, 10, 12), _ts(23, 22, 40)),
        _decision("TestPaladin.d2s", "pull", _ts(21, 19, 3), _ts(24, 9, 55)),
        _decision("DemoNekro.d2s", "conflict", _ts(24, 8, 30), _ts(24, 8, 41)),
        _decision("DemoNekro.key", "conflict", _ts(24, 8, 30), _ts(24, 8, 41)),
        _decision("DemoNekro.ma0", "conflict", _ts(24, 8, 30), _ts(24, 8, 41)),
        _decision("PrøveAmazone.d2s", "none", _ts(18, 21, 15), _ts(18, 21, 15)),
        _decision("SharedStashSoftCoreV2.d2i", "push", _ts(24, 10, 12), _ts(23, 22, 40)),
    ]
    local_d2s = {
        "EksempelHelt.d2s": make_d2s("EksempelHelt", 1, 87),
        "TestPaladin.d2s": make_d2s("TestPaladin", 3, 74),
        "DemoNekro.d2s": make_d2s("DemoNekro", 2, 61),
        "PrøveAmazone.d2s": make_d2s("PrøveAmazone", 0, 45),
    }
    remote_d2s = {
        "EksempelHelt.d2s": make_d2s("EksempelHelt", 1, 87),
        "TestPaladin.d2s": make_d2s("TestPaladin", 3, 74),
        "DemoNekro.d2s": make_d2s("DemoNekro", 2, 62),
        "PrøveAmazone.d2s": make_d2s("PrøveAmazone", 0, 45),
    }
    return local, remote, decisions, local_d2s, remote_d2s


def mockup_dashboard():
    local, remote, decisions, local_d2s, remote_d2s = mockup_files()
    rows = build_rows(local, remote, decisions, local_d2s, remote_d2s, NOW)
    view = make_dashboard_view(
        rows=rows,
        peer_name="Steam Deck",
        peer_host="192.168.1.42",
        connected=True,
        searching=False,
        role="client",
        hosting=False,
        game_running=True,
        peer_game_running=False,
        block_game=True,
        busy=False,
        has_secret=True,
        has_save=True,
        synced_at=_ts(23, 22, 41),
        now=NOW,
    )
    return view


def mockup_result():
    _local, _remote, decisions, local_d2s, remote_d2s = mockup_files()
    outcomes = [
        Outcome("EksempelHelt.d2s", "push", "changed", False, "", "backup/2026-10-24_1014/EksempelHelt.d2s"),
        Outcome("TestPaladin.d2s", "pull", "changed", False, "", "backup/2026-10-24_1014/TestPaladin.d2s"),
        Outcome(
            "DemoNekro.d2s",
            "pull",
            "conflict",
            True,
            "",
            "",
            ".d2r-sync-backups/conflicts/20261024-0830-from-deck/DemoNekro.d2s",
        ),
        Outcome("DemoNekro.key", "pull", "conflict", True, "", "", ".d2r-sync-backups/conflicts/20261024-0830-from-deck/DemoNekro.key"),
        Outcome("DemoNekro.ma0", "pull", "conflict", True, "", "", ".d2r-sync-backups/conflicts/20261024-0830-from-deck/DemoNekro.ma0"),
        Outcome("PrøveAmazone.d2s", "unchanged", "match"),
        Outcome("SharedStashSoftCoreV2.d2i", "push", "changed", False, "", "backup/2026-10-24_1014/SharedStashSoftCoreV2.d2i"),
    ]
    return build_result_view(
        decisions=decisions,
        resolved=[],
        outcomes=outcomes,
        local_d2s=local_d2s,
        remote_d2s=remote_d2s,
        peer_name="Steam Deck",
        dry_run=False,
        conflict_policy="keep-both",
        now=NOW,
        log_text="plan",
    )


class TimeTests(unittest.TestCase):
    def test_danish_calendar_words(self):
        self.assertEqual(format_da_time(_ts(24, 10, 12), NOW), "i dag 10:12")
        self.assertEqual(format_da_time(_ts(23, 22, 41), NOW), "i går 22:41")
        self.assertEqual(format_da_time(_ts(18, 21, 15), NOW), "18. okt 21:15")
        self.assertEqual(format_da_time(None, NOW), "—")
        self.assertIn("i går 22:41", format_footer(_ts(23, 22, 41), NOW))
        self.assertIn("aldrig", format_footer(None, NOW))


class DashboardTests(unittest.TestCase):
    def test_rows_use_the_plan_action_and_danish_badges(self):
        view = mockup_dashboard()
        by_title = {row.title: row for row in view.rows}
        self.assertEqual(
            [row.title for row in view.rows],
            ["DemoNekro", "EksempelHelt", "PrøveAmazone", "TestPaladin", "Fælles stash"],
        )
        hero = by_title["EksempelHelt"]
        self.assertEqual(hero.subtitle, "Sorceress · 87")
        self.assertEqual(hero.status, "send")
        self.assertEqual(hero.status_label, "↑ Sendes")
        self.assertTrue(hero.local_newer)
        self.assertEqual(hero.local_time, "i dag 10:12")
        self.assertEqual(hero.remote_time, "i går 22:40")

        paladin = by_title["TestPaladin"]
        self.assertEqual(paladin.subtitle, "Paladin · 74")
        self.assertEqual(paladin.status, "get")
        self.assertTrue(paladin.remote_newer)
        self.assertEqual(paladin.remote_time, "i dag 09:55")

        necro = by_title["DemoNekro"]
        self.assertEqual(necro.status, "conflict")
        self.assertEqual(necro.status_label, "⚔ Konflikt")
        self.assertEqual(necro.subtitle, "Necromancer · 61")

        amazon = by_title["PrøveAmazone"]
        self.assertEqual(amazon.status, "ok")
        self.assertEqual(amazon.status_label, "✓ Opdateret")
        self.assertFalse(amazon.local_newer)
        self.assertFalse(amazon.remote_newer)

        stash = by_title["Fælles stash"]
        self.assertEqual(stash.subtitle, "Delt kiste")
        self.assertEqual(stash.detail, "SharedStashSoftCoreV2.d2i")
        self.assertEqual(stash.status, "send")

        self.assertEqual(view.pill_text, "Steam Deck fundet – 192.168.1.42")
        self.assertEqual(view.peer_header, "Steam Deck")
        self.assertIn("D2R.exe", view.warning or "")
        self.assertFalse(view.button_enabled)
        self.assertEqual(view.button_text, "Synkroniser nu")
        self.assertIn("i går 22:41", view.footer)

    def test_unknown_status_before_a_peer_answers(self):
        local, _remote, _decisions, local_d2s, _remote_d2s = mockup_files()
        rows = build_rows(local, {}, None, local_d2s, {}, NOW)
        self.assertTrue(all(row.status == "unknown" for row in rows))

    def test_sync_button_follows_role_game_and_busy(self):
        blocked = sync_button_state(
            role="client",
            game_running=True,
            block_game=True,
            busy=False,
            has_secret=True,
            has_save=True,
            hosting=False,
        )
        self.assertFalse(blocked.enabled)
        allowed = sync_button_state(
            role="client",
            game_running=True,
            block_game=False,
            busy=False,
            has_secret=True,
            has_save=True,
            hosting=False,
        )
        self.assertTrue(allowed.enabled)
        self.assertEqual(
            sync_button_state(
                role="host", game_running=False, block_game=True, busy=False, has_secret=True, has_save=True, hosting=False
            ).text,
            "Start vært",
        )
        listening = sync_button_state(
            role="host", game_running=False, block_game=True, busy=False, has_secret=True, has_save=True, hosting=True
        )
        self.assertEqual(listening.text, "Lytter…")
        self.assertFalse(listening.enabled)
        busy = sync_button_state(
            role="client", game_running=False, block_game=True, busy=True, has_secret=True, has_save=True, hosting=False
        )
        self.assertEqual(busy.text, "Arbejder…")
        self.assertFalse(busy.enabled)


class ResultTests(unittest.TestCase):
    def test_conflict_card_keeps_both_and_leaves_those_files_out_of_the_table(self):
        view = mockup_result()
        self.assertEqual(view.title, "Synkronisering fuldført")
        self.assertEqual(view.pill, "3 filer synkroniseret · 1 konflikt løst")
        self.assertIsNotNone(view.conflict)
        card = view.conflict
        assert card is not None
        self.assertEqual(card.name, "DemoNekro")
        self.assertEqual(card.class_name, "Necromancer")
        self.assertIn("begge enheder", card.reason)
        self.assertEqual(card.winner.heading, "Steam Deck")
        self.assertIn("Niveau 62", card.winner.line)
        self.assertIn("NYESTE", card.winner.badge)
        self.assertEqual(card.loser.heading, "Denne PC")
        self.assertIn("Niveau 61", card.loser.line)
        self.assertIn("ÆLDRE", card.loser.badge)
        self.assertIn("DemoNekro.d2s", card.loser.hint)
        self.assertIn("behold begge", card.policy)
        names = [item.filename for item in view.files]
        self.assertEqual(names, ["EksempelHelt.d2s", "TestPaladin.d2s", "SharedStashSoftCoreV2.d2i"])
        self.assertEqual(view.files[0].action, "↑ Sendt til Steam Deck")
        self.assertEqual(view.files[1].action, "↓ Hentet fra Steam Deck")
        self.assertEqual(view.files[0].backup, "backup/2026-10-24_1014/")
        self.assertNotIn("DemoNekro", " ".join(names))

    def test_dry_run_wording_and_stopped_title(self):
        view = mockup_result()
        dry = build_result_view(
            decisions=[],
            resolved=[],
            outcomes=[Outcome("Hero.d2s", "push", "changed")],
            local_d2s={},
            remote_d2s={},
            peer_name="Steam Deck",
            dry_run=True,
            conflict_policy="keep-both",
            now=NOW,
            log_text="",
        )
        self.assertEqual(dry.title, "Prøvekørsel")
        self.assertIn("ville synkroniseres", dry.pill)
        self.assertIn("Sendes til", dry.files[0].action)
        stopped = build_result_view(
            decisions=[],
            resolved=[],
            outcomes=[Outcome("Hero.d2s", "error", "disk full")],
            local_d2s={},
            remote_d2s={},
            peer_name="Steam Deck",
            dry_run=False,
            conflict_policy="keep-both",
            now=NOW,
            log_text="",
        )
        self.assertEqual(stopped.title, "Synkronisering stoppet")
        self.assertIsNotNone(view)


class SettingsTests(unittest.TestCase):
    def test_draft_roundtrip_splits_a_host_and_port(self):
        cfg = Config(peer="192.168.1.42:47821", port=41943, secret="12345678", role="host", peer_mode="manual")
        draft = draft_from_config(cfg)
        self.assertEqual(draft.peer_host, "192.168.1.42")
        self.assertEqual(draft.port, 47821)
        fresh = Config()
        apply_draft(fresh, draft)
        self.assertEqual(fresh.role, "host")
        self.assertEqual(fresh.peer_mode, "manual")
        self.assertEqual(fresh.peer, "192.168.1.42")
        self.assertEqual(fresh.port, 47821)
        self.assertEqual(fresh.secret, "12345678")

    def test_rejects_a_short_secret_and_a_zero_retention(self):
        cfg = Config()
        draft = draft_from_config(cfg)
        draft.secret = "short"
        with self.assertRaises(ConfigError):
            apply_draft(cfg, draft)
        draft.secret = ""
        draft.retention_days = 0
        with self.assertRaises(ConfigError):
            apply_draft(cfg, draft)

    def test_gui_policy_never_asks(self):
        self.assertEqual(gui_conflict_policy("ask"), "keep-both")
        self.assertEqual(gui_conflict_policy("keep-both"), "keep-both")
        self.assertEqual(gui_conflict_policy("newest"), "newest")
        self.assertEqual(gui_conflict_policy("anything"), "keep-both")

    def test_conflict_d2s_includes_the_sibling_character(self):
        decisions = [_decision("Hero.key", "conflict", 1.0, 2.0)]
        names = conflict_character_d2s(decisions, ["Hero.d2s", "Hero.key", "Other.d2s"])
        self.assertEqual(names, ["Hero.d2s"])
        self.assertEqual(conflict_character_d2s([], ["Hero.d2s"]), [])


class ErrorTextTests(unittest.TestCase):
    def test_known_failures_are_danish(self):
        self.assertIn("kører", danish_error(GameRunningError("the game appears to be running")))
        self.assertIn("hemmelighed", danish_error(D2RSyncError("shared secret rejected")))
        self.assertIn("modpart", danish_error(D2RSyncError("no peer answered")))
        self.assertIn("IP-adressen", danish_error(D2RSyncError("no peer address configured")))
        missing = SavePathError(
            "could not find a Diablo II Resurrected save folder. Set save_path in the config file."
        )
        self.assertEqual(danish_error(missing), MISSING_SAVE_FOLDER)
        view = make_dashboard_view(
            rows=[],
            peer_name="",
            peer_host="",
            connected=False,
            searching=False,
            role="client",
            hosting=False,
            game_running=False,
            peer_game_running=False,
            block_game=True,
            busy=False,
            has_secret=True,
            has_save=False,
            synced_at=None,
            now=NOW,
        )
        self.assertEqual(view.empty_text, MISSING_SAVE_FOLDER)
        searching = make_dashboard_view(
            rows=[],
            peer_name="",
            peer_host="",
            connected=False,
            searching=True,
            role="client",
            hosting=False,
            game_running=False,
            peer_game_running=False,
            block_game=True,
            busy=False,
            has_secret=True,
            has_save=False,
            synced_at=None,
            now=NOW,
        )
        self.assertNotEqual(searching.empty_text, MISSING_SAVE_FOLDER)


if __name__ == "__main__":
    unittest.main()
