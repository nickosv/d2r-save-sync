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
        from PySide6.QtWidgets import QLabel

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
        self.assertEqual(window.stack.currentIndex(), 1)
        self.assertIn("1 file synced", window.result_pill_text.text())
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
        self.assertIn("Up to date", labels)
        self.assertNotIn("Bloodoath", labels)
        window.close()


if __name__ == "__main__":
    unittest.main()
