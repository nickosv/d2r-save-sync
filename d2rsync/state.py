"""Remember the hash each file had after the last successful sync."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from d2rsync.errors import SyncIOError
from d2rsync.names import is_syncable_name


def save_key(path: Path) -> str:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    return os.path.normcase(str(resolved))


class SyncState:
    def __init__(
        self,
        path: Path,
        folder_key: str,
        files: dict[str, str] | None = None,
        synced_at: float | None = None,
        peer: str = "",
        peer_files: dict | None = None,
    ):
        self.path = Path(path)
        self.folder_key = folder_key
        self.files: dict[str, str] = dict(files or {})
        self.synced_at = synced_at
        self.peer = peer or ""
        self.peer_files: dict = dict(peer_files or {})

    def hash_for(self, rel: str) -> str | None:
        return self.files.get(rel)

    def set_hash(self, rel: str, digest: str | None) -> None:
        if digest is None:
            self.files.pop(rel, None)
        else:
            self.files[rel] = digest

    def as_dict(self) -> dict[str, str]:
        return dict(self.files)

    def mark_synced(
        self,
        when: float | None = None,
        *,
        peer: str | None = None,
        peer_files: dict | None = None,
    ) -> None:
        """Record a finished sync. Omitted peer data keeps the previous value."""
        self.synced_at = time.time() if when is None else when
        if peer:
            self.peer = peer
        if peer_files is not None:
            self.peer_files = _encode_peer_files(peer_files)
        self.save()

    def peer_entries(self) -> dict:
        """Files the other machine reported at the last sync, keyed by name."""
        from d2rsync.model import Entry

        found = {}
        for name, payload in self.peer_files.items():
            if not isinstance(payload, dict):
                continue
            try:
                found[str(name)] = Entry.from_json(payload)
            except (KeyError, TypeError, ValueError):
                continue
        return found

    def save(self) -> None:
        payload = _read_document(self.path)
        folders = payload.setdefault("folders", {})
        if self.files or self.synced_at is not None or self.peer or self.peer_files:
            folder = {"files": self.files}
            if self.synced_at is not None:
                folder["synced_at"] = self.synced_at
            if self.peer:
                folder["peer"] = self.peer
            if self.peer_files:
                folder["peer_files"] = self.peer_files
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
    peer = ""
    peer_files: dict = {}
    if isinstance(folder, dict):
        raw_time = folder.get("synced_at")
        if isinstance(raw_time, (int, float)):
            synced_at = float(raw_time)
        raw_peer = folder.get("peer")
        if isinstance(raw_peer, str):
            peer = raw_peer
        raw_peer_files = folder.get("peer_files")
        if isinstance(raw_peer_files, dict):
            for name, payload in raw_peer_files.items():
                if isinstance(name, str) and isinstance(payload, dict) and is_syncable_name(name):
                    peer_files[name] = payload
    if isinstance(raw, dict):
        for name, digest in raw.items():
            if isinstance(name, str) and isinstance(digest, str) and is_syncable_name(name):
                files[name] = digest
    return SyncState(path, key, files, synced_at, peer, peer_files)


def _encode_peer_files(peer_files: dict) -> dict:
    encoded = {}
    for name, entry in peer_files.items():
        if not isinstance(name, str) or not is_syncable_name(name):
            continue
        if hasattr(entry, "to_json"):
            encoded[name] = entry.to_json()
            continue
        if not isinstance(entry, dict):
            continue
        try:
            encoded[name] = {
                "sha256": str(entry["sha256"]),
                "size": int(entry["size"]),
                "mtime": float(entry["mtime"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return encoded


def _read_document(path: Path) -> dict:
    try:
        exists = path.exists()
    except OSError as exc:
        raise SyncIOError(f"could not read sync state {path}: {exc}") from exc
    if not exists:
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
