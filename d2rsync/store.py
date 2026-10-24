"""Local save folder: list, read, and write files, backing up first."""

from __future__ import annotations

import os
from pathlib import Path

from d2rsync.backup import BackupManager
from d2rsync.errors import SyncIOError
from d2rsync.hashing import sha256_bytes
from d2rsync.model import Entry
from d2rsync.names import is_syncable_name, validate_rel

MAX_FILE_BYTES = 32 * 1024 * 1024


class DirectorySave:
    def __init__(self, root: Path, backups: BackupManager):
        self.root = Path(root)
        self.backups = backups
        if not self.root.is_dir():
            raise SyncIOError(f"save folder does not exist: {self.root}")

    def inventory(self) -> dict[str, Entry]:
        found: dict[str, Entry] = {}
        try:
            children = list(self.root.iterdir())
        except OSError as exc:
            raise SyncIOError(f"could not list {self.root}: {exc}") from exc
        for child in children:
            if not child.is_file() or child.is_symlink():
                continue
            if not is_syncable_name(child.name):
                continue
            try:
                stat = child.stat()
            except OSError as exc:
                raise SyncIOError(f"could not stat {child.name}: {exc}") from exc
            if stat.st_size > MAX_FILE_BYTES:
                raise SyncIOError(f"{child.name} is larger than {MAX_FILE_BYTES} bytes")
            digest = sha256_bytes(child.read_bytes())
            found[child.name] = Entry(digest, stat.st_size, stat.st_mtime)
        return found

    def read_bytes(self, rel: str) -> bytes:
        path = self._resolve(rel)
        if not path.is_file() or path.is_symlink():
            raise SyncIOError(f"missing save file: {rel}")
        data = path.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise SyncIOError(f"{rel} is larger than {MAX_FILE_BYTES} bytes")
        return data

    def write_bytes(self, rel: str, data: bytes, mtime: float) -> None:
        if len(data) > MAX_FILE_BYTES:
            raise SyncIOError(f"{rel} is larger than {MAX_FILE_BYTES} bytes")
        path = self._resolve(rel)
        if path.is_symlink():
            raise SyncIOError(f"refusing to follow symlink: {rel}")
        if path.exists() and not path.is_file():
            raise SyncIOError(f"refusing to replace non-file: {rel}")
        if path.is_file():
            self.backups.backup(path, rel)
        temporary = path.with_name(path.name + ".d2rsync-tmp")
        temporary.write_bytes(data)
        os.utime(temporary, (mtime, mtime))
        os.replace(temporary, path)

    def remove(self, rel: str) -> None:
        path = self._resolve(rel)
        if path.is_symlink():
            raise SyncIOError(f"refusing to follow symlink: {rel}")
        if not path.exists():
            return
        if not path.is_file():
            raise SyncIOError(f"refusing to delete non-file: {rel}")
        self.backups.backup(path, rel)
        path.unlink()

    def archive_conflict(self, rel: str, data: bytes, label: str) -> str:
        dest = self.backups.archive_conflict(rel, data, label)
        return str(dest)

    def _resolve(self, rel: str) -> Path:
        name = validate_rel(rel)
        root = self.root.resolve()
        dest = root / name
        if dest.is_symlink():
            raise SyncIOError(f"refusing to follow symlink: {rel}")
        resolved = dest.resolve()
        if resolved.parent != root:
            raise SyncIOError(f"path escapes the save folder: {rel}")
        return dest
