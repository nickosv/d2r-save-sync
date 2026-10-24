"""Detect whether Diablo II: Resurrected looks like it is running.

A positive match refuses the sync, because the game keeps character files
open and can be mid-write. Unknown means the process list could not be read;
the caller warns and continues rather than blocking every sync.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

MARKERS = ("d2r.exe", "diablo ii resurrected")


def game_is_running(names: list[str] | None = None) -> bool | None:
    """True, False, or None when the process list could not be read."""
    if names is None:
        names = list_process_names()
    if names is None:
        return None
    for name in names:
        lowered = name.lower()
        if any(marker in lowered for marker in MARKERS):
            return True
    return False


def list_process_names() -> list[str] | None:
    if os.name == "nt":
        return _windows_processes()
    return _posix_processes()


def _windows_processes() -> list[str] | None:
    try:
        completed = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    names: list[str] = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line.startswith('"'):
            continue
        names.append(line.split('"', 2)[1])
    return names


def _posix_processes() -> list[str] | None:
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    names: list[str] = []
    try:
        entries = list(proc.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            try:
                comm = (entry / "comm").read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            names.append(comm.strip())
            continue
        names.append(raw.replace(b"\x00", b" ").decode("utf-8", "replace"))
    return names
