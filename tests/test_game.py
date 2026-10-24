"""Process listing must not flash a console in the windowed Windows build."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest import mock

from d2rsync.game import _windows_processes, no_console_kwargs


class NoConsoleTests(unittest.TestCase):
    def test_non_windows_omits_creation_flags(self):
        with mock.patch("d2rsync.game.os.name", "posix"):
            self.assertEqual(no_console_kwargs(), {})

    def test_windows_tasklist_hides_its_console(self):
        flag = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        completed = mock.Mock(returncode=0, stdout='"D2R.exe","1234"\n')
        with mock.patch("d2rsync.game.os.name", "nt"), mock.patch(
            "d2rsync.game.subprocess.run", return_value=completed
        ) as run:
            names = _windows_processes()
        self.assertEqual(names, ["D2R.exe"])
        self.assertEqual(run.call_args.kwargs["creationflags"], flag)
        self.assertEqual(run.call_args.args[0][0], "tasklist")

    def test_windowed_spec_has_no_console(self):
        spec = Path(__file__).resolve().parents[1] / "D2RSaveSync.spec"
        text = spec.read_text(encoding="utf-8")
        self.assertIn("console=False", text)
        self.assertNotIn("console=True", text)
