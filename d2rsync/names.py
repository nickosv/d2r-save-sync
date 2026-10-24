"""Which files in a D2R save folder are worth syncing.

Offline characters are a `.d2s` plus sidecar files the game writes beside
them (keymap, controller layout, automap). Online characters live on
Battle.net. The local folder only keeps their keymap and controller files,
named `.keyo` and `.ctlo`, and those must never be copied. A character name
on the exclude list is left alone on both machines as well.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from d2rsync.d2s import d2s_is_online, parse_d2s

# Extensions seen in real Saved Games folders. Matching is case-insensitive.
# `.keyo` and `.ctlo` are online sidecars and are intentionally absent.
_CHARACTER_SUFFIXES = (
    ".d2s",
    ".key",
    ".ctl",
    ".map",
)
_ONLINE_SUFFIXES = (".keyo", ".ctlo")
_STEM_SUFFIXES = _CHARACTER_SUFFIXES + _ONLINE_SUFFIXES
_MA_RE = re.compile(r"\.ma\d+$", re.IGNORECASE)
_MA_STEM = re.compile(r"^(?P<stem>.+)\.ma\d+$", re.IGNORECASE)
_STASH_RE = re.compile(r"^sharedstash.*\.d2i$", re.IGNORECASE)
_TEMP_SUFFIXES = (".d2rsync-tmp", ".tmp", ".temp")
MAX_CHARACTER_NAMES = 200
MAX_CHARACTER_NAME_LENGTH = 64


def is_syncable_name(name: str) -> bool:
    """Return True if `name` is a single save-file name we should sync."""
    if is_online_sidecar(name):
        return False
    return _known_save_name(name) and not name.lower().endswith(_ONLINE_SUFFIXES)


def is_online_sidecar(name: str) -> bool:
    """True for the local keymap/controller files an online character leaves behind."""
    if not _flat_name(name):
        return False
    lower = name.lower()
    return any(lower.endswith(suffix) and len(name) > len(suffix) for suffix in _ONLINE_SUFFIXES)


def is_listed_save_name(name: str) -> bool:
    """True for offline saves and for online sidecars the character list should show."""
    return is_syncable_name(name) or is_online_sidecar(name)


def character_stem(filename: str) -> str | None:
    """Return the character name for a save file, or None for a shared stash."""
    if not _flat_name(filename):
        return None
    lower = filename.lower()
    if _STASH_RE.match(filename):
        return None
    for suffix in _STEM_SUFFIXES:
        if lower.endswith(suffix) and len(filename) > len(suffix):
            return filename[: -len(suffix)]
    match = _MA_STEM.match(filename)
    if match and match.group("stem"):
        return match.group("stem")
    return None


def validate_rel(rel: str) -> str:
    """Return a flat save-file name, or raise ValueError."""
    if not isinstance(rel, str):
        raise ValueError("path must be a file name")
    if rel != rel.strip():
        raise ValueError("path must not have surrounding whitespace")
    if is_syncable_name(rel):
        return rel
    raise ValueError(f"not a Diablo II Resurrected save file: {rel}")


def list_save_filenames(root: Path) -> list[str]:
    """Names in `root` that belong to a character or shared stash, including online sidecars."""
    try:
        if not root.is_dir():
            return []
        children = list(root.iterdir())
    except OSError:
        return []
    names: list[str] = []
    for child in children:
        try:
            if not child.is_file() or child.is_symlink():
                continue
        except OSError:
            continue
        if is_listed_save_name(child.name):
            names.append(child.name)
    return names


def load_d2s_bytes(root: Path, filenames: list[str] | tuple[str, ...]) -> dict[str, bytes]:
    """Read `.d2s` bytes for header checks. Unreadable files are omitted."""
    found: dict[str, bytes] = {}
    for name in filenames:
        if not name.lower().endswith(".d2s"):
            continue
        path = root / name
        try:
            if not path.is_file() or path.is_symlink():
                continue
            found[name] = path.read_bytes()
        except OSError:
            continue
    return found


def online_character_names(
    filenames: list[str] | tuple[str, ...],
    d2s_bytes: dict[str, bytes] | None = None,
) -> list[str]:
    """Display names of Battle.net characters among `filenames`.

    A name is online when its files include a `.keyo` or `.ctlo` and no
    `.d2s`, or when a `.d2s` header positively says the character is online.
    A `.d2s` beside a `.keyo` is an offline character; only the online
    sidecar is skipped, and that happens in `omit_blocked`.
    """
    blobs = d2s_bytes or {}
    stems = _stem_spellings(filenames)
    sidecar_stems: set[str] = set()
    d2s_by_stem: dict[str, str] = {}
    for name in filenames:
        stem = character_stem(name)
        if stem is None:
            continue
        key = stem.casefold()
        if is_online_sidecar(name):
            sidecar_stems.add(key)
        if name.lower().endswith(".d2s"):
            d2s_by_stem[key] = name
    online: list[str] = []
    for key, spelling in stems.items():
        header_online = False
        d2s_name = d2s_by_stem.get(key)
        if d2s_name is not None and d2s_is_online(blobs.get(d2s_name, b"")):
            header_online = True
        if header_online or (key in sidecar_stems and d2s_name is None):
            online.append(spelling)
    return online


def block_list(*groups: list[str] | tuple[str, ...]) -> list[str]:
    """Merge character names, keeping the first spelling of each."""
    seen: set[str] = set()
    merged: list[str] = []
    for group in groups:
        for name in group:
            if not isinstance(name, str):
                continue
            text = name.strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(text)
            if len(merged) >= MAX_CHARACTER_NAMES:
                return merged
    return merged


def protocol_names(value) -> list[str]:
    """Character names from a peer message. Junk entries are ignored."""
    if not isinstance(value, list):
        return []
    accepted: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not _acceptable_character_name(text):
            continue
        accepted.append(text)
        if len(accepted) >= MAX_CHARACTER_NAMES:
            break
    return block_list(accepted)


def omit_blocked(files: dict, state: dict, blocked_names: list[str] | tuple[str, ...]) -> tuple[dict, dict]:
    """Drop online sidecars and every file belonging to a blocked character."""
    stems = {name.casefold() for name in blocked_names}

    def blocked(name: str) -> bool:
        if is_online_sidecar(name):
            return True
        stem = character_stem(name)
        return stem is not None and stem.casefold() in stems

    return (
        {key: value for key, value in files.items() if not blocked(key)},
        {key: value for key, value in state.items() if not blocked(key)},
    )


def name_is_blocked(filename: str, blocked_names: list[str] | tuple[str, ...] | set[str]) -> bool:
    if is_online_sidecar(filename):
        return True
    stem = character_stem(filename)
    if stem is None:
        return False
    stems = {name.casefold() for name in blocked_names}
    return stem.casefold() in stems


@dataclass(frozen=True)
class CharacterChoice:
    name: str
    online: bool
    subtitle: str


def characters_in_folder(root: Path | None) -> list[CharacterChoice]:
    """One row per character in a save folder, including online characters."""
    if root is None:
        return []
    filenames = list_save_filenames(root)
    blobs = load_d2s_bytes(root, filenames)
    online = {name.casefold() for name in online_character_names(filenames, blobs)}
    grouped: dict[str, list[str]] = {}
    for name in filenames:
        stem = character_stem(name)
        if stem is None:
            continue
        grouped.setdefault(stem.casefold(), []).append(name)
    spellings = _stem_spellings(filenames)
    choices: list[CharacterChoice] = []
    for key, spelling in spellings.items():
        names = grouped.get(key, [])
        info = None
        for name in names:
            data = blobs.get(name)
            if data:
                info = parse_d2s(data)
                if info:
                    break
        is_online = key in online
        if is_online:
            subtitle = "Online"
        elif info:
            subtitle = f"{info.class_name} · {info.level}"
        else:
            subtitle = "Offline"
        choices.append(CharacterChoice(spelling, is_online, subtitle))
    choices.sort(key=lambda item: (item.name.casefold()))
    return choices


def _stem_spellings(filenames) -> dict[str, str]:
    """casefold -> display stem. A `.d2s` spelling wins over a sidecar spelling."""
    found: dict[str, str] = {}
    for name in sorted(filenames):
        stem = character_stem(name)
        if stem is None:
            continue
        key = stem.casefold()
        if key not in found or name.lower().endswith(".d2s"):
            found[key] = stem
    return found


def _known_save_name(name: str) -> bool:
    if not _flat_name(name):
        return False
    lower = name.lower()
    if lower.endswith(_TEMP_SUFFIXES) or lower.endswith(".bak"):
        return False
    if _STASH_RE.match(name):
        return True
    for suffix in _CHARACTER_SUFFIXES + _ONLINE_SUFFIXES:
        if lower.endswith(suffix) and len(name) > len(suffix):
            return True
    if _MA_RE.search(name) and len(name) > 4:
        return True
    return False


def _flat_name(name: str) -> bool:
    if not name or "/" in name or "\\" in name:
        return False
    if name.startswith("."):
        return False
    return True


def _acceptable_character_name(text: str) -> bool:
    if not text or text != text.strip():
        return False
    if len(text) > MAX_CHARACTER_NAME_LENGTH:
        return False
    if "/" in text or "\\" in text or text.startswith("."):
        return False
    return True
