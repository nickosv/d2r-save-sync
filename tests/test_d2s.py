"""Diablo II character header parsing."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from d2rsync.d2s import parse_d2s, read_local_d2s
from tests.support import make_d2s


class D2STests(unittest.TestCase):
    def test_reads_name_class_and_level(self):
        info = parse_d2s(make_d2s("EksempelHelt", 1, 87))
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info.name, "EksempelHelt")
        self.assertEqual(info.class_name, "Sorceress")
        self.assertEqual(info.level, 87)

    def test_rejects_a_bad_header(self):
        self.assertIsNone(parse_d2s(b"not a character"))
        self.assertIsNone(parse_d2s(make_d2s("Hero", 1, 87)[:20]))
        self.assertIsNone(parse_d2s(make_d2s("Hero", 1, 0)))
        self.assertIsNone(parse_d2s(make_d2s("Hero", 1, 100)))
        self.assertIsNone(parse_d2s(make_d2s("Hero", 9, 10)))

    def test_read_local_keeps_only_character_files(self):
        root = Path(tempfile.mkdtemp())
        (root / "Hero.d2s").write_bytes(make_d2s("Hero", 0, 10))
        (root / "Hero.d2s.bak").write_bytes(make_d2s("Nope", 0, 10))
        (root / "notes.txt").write_bytes(b"leave me")
        found = read_local_d2s(root)
        self.assertEqual(list(found), ["Hero.d2s"])
        self.assertEqual(parse_d2s(found["Hero.d2s"]).class_name, "Amazon")


if __name__ == "__main__":
    unittest.main()
