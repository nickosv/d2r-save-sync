"""Which files in a D2R save folder are worth syncing.

Offline characters are a `.d2s` plus sidecar files the game writes beside
them (keymap, controller layout, automap). Online characters only have
local keymap/controller files; the character itself lives on Battle.net.
Shared stash tabs are `SharedStash*.d2i`.
"""

from __future__ import annotations

import re

# Extensions seen in real Saved Games folders. Matching is case-insensitive.
_CHARACTER_SUFFIXES = (
    ".d2s",
    ".key",
    ".keyo",
    ".ctl",
    ".ctlo",
    ".map",
)
_MA_RE = re.compile(r"\.ma\d+$", re.IGNORECASE)
_STASH_RE = re.compile(r"^sharedstash.*\.d2i$", re.IGNORECASE)
_TEMP_SUFFIXES = (".d2rsync-tmp", ".tmp", ".temp")


def is_syncable_name(name: str) -> bool:
    """Return True if `name` is a single save-file name we should sync."""
    if not name or "/" in name or "\\" in name:
        return False
    if name.startswith("."):
        return False
    lower = name.lower()
    if lower.endswith(_TEMP_SUFFIXES) or lower.endswith(".bak"):
        return False
    if _STASH_RE.match(name):
        return True
    for suffix in _CHARACTER_SUFFIXES:
        if lower.endswith(suffix) and len(name) > len(suffix):
            return True
    if _MA_RE.search(name) and len(name) > 4:
        return True
    return False


def validate_rel(rel: str) -> str:
    """Return a flat save-file name, or raise ValueError."""
    if not isinstance(rel, str):
        raise ValueError("path must be a file name")
    if rel != rel.strip():
        raise ValueError("path must not have surrounding whitespace")
    if is_syncable_name(rel):
        return rel
    raise ValueError(f"not a Diablo II Resurrected save file: {rel}")
