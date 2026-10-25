"""Offscreen render of the three screens. Skipped when PySide6 is absent."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from d2rsync.config import load_config  # noqa: E402
from d2rsync.viewmodel import SettingsDraft  # noqa: E402
from tests.test_viewmodel import mockup_dashboard, mockup_result  # noqa: E402


def _qt():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return None
    return QApplication


class WindowSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        application = _qt()
        if application is None:
            raise unittest.SkipTest("PySide6 is not installed")
        from PySide6.QtWidgets import QApplication

        from d2rsync.ui.app import prepare

        cls.app = QApplication.instance() or QApplication([])
        prepare(cls.app)

    @classmethod
    def tearDownClass(cls):
        # Qt 6.12's offscreen plugin segfaults (exit 139) while Python is
        # destroying QApplication at interpreter shutdown. The tests have
        # already passed by then, but the CI job still fails. Delete the
        # application while the platform plugin is alive.
        app = getattr(cls, "app", None)
        cls.app = None
        if app is None:
            return
        import shiboken6

        app.closeAllWindows()
        for widget in list(app.allWidgets()):
            widget.deleteLater()
        app.processEvents()
        if shiboken6.isValid(app):
            shiboken6.delete(app)

    def test_three_screens_render_the_mockup_state(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QLabel

        from d2rsync.ui.window import MainWindow

        directory = Path(tempfile.mkdtemp())
        window = MainWindow(autostart=False, config_file=directory / "config.json")
        window.resize(1280, 800)
        self.assertFalse(window.windowIcon().isNull())
        window.dry.setChecked(True)
        window.show_dashboard_view(mockup_dashboard())
        window.show()
        self.app.processEvents()

        self.assertFalse(window.sync_btn.isEnabled())
        self.assertIn("Sync now", window.sync_btn.text())
        self.assertTrue(window.warn.isVisible())
        self.assertIn("D2R.exe", window.warn_text.text())
        self.assertIn("Steam Deck found", window.dash_pill_text.text())
        self.assertTrue(window.dry.isChecked())
        self.assertEqual(window.size().width(), 1280)
        self.assertEqual(window.size().height(), 800)

        shots = Path("/tmp/d2r-ui")
        shots.mkdir(parents=True, exist_ok=True)
        window.grab().save(str(shots / "screen1.png"))

        window.show_result_view(mockup_result())
        self.app.processEvents()
        texts = " ".join(label.text() for label in window.findChildren(QLabel))
        self.assertIn("DemoNekro", texts)
        self.assertIn("Level 62", texts)
        self.assertIn("keep both", texts)
        self.assertIn("EksempelHelt.d2s", texts)
        self.assertIn("3 files synced", window.result_pill_text.text())
        window.grab().save(str(shots / "screen2.png"))

        window.show_settings_draft(
            SettingsDraft(
                save_path="",
                role="host",
                peer_mode="auto",
                peer_host="192.168.1.42",
                port=47821,
                secret="super-secret-value",
                retention_days=30,
                block_game=True,
            ),
            detected=r"C:\Users\Spiller\Saved Games\Diablo II Resurrected",
        )
        self.app.processEvents()
        self.assertEqual(window.path_edit.text(), r"C:\Users\Spiller\Saved Games\Diablo II Resurrected")
        self.assertIn("DETECTED", window.path_label.text())
        self.assertEqual(window.role.current(), "host")
        self.assertEqual(window.host_edit.text(), "192.168.1.42")
        self.assertEqual(window.port.value(), 47821)
        self.assertEqual(window.secret.echoMode().name, "Password")
        self.assertEqual(window.retention_label.text(), "30 days")
        self.assertTrue(window.block.isChecked())
        window.grab().save(str(shots / "screen3.png"))

        window.nav_sync.click()
        self.app.processEvents()
        self.assertEqual(window.stack.currentIndex(), 0)
        window.nav_result.click()
        self.app.processEvents()
        self.assertEqual(window.stack.currentIndex(), 1)
        QTest.keyClick(window, Qt.Key.Key_Escape)
        self.app.processEvents()
        self.assertEqual(window.stack.currentIndex(), 0)

        before = window.dry.isChecked()
        QTest.mouseClick(window.dry, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(window.dry.isChecked(), not before)
        window.close()

    def test_settings_roundtrip_writes_the_config(self):
        from d2rsync.ui.window import MainWindow

        directory = Path(tempfile.mkdtemp())
        saves = directory / "saves"
        saves.mkdir()
        window = MainWindow(autostart=False, config_file=directory / "config.json")
        window.show_settings_draft(
            SettingsDraft(
                save_path=str(saves),
                role="host",
                peer_mode="manual",
                peer_host="10.0.0.8",
                port=42000,
                secret="another-secret",
                retention_days=14,
                block_game=False,
            )
        )
        window._save_settings()
        if window._worker is not None:
            window._worker.wait(8000)
        self.app.processEvents()
        loaded = load_config(directory / "config.json")
        self.assertEqual(loaded.role, "host")
        self.assertEqual(loaded.peer_mode, "manual")
        self.assertEqual(loaded.peer, "10.0.0.8")
        self.assertEqual(loaded.port, 42000)
        self.assertEqual(loaded.secret, "another-secret")
        self.assertEqual(loaded.backup_retention_days, 14)
        self.assertFalse(loaded.refuse_if_game_running)
        self.assertEqual(loaded.save_path, str(saves))

        window.role.set_current("client")
        window._cancel_settings()
        self.assertEqual(window.role.current(), "host")
        self.assertEqual(window.stack.currentIndex(), 0)
        window.close()

    def test_sync_uses_the_same_manual_peer_as_the_connection_test(self):
        from d2rsync.ui.window import MainWindow

        directory = Path(tempfile.mkdtemp())
        window = MainWindow(autostart=False, config_file=directory / "config.json")
        window.role.set_current("client")
        window.peer_mode.set_current("manual")
        window.host_edit.setText("192.168.178.69")
        window.port.setValue(41943)
        window.secret.setText("shared-secret")
        snap = window._snapshot_for_action()
        self.assertEqual(snap.peer_mode, "manual")
        self.assertEqual(snap.peer, "192.168.178.69")
        self.assertEqual(snap.port, 41943)
        self.assertEqual(window.cfg.peer_mode, "auto")
        self.assertIsNone(window.cfg.peer)
        window.close()

    def test_dashboard_pill_reports_a_connection_error(self):
        from d2rsync.ui.window import MainWindow

        directory = Path(tempfile.mkdtemp())
        window = MainWindow(autostart=False, config_file=directory / "config.json")
        window.cfg.role = "client"
        window.connected = False
        window.searching = False
        window._note_sticky = False
        window._note = "Could not connect. Check the IP, port, and that the host is running."
        window._render_dashboard_from_state()
        self.assertEqual(
            window.dash_pill_text.text(),
            "Could not connect. Check the IP, port, and that the host is running.",
        )
        window._note = "No single peer answered. Start the other machine, or set an IP in Settings."
        window._render_dashboard_from_state()
        self.assertEqual(window.dash_pill_text.text(), "No peer found")
        window.close()

    def test_character_checkboxes_save_the_exclude_list(self):
        from PySide6.QtWidgets import QCheckBox

        from d2rsync.ui.window import MainWindow
        from tests.support import write_file

        directory = Path(tempfile.mkdtemp())
        saves = directory / "saves"
        saves.mkdir()
        write_file(saves, "Hero.d2s", b"hero")
        write_file(saves, "Bloodoath.keyo", b"keys")
        write_file(saves, "Bloodoath.ctlo", b"pad")
        window = MainWindow(autostart=False, config_file=directory / "config.json")
        window.show_settings_draft(
            SettingsDraft(
                save_path=str(saves),
                role="client",
                peer_mode="auto",
                peer_host="",
                port=41943,
                secret="shared-secret",
                retention_days=30,
                block_game=True,
            )
        )
        self.app.processEvents()
        boxes = {
            box.property("character"): box
            for box in window.findChildren(QCheckBox)
            if box.property("character")
        }
        self.assertEqual(set(boxes), {"Hero", "Bloodoath"})
        self.assertTrue(boxes["Bloodoath"].isChecked())
        self.assertFalse(boxes["Bloodoath"].isEnabled())
        self.assertIn("Online", boxes["Bloodoath"].text())
        self.assertTrue(boxes["Hero"].isEnabled())
        boxes["Hero"].setChecked(True)
        window._save_settings()
        if window._worker is not None:
            window._worker.wait(8000)
        self.app.processEvents()
        loaded = load_config(directory / "config.json")
        self.assertEqual(loaded.exclude_characters, ["Hero"])
        self.assertNotIn("Bloodoath", loaded.exclude_characters)
        window.close()

    def test_host_notice_opens_the_result_and_returns_to_waiting(self):
        from PySide6.QtWidgets import QCheckBox, QLabel

        from d2rsync.model import Outcome
        from d2rsync.server import HostNotice
        from d2rsync.state import SyncState, save_key
        from d2rsync.ui.window import MainWindow

        directory = Path(tempfile.mkdtemp())
        saves = directory / "saves"
        saves.mkdir()
        (saves / "Hero.d2s").write_bytes(b"hero")
        (saves / "Bloodoath.keyo").write_bytes(b"online-keys")
        (saves / "Bloodoath.ctlo").write_bytes(b"online-pad")
        window = MainWindow(autostart=False, config_file=directory / "config.json")
        window.role.set_current("host")
        window.secret.setText("shared-secret")
        window._pinned = True
        window.path_edit.setText(str(saves))
        window.cfg.role = "host"
        window.cfg.secret = "shared-secret"
        window.cfg.save_path = str(saves)
        window.hosting = True
        SyncState(window._state_file(), save_key(saves)).mark_synced(1_700_000_000)
        window.show()
        self.app.processEvents()

        window.host_notice.emit(
            HostNotice("connected", "steamdeck", "192.168.1.20", detail="steamdeck connected.")
        )
        self.app.processEvents()
        self.assertEqual(window.dash_pill_text.text(), "steamdeck connected – 192.168.1.20")

        window.host_notice.emit(
            HostNotice("progress", "steamdeck", "192.168.1.20", detail="Sent Hero.d2s")
        )
        self.app.processEvents()
        self.assertEqual(window.dash_pill_text.text(), "Syncing with steamdeck – 192.168.1.20")
        self.assertIn("Listening", window.sync_btn.text())

        window.host_notice.emit(
            HostNotice(
                "finished",
                "steamdeck",
                "192.168.1.20",
                detail="Synced with steamdeck.",
                outcomes=(Outcome("Hero.d2s", "push", "sent to steamdeck"),),
                synced_at=1_700_000_000.0,
                show_result=True,
            )
        )
        self.app.processEvents()
        if window._worker is not None:
            window._worker.wait(8000)
        self.app.processEvents()
        self.assertEqual(window.stack.currentIndex(), 0)
        self.assertIn("1 file synced", window.dash_note.text())
        texts = " ".join(label.text() for label in window.findChildren(QLabel))
        self.assertIn("Sent to steamdeck", texts)
        self.assertTrue(window.hosting)
        self.assertIsNone(window._server)

        window._done()
        if window._worker is not None:
            window._worker.wait(8000)
        self.app.processEvents()
        self.assertEqual(window.stack.currentIndex(), 0)
        self.assertIn("waiting for the other machine", window.dash_pill_text.text())
        self.assertIn("steamdeck", window.footer.text())
        labels = " ".join(label.text() for label in window.findChildren(QLabel))
        self.assertIn("Bloodoath", labels)
        online = [
            box
            for box in window.findChildren(QCheckBox)
            if box.property("sync-character") == "Bloodoath"
        ]
        self.assertTrue(online)
        self.assertFalse(online[0].isChecked())
        self.assertFalse(online[0].isEnabled())
        window.close()

    def test_sync_screen_checkboxes_persist_exclude_and_lock_online(self):
        from PySide6.QtWidgets import QCheckBox

        from d2rsync.ui.window import MainWindow
        from tests.support import write_file

        directory = Path(tempfile.mkdtemp())
        saves = directory / "saves"
        saves.mkdir()
        write_file(saves, "Morga.d2s", b"morga")
        write_file(saves, "Hero.d2s", b"hero")
        write_file(saves, "Bloodoath.keyo", b"keys")
        write_file(saves, "Bloodoath.ctlo", b"pad")
        window = MainWindow(autostart=False, config_file=directory / "config.json")
        window.cfg.secret = "shared-secret"
        window.cfg.save_path = str(saves)
        window._pinned = True
        window.path_edit.setText(str(saves))
        window.save_folder = saves
        window._render_dashboard_from_state()
        self.app.processEvents()
        boxes = {
            box.property("sync-character"): box
            for box in window.findChildren(QCheckBox)
            if box.property("sync-character")
        }
        self.assertEqual(set(boxes), {"Morga", "Hero", "Bloodoath"})
        self.assertTrue(boxes["Morga"].isChecked())
        self.assertTrue(boxes["Hero"].isChecked())
        self.assertFalse(boxes["Bloodoath"].isChecked())
        self.assertFalse(boxes["Bloodoath"].isEnabled())
        boxes["Morga"].setChecked(False)
        self.app.processEvents()
        loaded = load_config(directory / "config.json")
        self.assertEqual(loaded.exclude_characters, ["Morga"])
        self.assertNotIn("Bloodoath", loaded.exclude_characters)
        window.close()

    def test_deck_sync_stays_on_sync_and_refreshes_both_machines(self):
        """Morga must not keep yesterday vs 23 Oct, and Last sync must move."""
        import threading
        from datetime import datetime, timedelta

        from PySide6.QtWidgets import QLabel

        from d2rsync.backup import BackupManager
        from d2rsync.hashing import sha256_bytes
        from d2rsync.server import Server
        from d2rsync.state import SyncState, save_key
        from d2rsync.store import DirectorySave
        from d2rsync.ui.window import MainWindow
        from d2rsync.viewmodel import format_when
        from tests.support import write_file

        now = datetime.now().replace(microsecond=0)
        pc_time = (now - timedelta(days=1)).replace(hour=18, minute=0, second=0).timestamp()
        deck_time = (now - timedelta(days=2)).replace(hour=9, minute=0, second=0).timestamp()
        first = (now - timedelta(days=1)).replace(hour=20, minute=22, second=0).timestamp()
        data = b"morga-from-pc"
        digest = sha256_bytes(data)
        root = Path(tempfile.mkdtemp())
        deck_root = root / "deck"
        pc_root = root / "pc"
        deck_root.mkdir()
        pc_root.mkdir()
        write_file(pc_root, "Morga.d2s", data, pc_time)
        write_file(deck_root, "Morga.d2s", data, deck_time)

        deck = MainWindow(autostart=False, config_file=root / "deck-config" / "config.json")
        host = MainWindow(autostart=False, config_file=root / "pc-config" / "config.json")
        SyncState(deck._state_file(), save_key(deck_root), {"Morga.d2s": digest}).mark_synced(first)
        SyncState(host._state_file(), save_key(pc_root), {"Morga.d2s": digest}).mark_synced(first)

        server_box: dict = {}

        def forward(notice):
            host.host_notice.emit(notice)

        save = DirectorySave(pc_root, BackupManager(pc_root))
        server = Server(
            save,
            SyncState(host._state_file(), save_key(pc_root), {"Morga.d2s": digest}, first),
            "shared-secret",
            host="127.0.0.1",
            port=0,
            name="pc",
            discover=False,
            fail_delay=0,
            game_check=lambda: False,
            on_notice=forward,
        )
        server_box["server"] = server
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        server.wait_until_listening()
        try:
            self._arm_client(deck, deck_root, server.bound_port)
            deck.cfg.name = "deck"
            host.role.set_current("host")
            host.cfg.role = "host"
            host.cfg.name = "pc"
            host.cfg.secret = "shared-secret"
            host.cfg.save_path = str(pc_root)
            host.secret.setText("shared-secret")
            host._pinned = True
            host.path_edit.setText(str(pc_root))
            host.save_folder = pc_root
            host.hosting = True
            host.synced_at = first
            host.last_peer_name = "deck"
            host.show()
            deck.show()
            self.app.processEvents()

            deck._start_sync()
            self._drain(deck)
            self._drain(host)

            self.assertEqual(deck.stack.currentIndex(), 0, deck.dash_note.text())
            self.assertEqual(host.stack.currentIndex(), 0, host.dash_note.text())
            stale = format_when(deck_time, datetime.now())
            current = format_when(pc_time, datetime.now())
            first_label = format_when(first, datetime.now())
            for window, peer in ((deck, "pc"), (host, "deck")):
                labels = " ".join(label.text() for label in window.findChildren(QLabel))
                self.assertNotIn(stale, labels, labels)
                self.assertGreaterEqual(labels.count(current), 2, labels)
                self.assertNotIn(first_label, window.footer.text(), window.footer.text())
                self.assertIn(peer, window.footer.text())
                self.assertIn("file", window.dash_note.text().lower(), window.dash_note.text())
        finally:
            server.stop()
            thread.join(timeout=3)
            deck.close()
            host.close()

    def _arm_client(self, window, folder: Path, port: int) -> None:
        window.role.set_current("client")
        window.peer_mode.set_current("manual")
        window.host_edit.setText("127.0.0.1")
        window.port.setValue(port)
        window.secret.setText("shared-secret")
        window.block.setChecked(False)
        window._pinned = True
        window.path_edit.setText(str(folder))
        window.save_folder = folder
        window.cfg.role = "client"
        window.cfg.peer_mode = "manual"
        window.cfg.peer = "127.0.0.1"
        window.cfg.port = port
        window.cfg.secret = "shared-secret"
        window.cfg.save_path = str(folder)
        window.cfg.refuse_if_game_running = False

    def _drain(self, window) -> None:
        for _ in range(8):
            self.app.processEvents()
            worker = window._worker
            if worker is not None and worker.isRunning():
                worker.wait(8000)
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
