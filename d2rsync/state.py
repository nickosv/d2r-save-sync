"""Remember the hash each file had after the last successful sync."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from d2rsync.errors import SyncIOError
from d2rsync.names import is_syncable_name


def save_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


class SyncState:
    def __init__(
        self,
        path: Path,
        folder_key: str,
        files: dict[str, str] | None = None,
        synced_at: float | None = None,
    ):
        self.path = Path(path)
        self.folder_key = folder_key
        self.files: dict[str, str] = dict(files or {})
        self.synced_at = synced_at

    def hash_for(self, rel: str) -> str | None:
        return self.files.get(rel)

    def set_hash(self, rel: str, digest: str | None) -> None:
        if digest is None:
            self.files.pop(rel, None)
        else:
            self.files[rel] = digest

    def as_dict(self) -> dict[str, str]:
        return dict(self.files)

    def mark_synced(self, when: float | None = None) -> None:
        self.synced_at = time.time() if when is None else when
        self.save()

    def save(self) -> None:
        payload = _read_document(self.path)
        folders = payload.setdefault("folders", {})
        if self.files or self.synced_at is not None:
            folder = {"files": self.files}
            if self.synced_at is not None:
                folder["synced_at"] = self.synced_at
            folders[self.folder_key] = folder
        else:
            folders.pop(self.folder_key, None)
        _atomic_write_json(self.path, payload)


def load_state(path: Path, save_root: Path) -> SyncState:
    key = save_key(save_root)
    document = _read_document(path)
    folder = document.get("folders", {}).get(key, {})
    raw = folder.get("files", {}) if isinstance(folder, dict) else {}
    files: dict[str, str] = {}
    synced_at = None
    if isinstance(folder, dict):
        raw_time = folder.get("synced_at")
        if isinstance(raw_time, (int, float)):
            synced_at = float(raw_time)
    if isinstance(raw, dict):
        for name, digest in raw.items():
            if isinstance(name, str) and isinstance(digest, str) and is_syncable_name(name):
                files[name] = digest
    return SyncState(path, key, files, synced_at)


def _read_document(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "folders": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SyncIOError(f"could not read sync state {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise SyncIOError(f"sync state {path} is not version 1")
    if "folders" not in data or not isinstance(data["folders"], dict):
        data["folders"] = {}
    return data


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(blob)
    if os.name != "nt":
        os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    if os.name != "nt":
        os.chmod(path, 0o600)
