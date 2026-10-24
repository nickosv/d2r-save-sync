"""Read class and level from a Diablo II / Resurrected .d2s header.

D2R still uses the classic header: signature, then the name at byte 20,
class at byte 40, and level at byte 43.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SIGNATURE = 0xAA55AA55

CLASS_NAMES = (
    "Amazon",
    "Sorceress",
    "Necromancer",
    "Paladin",
    "Barbarian",
    "Druid",
    "Assassin",
)


@dataclass(frozen=True)
class CharacterInfo:
    name: str
    class_name: str
    level: int


def parse_d2s(data: bytes) -> CharacterInfo | None:
    if len(data) < 44:
        return None
    signature = int.from_bytes(data[0:4], "little")
    if signature != SIGNATURE:
        return None
    raw_name = data[20:36].split(b"\x00", 1)[0]
    try:
        name = raw_name.decode("latin-1").strip()
    except UnicodeDecodeError:
        name = ""
    class_id = data[40]
    if class_id >= len(CLASS_NAMES):
        return None
    level = data[43]
    if level < 1 or level > 99:
        return None
    return CharacterInfo(name=name, class_name=CLASS_NAMES[class_id], level=level)


def read_local_d2s(root: Path) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    if not root.is_dir():
        return found
    try:
        children = list(root.iterdir())
    except OSError:
        return found
    for child in children:
        if not child.is_file() or child.is_symlink():
            continue
        if child.suffix.lower() != ".d2s":
            continue
        try:
            found[child.name] = child.read_bytes()
        except OSError:
            continue
    return found
