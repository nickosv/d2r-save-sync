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

    def test_three_screens_render_the_mockup_state(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QLabel

        from d2rsync.ui.window import MainWindow

        directory = Path(tempfile.mkdtemp())
        window = MainWindow(autostart=False, config_file=directory / "config.json")
        window.resize(1280, 800)
        window.dry.setChecked(True)
        window.show_dashboard_view(mockup_dashboard())
        window.show()
        self.app.processEvents()

        self.assertFalse(window.sync_btn.isEnabled())
        self.assertIn("Synkroniser nu", window.sync_btn.text())
        self.assertTrue(window.warn.isVisible())
        self.assertIn("D2R.exe", window.warn_text.text())
        self.assertIn("Steam Deck fundet", window.dash_pill_text.text())
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
        self.assertIn("Niveau 62", texts)
        self.assertIn("behold begge", texts)
        self.assertIn("EksempelHelt.d2s", texts)
        self.assertIn("3 filer synkroniseret", window.result_pill_text.text())
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
        self.assertIn("AUTOMATISK", window.path_label.text())
        self.assertEqual(window.role.current(), "host")
        self.assertEqual(window.host_edit.text(), "192.168.1.42")
        self.assertEqual(window.port.value(), 47821)
        self.assertEqual(window.secret.echoMode().name, "Password")
        self.assertEqual(window.retention_label.text(), "30 dage")
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


if __name__ == "__main__":
    unittest.main()
