"""Timestamped copies of any save file before it is overwritten or deleted."""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from d2rsync.names import validate_rel

BACKUP_DIRNAME = ".d2r-sync-backups"


def _resolved(path: Path) -> Path | None:
    try:
        return path.resolve()
    except OSError:
        return None


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _remove_tree(path: Path) -> bool:
    try:
        shutil.rmtree(path)
    except OSError:
        return False
    return True


def sanitize_label(label: str) -> str:
    cleaned = []
    for char in label:
        if char.isalnum() or char in "._-":
            cleaned.append(char)
        else:
            cleaned.append("-")
    text = "".join(cleaned).strip("-")
    return text[:40] or "peer"


class BackupManager:
    """Group backups from one sync into a single timestamped folder."""

    def __init__(
        self,
        save_root: Path,
        *,
        retention_count: int = 20,
        retention_days: int = 30,
        clock=None,
    ):
        if retention_count < 1:
            raise ValueError("retention_count must be at least 1")
        if retention_days < 1:
            raise ValueError("retention_days must be at least 1")
        self.save_root = Path(save_root)
        self.base = self.save_root / BACKUP_DIRNAME
        self.retention_count = retention_count
        self.retention_days = retention_days
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._snapshot: Path | None = None
        self._conflict_dirs: dict[str, Path] = {}

    def backup(self, source: Path, rel: str) -> Path:
        rel = validate_rel(rel)
        if self._snapshot is None:
            self._snapshot = self._unique_dir(self.base / "snapshots", self._stamp())
        dest = self._snapshot / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        return dest

    def archive_conflict(self, rel: str, data: bytes, label: str) -> Path:
        rel = validate_rel(rel)
        label = sanitize_label(label)
        folder = self._conflict_dirs.get(label)
        if folder is None:
            folder = self._unique_dir(self.base / "conflicts", f"{self._stamp()}-{label}")
            self._conflict_dirs[label] = folder
        dest = folder / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest

    def prune(self) -> list[Path]:
        """Drop old snapshot and conflict folders. Returns paths removed."""
        removed: list[Path] = []
        protected = set()
        if self._snapshot is not None:
            resolved = _resolved(self._snapshot)
            if resolved is not None:
                protected.add(resolved)
        for folder in self._conflict_dirs.values():
            resolved = _resolved(folder)
            if resolved is not None:
                protected.add(resolved)
        for category in ("snapshots", "conflicts"):
            removed.extend(self._prune_category(self.base / category, protected))
        return removed

    def _prune_category(self, category: Path, protected: set[Path]) -> list[Path]:
        try:
            if not category.is_dir():
                return []
            names = list(category.iterdir())
        except OSError:
            return []
        folders: list[Path] = []
        for path in names:
            try:
                if path.is_dir():
                    folders.append(path)
            except OSError:
                continue
        now = self._clock().timestamp()
        max_age = self.retention_days * 86400
        removed: list[Path] = []
        kept: list[Path] = []
        for folder in folders:
            resolved = _resolved(folder)
            if resolved is None:
                kept.append(folder)
                continue
            if resolved in protected:
                kept.append(folder)
                continue
            try:
                age = now - folder.stat().st_mtime
            except OSError:
                kept.append(folder)
                continue
            if age > max_age:
                if _remove_tree(folder):
                    removed.append(folder)
                else:
                    kept.append(folder)
            else:
                kept.append(folder)
        kept.sort(key=lambda path: _mtime(path), reverse=True)
        # Protected folders always stay, then the newest others up to the count.
        overflow = kept[self.retention_count :]
        for folder in overflow:
            resolved = _resolved(folder)
            if resolved is None or resolved in protected:
                continue
            if _remove_tree(folder):
                removed.append(folder)
        return removed

    def _stamp(self) -> str:
        return self._clock().strftime("%Y%m%dT%H%M%SZ")

    def _unique_dir(self, parent: Path, name: str) -> Path:
        parent.mkdir(parents=True, exist_ok=True)
        candidate = parent / name
        suffix = 0
        while candidate.exists():
            suffix += 1
            candidate = parent / f"{name}-{suffix}"
        candidate.mkdir(parents=True)
        # Make the directory mtime match the injected clock so tests can age it.
        stamped = self._clock().timestamp()
        os.utime(candidate, (stamped, stamped))
        return candidate
