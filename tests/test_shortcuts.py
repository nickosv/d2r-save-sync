"""Windows shortcuts launch pythonw -m d2rsync, with no extra launcher file."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from d2rsync.cli import main
from d2rsync.shortcuts import (
    ARGUMENTS,
    SHORTCUT_NAME,
    ShortcutError,
    icon_path,
    install_shortcuts,
    read_shell_link,
    resolve_pythonw,
)


class ShortcutTests(unittest.TestCase):
    def test_icon_is_packaged_as_an_ico(self):
        path = icon_path()
        self.assertEqual(path.name, "d2rsync.ico")
        self.assertTrue(path.is_file())
        self.assertTrue(path.read_bytes().startswith(b"\x00\x00\x01\x00"))

    def test_shortcut_arguments_are_the_module(self):
        self.assertEqual(ARGUMENTS, "-m d2rsync")
        self.assertNotIn(".exe", ARGUMENTS)
        self.assertNotIn(".bat", ARGUMENTS)

    def test_pythonw_is_the_sibling_of_python(self):
        directory = Path(tempfile.mkdtemp())
        python = directory / "python.exe"
        pythonw = directory / "pythonw.exe"
        python.write_bytes(b"py")
        pythonw.write_bytes(b"pyw")
        self.assertEqual(resolve_pythonw(python), pythonw)
        self.assertEqual(resolve_pythonw(pythonw), pythonw)

    def test_missing_pythonw_names_the_python_org_install(self):
        directory = Path(tempfile.mkdtemp())
        python = directory / "python.exe"
        python.write_bytes(b"py")
        with self.assertRaises(ShortcutError) as caught:
            resolve_pythonw(python)
        message = str(caught.exception)
        self.assertIn("pythonw.exe", message)
        self.assertIn("Python.Python.3.12", message)

    def test_project_does_not_declare_pip_exe_launchers(self):
        text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        self.assertNotIn("[project.scripts]", text)
        self.assertNotIn("[project.gui-scripts]", text)

    def test_create_shortcuts_flag_reports_the_links(self):
        from d2rsync import shortcuts

        destinations = [
            Path("Desktop") / SHORTCUT_NAME,
            Path("Programs") / SHORTCUT_NAME,
        ]

        def fake(folders=None):
            self.assertIsNone(folders)
            return destinations

        original = shortcuts.install_shortcuts
        shortcuts.install_shortcuts = fake
        try:
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["--create-shortcuts"])
        finally:
            shortcuts.install_shortcuts = original
        self.assertEqual(code, 0)
        self.assertIn(SHORTCUT_NAME, stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_install_is_refused_off_windows(self):
        if sys.platform == "win32":
            self.skipTest("the Windows runner creates a real link in a temp folder")
        with self.assertRaises(ShortcutError) as caught:
            install_shortcuts()
        self.assertIn("Windows", str(caught.exception))

    @unittest.skipUnless(sys.platform == "win32", "COM shell links are created on Windows")
    def test_shortcut_targets_pythonw_with_the_module_and_icon(self):
        folder = Path(tempfile.mkdtemp())
        written = install_shortcuts([folder])
        self.assertEqual([path.name for path in written], [SHORTCUT_NAME])
        self.assertFalse(list(folder.glob("*.exe")))
        self.assertFalse(list(folder.glob("*.bat")))
        self.assertFalse(list(folder.glob("*.ps1")))
        target, arguments, icon = read_shell_link(written[0])
        self.assertTrue(target.lower().endswith("pythonw.exe"), target)
        self.assertTrue(Path(target).is_file(), target)
        self.assertEqual(arguments, "-m d2rsync")
        self.assertTrue(icon.lower().endswith("d2rsync.ico"), icon)
        self.assertEqual(Path(icon).resolve(), icon_path().resolve())
