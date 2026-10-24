"""Shared helpers for save-sync tests."""

from __future__ import annotations

import os
from pathlib import Path

from d2rsync.hashing import sha256_bytes
from d2rsync.model import Entry


def entry(data: bytes, mtime: float) -> Entry:
    return Entry(sha256_bytes(data), len(data), mtime)


def write_file(root: Path, name: str, data: bytes, mtime: float | None = None) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path
