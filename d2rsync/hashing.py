"""Content hashes for save files. Saves are small, so the whole file is hashed."""

from __future__ import annotations

import hashlib


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
