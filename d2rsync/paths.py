"""Find Diablo II: Resurrected save folders on Windows and under Proton."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from d2rsync.names import is_syncable_name

# Steam release of Diablo II: Resurrected. Battle.net-via-Proton uses a
# random compatdata id, so this is only a tie-break, not a requirement.
STEAM_APPID = "2536520"

# Relative to a Windows user profile (also a Proton prefix user dir).
_PROFILE_TAILS = (
    ("Saved Games", "Diablo II Resurrected"),
    ("Documents", "Diablo II Resurrected", "Save"),
)


@dataclass(frozen=True)
class SaveLocation:
    path: Path
    kind: str
    appid: str | None
    file_count: int
    newest_mtime: float
    user_name: str | None = None

    def rank(self) -> tuple:
        has_files = 1 if self.file_count else 0
        known_app = 1 if self.appid == STEAM_APPID else 0
        steamuser = 1 if (self.user_name or "").lower() == "steamuser" else 0
        return (has_files, self.newest_mtime, known_app, steamuser)


def detect_save_folders(
    *,
    platform: str | None = None,
    home: Path | None = None,
    userprofile: Path | None = None,
    environ: dict | None = None,
    steam_roots: list[Path] | None = None,
    scan_removable: bool = True,
    media_root: Path | None = None,
) -> list[SaveLocation]:
    """Return every existing save folder, best candidate first."""
    platform = platform if platform is not None else sys.platform
    env = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    found: list[SaveLocation] = []

    if platform.startswith("win"):
        profile = userprofile
        if profile is None:
            raw = env.get("USERPROFILE") or str(home)
            profile = Path(raw)
        found.extend(_windows_locations(Path(profile)))
    else:
        roots = steam_roots if steam_roots is not None else default_steam_roots(home, env)
        if scan_removable:
            roots = list(roots) + removable_library_roots(media_root)
        seen: set[str] = set()
        for root in roots:
            key = os.path.normcase(str(Path(root)))
            if key in seen:
                continue
            seen.add(key)
            try:
                found.extend(_proton_locations(Path(root)))
            except OSError:
                continue

    unique: dict[str, SaveLocation] = {}
    for location in found:
        try:
            key = os.path.normcase(str(location.path.resolve()))
        except OSError:
            key = os.path.normcase(str(location.path))
        current = unique.get(key)
        if current is None or location.rank() > current.rank():
            unique[key] = location
    return sorted(unique.values(), key=lambda item: item.rank(), reverse=True)


def choose_save_folder(locations: list[SaveLocation], override: Path | None = None) -> Path:
    if override is not None:
        path = Path(override)
        if not _safe_is_dir(path):
            raise FileNotFoundError(f"save path does not exist: {path}")
        return path
    if not locations:
        raise FileNotFoundError(
            "could not find a Diablo II Resurrected save folder. "
            "Set save_path in the config file."
        )
    return locations[0].path


def default_steam_roots(home: Path, environ: dict | None = None) -> list[Path]:
    env = os.environ if environ is None else environ
    candidates = [
        home / ".local" / "share" / "Steam",
        home / ".steam" / "steam",
        home / ".steam" / "root",
        home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
    ]
    xdg = env.get("XDG_DATA_HOME")
    if xdg:
        candidates.append(Path(xdg) / "Steam")
    roots: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        if not _safe_is_dir(path):
            return
        key = _path_key(path)
        if key in seen:
            return
        seen.add(key)
        roots.append(path)

    for candidate in candidates:
        add(candidate)
        for library in libraries_from_steam_root(candidate):
            add(library)
    return roots


def removable_library_roots(media: Path | None = None) -> list[Path]:
    """SD card and other Steam libraries mounted by SteamOS.

    ``media`` defaults to ``/run/media``. An unreadable mount is skipped.
    """
    root = Path("/run/media") if media is None else Path(media)
    if not _safe_is_dir(root):
        return []
    mounts: list[Path] = [root / "mmcblk0p1"]
    deck = root / "deck"
    if _safe_is_dir(deck):
        mounts.extend(path for path in _safe_iterdir(deck) if _safe_is_dir(path))
    mounts.extend(path for path in _safe_iterdir(root) if path.name != "deck" and _safe_is_dir(path))
    roots: list[Path] = []
    seen: set[str] = set()
    for mount in mounts:
        for candidate in (mount, mount / "SteamLibrary", mount / "steamapps"):
            key = os.path.normcase(str(candidate))
            if key in seen:
                continue
            if _compatdata_dir(candidate) is None:
                continue
            seen.add(key)
            roots.append(candidate)
    return roots


def libraries_from_steam_root(steam_root: Path) -> list[Path]:
    paths: list[Path] = []
    for relative in (
        Path("steamapps") / "libraryfolders.vdf",
        Path("config") / "libraryfolders.vdf",
    ):
        vdf = steam_root / relative
        try:
            readable = vdf.is_file()
        except OSError:
            continue
        if readable:
            try:
                text = vdf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for raw in parse_library_paths(text):
                paths.append(Path(raw))
    return paths


def parse_library_paths(text: str) -> list[str]:
    """Pull library root paths out of a Steam libraryfolders.vdf file."""
    found: list[str] = []
    for match in re.finditer(r'"path"\s+"([^"]*)"', text, flags=re.IGNORECASE):
        found.append(_unescape_vdf(match.group(1)))
    for match in re.finditer(r'"(\d+)"\s+"([^"]*)"', text):
        value = _unescape_vdf(match.group(2))
        if _looks_like_path(value):
            found.append(value)
    unique: list[str] = []
    seen: set[str] = set()
    for item in found:
        key = os.path.normcase(item)
        if key in seen or not item:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _windows_locations(profile: Path) -> list[SaveLocation]:
    locations = []
    for tail in _PROFILE_TAILS:
        path = _descend(profile, tail)
        if path is None or not _safe_is_dir(path):
            continue
        count, newest = _summarize(path)
        kind = "windows-documents" if tail[0].lower() == "documents" else "windows"
        locations.append(
            SaveLocation(path, kind, None, count, newest, profile.name),
        )
    return locations


def _proton_locations(root: Path) -> list[SaveLocation]:
    compat = _compatdata_dir(root)
    if compat is None:
        return []
    locations: list[SaveLocation] = []
    for prefix in _safe_iterdir(compat):
        try:
            if not _safe_is_dir(prefix):
                continue
            users = prefix / "pfx" / "drive_c" / "users"
            if not _safe_is_dir(users):
                # Case-insensitive fallback for unusual prefixes.
                drive = _descend(prefix, ("pfx", "drive_c", "users"))
                if drive is None:
                    continue
                users = drive
            for user_dir in _safe_iterdir(users):
                if not _safe_is_dir(user_dir):
                    continue
                for tail in _PROFILE_TAILS:
                    path = _descend(user_dir, tail)
                    if path is None or not _safe_is_dir(path):
                        continue
                    count, newest = _summarize(path)
                    kind = "proton-documents" if tail[0].lower() == "documents" else "proton"
                    locations.append(
                        SaveLocation(path, kind, prefix.name, count, newest, user_dir.name)
                    )
        except OSError:
            continue
    return locations


def _compatdata_dir(root: Path) -> Path | None:
    if not _safe_is_dir(root):
        return None
    nested = root / "steamapps" / "compatdata"
    if _safe_is_dir(nested):
        return nested
    if root.name.lower() == "steamapps" and _safe_is_dir(root / "compatdata"):
        return root / "compatdata"
    return None


def _descend(start: Path, parts: tuple[str, ...]) -> Path | None:
    current = start
    for part in parts:
        nxt = _child_ci(current, part)
        if nxt is None:
            return None
        current = nxt
    return current


def _child_ci(parent: Path, name: str) -> Path | None:
    if not _safe_is_dir(parent):
        return None
    direct = parent / name
    if _safe_exists(direct):
        return direct
    for entry in _safe_iterdir(parent):
        if entry.name.lower() == name.lower():
            return entry
    return None


def _summarize(path: Path) -> tuple[int, float]:
    count = 0
    newest = 0.0
    for child in _safe_iterdir(path):
        try:
            if not child.is_file() or child.is_symlink():
                continue
        except OSError:
            continue
        if not is_syncable_name(child.name):
            continue
        count += 1
        try:
            newest = max(newest, child.stat().st_mtime)
        except OSError:
            continue
    return count, newest


def _safe_is_dir(path: Path) -> bool:
    """False when the path is missing or this user cannot stat it."""
    try:
        return path.is_dir()
    except OSError:
        return False


def _safe_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _safe_iterdir(path: Path) -> list[Path]:
    try:
        return list(path.iterdir())
    except OSError:
        return []


def _path_key(path: Path) -> str:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    return os.path.normcase(str(resolved))


def _unescape_vdf(value: str) -> str:
    return value.replace("\\\\", "\\")


def _looks_like_path(value: str) -> bool:
    if value.startswith("/") or value.startswith("\\\\"):
        return True
    return len(value) >= 3 and value[1] == ":" and value[2] in "\\/"
