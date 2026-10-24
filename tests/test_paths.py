"""Save-folder detection on Windows paths and Proton prefixes."""

from __future__ import annotations

import unittest
from pathlib import Path

from d2rsync.paths import (
    STEAM_APPID,
    choose_save_folder,
    detect_save_folders,
    parse_library_paths,
)
from tests.support import write_file


VDF = """
"libraryfolders"
{
	"0"
	{
		"path"		"/home/deck/.local/share/Steam"
	}
	"1"
	{
		"path"		"/run/media/mmcblk0p1/SteamLibrary"
	}
}
"LibraryFolders"
{
	"TimeNextStatsReport"		"123"
	"ContentStatsID"		"-1"
	"1"		"D:\\\\SteamLibrary"
}
"""


class VdfTests(unittest.TestCase):
    def test_parse_modern_and_legacy_library_paths(self):
        self.assertEqual(
            parse_library_paths(VDF),
            [
                "/home/deck/.local/share/Steam",
                "/run/media/mmcblk0p1/SteamLibrary",
                "D:\\SteamLibrary",
            ],
        )


class DetectTests(unittest.TestCase):
    def test_windows_saved_games_and_documents(self):
        home = Path(self._tmp())
        profile = home / "Nick"
        saved = profile / "Saved Games" / "Diablo II Resurrected"
        docs = profile / "Documents" / "Diablo II Resurrected" / "Save"
        write_file(saved, "Hero.d2s", b"saved", 50)
        write_file(docs, "Old.d2s", b"docs", 10)
        found = detect_save_folders(
            platform="win32",
            home=home,
            userprofile=profile,
            scan_removable=False,
        )
        self.assertEqual(found[0].path, saved)
        self.assertEqual(found[0].kind, "windows")
        self.assertEqual({item.path for item in found}, {saved, docs})

    def test_proton_scan_prefers_the_newest_saves_over_the_known_appid(self):
        home = Path(self._tmp())
        steam = home / ".local" / "share" / "Steam"
        official = self._prefix(steam, STEAM_APPID)
        bnet = self._prefix(steam, "4123456789")
        write_file(official, "Old.d2s", b"old", 10)
        write_file(bnet, "New.d2s", b"new", 99)
        # Case differences Proton sometimes shows up with.
        weird = (
            steam
            / "steamapps"
            / "compatdata"
            / "7"
            / "pfx"
            / "drive_c"
            / "users"
            / "steamuser"
            / "saved games"
            / "diablo ii resurrected"
        )
        write_file(weird, "Case.d2s", b"case", 20)
        found = detect_save_folders(
            platform="linux",
            home=home,
            environ={},
            steam_roots=[steam],
            scan_removable=False,
        )
        self.assertEqual(found[0].path.resolve(), bnet.resolve())
        self.assertEqual(found[0].appid, "4123456789")
        appids = {item.appid for item in found}
        self.assertIn(STEAM_APPID, appids)
        self.assertIn("7", appids)

    def test_sd_card_library_from_libraryfolders(self):
        home = Path(self._tmp())
        steam = home / ".local" / "share" / "Steam"
        (steam / "steamapps").mkdir(parents=True)
        card = home / "sd" / "SteamLibrary"
        saves = self._prefix(card, "555")
        write_file(saves, "Hero.d2s", b"sd", 5)
        vdf = steam / "steamapps" / "libraryfolders.vdf"
        vdf.write_text(
            '"libraryfolders"\n{\n"0"\n{\n"path"\t"' + str(card) + '"\n}\n}\n',
            encoding="utf-8",
        )
        found = detect_save_folders(
            platform="linux",
            home=home,
            environ={},
            scan_removable=False,
        )
        self.assertTrue(any(item.path.resolve() == saves.resolve() for item in found))

    def test_documents_save_under_proton_is_found(self):
        home = Path(self._tmp())
        steam = home / ".local" / "share" / "Steam"
        docs = (
            steam
            / "steamapps"
            / "compatdata"
            / "99"
            / "pfx"
            / "drive_c"
            / "users"
            / "steamuser"
            / "Documents"
            / "Diablo II Resurrected"
            / "Save"
        )
        write_file(docs, "Hero.d2s", b"docs", 3)
        found = detect_save_folders(
            platform="linux",
            home=home,
            environ={},
            steam_roots=[steam],
            scan_removable=False,
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].kind, "proton-documents")

    def test_override_wins_and_missing_override_errors(self):
        home = Path(self._tmp())
        pinned = home / "pinned"
        pinned.mkdir()
        chosen = choose_save_folder([], pinned)
        self.assertEqual(chosen, pinned)
        with self.assertRaises(FileNotFoundError):
            choose_save_folder([], home / "missing")

    def test_known_appid_breaks_a_tie_when_mtimes_match(self):
        home = Path(self._tmp())
        steam = home / ".local" / "share" / "Steam"
        official = self._prefix(steam, STEAM_APPID)
        other = self._prefix(steam, "100")
        write_file(official, "Hero.d2s", b"a", 20)
        write_file(other, "Hero.d2s", b"b", 20)
        found = detect_save_folders(
            platform="linux",
            home=home,
            environ={},
            steam_roots=[steam],
            scan_removable=False,
        )
        self.assertEqual(found[0].appid, STEAM_APPID)

    def _prefix(self, library: Path, appid: str) -> Path:
        return (
            library
            / "steamapps"
            / "compatdata"
            / appid
            / "pfx"
            / "drive_c"
            / "users"
            / "steamuser"
            / "Saved Games"
            / "Diablo II Resurrected"
        )

    def _tmp(self) -> str:
        import tempfile

        return tempfile.mkdtemp(prefix="d2r-path-")


if __name__ == "__main__":
    unittest.main()
