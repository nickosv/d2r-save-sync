"""JSON config shared by the Windows PC and the Steam Deck."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from d2rsync.errors import ConfigError
from d2rsync.plan import POLICIES

DEFAULT_PORT = 41943
DEFAULT_DISCOVER_PORT = 41942
MIN_SECRET_LENGTH = 8


@dataclass
class Config:
    save_path: str | None = None
    peer: str | None = None
    port: int = DEFAULT_PORT
    discover_port: int = DEFAULT_DISCOVER_PORT
    secret: str | None = None
    conflict: str = "keep-both"
    backup_retention_count: int = 20
    backup_retention_days: int = 30
    refuse_if_game_running: bool = True
    listen_host: str = "0.0.0.0"
    name: str | None = None
    role: str = "client"
    peer_mode: str = "auto"
    path: Path | None = None

    def secret_value(self) -> str:
        env = os.environ.get("D2R_SYNC_SECRET")
        if env:
            _check_secret(env)
            return env
        if self.secret:
            _check_secret(self.secret)
            return self.secret
        raise ConfigError(
            "no shared secret set. Run `d2rsync init` on one machine and copy "
            "the secret into the other machine's config, or set D2R_SYNC_SECRET."
        )

    def fingerprint(self) -> str | None:
        try:
            secret = self.secret_value()
        except ConfigError:
            return None
        import hashlib

        return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:8]


def config_dir() -> Path:
    override = os.environ.get("D2R_SYNC_CONFIG_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "d2r-save-sync"
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(xdg) / "d2r-save-sync"


def config_path() -> Path:
    return config_dir() / "config.json"


def state_path() -> Path:
    return config_dir() / "state.json"


def load_config(path: Path | None = None) -> Config:
    file_path = Path(path) if path is not None else config_path()
    if not file_path.exists():
        cfg = Config(path=file_path)
        return cfg
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"could not read {file_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{file_path} must contain a JSON object")
    return _from_dict(raw, file_path)


def write_config(cfg: Config, path: Path | None = None) -> Path:
    file_path = Path(path) if path is not None else (cfg.path or config_path())
    payload = {
        "save_path": cfg.save_path,
        "peer": cfg.peer,
        "port": cfg.port,
        "discover_port": cfg.discover_port,
        "secret": cfg.secret,
        "conflict": cfg.conflict,
        "backup_retention_count": cfg.backup_retention_count,
        "backup_retention_days": cfg.backup_retention_days,
        "refuse_if_game_running": cfg.refuse_if_game_running,
        "listen_host": cfg.listen_host,
        "name": cfg.name,
        "role": cfg.role,
        "peer_mode": cfg.peer_mode,
    }
    file_path.parent.mkdir(parents=True, exist_ok=True)
    blob = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    temporary = file_path.with_suffix(file_path.suffix + ".tmp")
    temporary.write_bytes(blob)
    if os.name != "nt":
        os.chmod(temporary, 0o600)
    os.replace(temporary, file_path)
    if os.name != "nt":
        os.chmod(file_path, 0o600)
    cfg.path = file_path
    return file_path


def init_config(path: Path | None = None, *, secret: str | None = None, force: bool = False) -> tuple[Config, bool]:
    """Create a config if missing. Returns (config, created)."""
    file_path = Path(path) if path is not None else config_path()
    if file_path.exists() and not force:
        return load_config(file_path), False
    chosen = secret or secrets.token_urlsafe(24)
    _check_secret(chosen)
    cfg = Config(secret=chosen, path=file_path)
    write_config(cfg, file_path)
    return cfg, True


def apply_overrides(
    cfg: Config,
    *,
    save_path: str | None = None,
    peer: str | None = None,
    port: int | None = None,
    discover_port: int | None = None,
    secret: str | None = None,
    conflict: str | None = None,
    name: str | None = None,
) -> Config:
    if save_path:
        cfg.save_path = save_path
    if peer:
        cfg.peer = peer
    if port is not None:
        cfg.port = _port(port, "port")
    if discover_port is not None:
        cfg.discover_port = _port(discover_port, "discover_port")
    if secret:
        _check_secret(secret)
        cfg.secret = secret
    if conflict:
        if conflict not in POLICIES:
            raise ConfigError(f"conflict must be one of: {', '.join(POLICIES)}")
        cfg.conflict = conflict
    if name:
        cfg.name = name
    return cfg


def parse_peer(value: str, default_port: int) -> tuple[str, int]:
    text = value.strip()
    if not text:
        raise ConfigError("peer address is empty")
    if text.startswith("["):
        raise ConfigError("IPv6 peers are not supported; use an IPv4 address")
    if ":" in text:
        host, _, port_text = text.rpartition(":")
        if not host:
            raise ConfigError(f"could not parse peer address: {value}")
        return host, _port(int(port_text), "peer port")
    return text, default_port


def _from_dict(raw: dict, path: Path) -> Config:
    cfg = Config(path=path)
    cfg.save_path = _optional_str(raw.get("save_path"))
    cfg.peer = _optional_str(raw.get("peer"))
    cfg.name = _optional_str(raw.get("name"))
    cfg.secret = _optional_str(raw.get("secret"))
    if cfg.secret:
        _check_secret(cfg.secret)
    if "port" in raw and raw["port"] is not None:
        cfg.port = _port(int(raw["port"]), "port")
    if "discover_port" in raw and raw["discover_port"] is not None:
        cfg.discover_port = _port(int(raw["discover_port"]), "discover_port")
    if raw.get("conflict"):
        conflict = str(raw["conflict"])
        if conflict not in POLICIES:
            raise ConfigError(f"conflict must be one of: {', '.join(POLICIES)}")
        cfg.conflict = conflict
    if "backup_retention_count" in raw and raw["backup_retention_count"] is not None:
        cfg.backup_retention_count = _positive(int(raw["backup_retention_count"]), "backup_retention_count")
    if "backup_retention_days" in raw and raw["backup_retention_days"] is not None:
        cfg.backup_retention_days = _positive(int(raw["backup_retention_days"]), "backup_retention_days")
    if "refuse_if_game_running" in raw and raw["refuse_if_game_running"] is not None:
        cfg.refuse_if_game_running = bool(raw["refuse_if_game_running"])
    if raw.get("listen_host"):
        cfg.listen_host = str(raw["listen_host"])
    role = _optional_str(raw.get("role")) or "client"
    if role not in ("client", "host"):
        raise ConfigError("role must be client or host")
    cfg.role = role
    peer_mode = _optional_str(raw.get("peer_mode")) or "auto"
    if peer_mode not in ("auto", "manual"):
        raise ConfigError("peer_mode must be auto or manual")
    cfg.peer_mode = peer_mode
    return cfg


def _optional_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _port(value: int, label: str) -> int:
    if not 1 <= int(value) <= 65535:
        raise ConfigError(f"{label} must be between 1 and 65535")
    return int(value)


def _positive(value: int, label: str) -> int:
    if int(value) < 1:
        raise ConfigError(f"{label} must be at least 1")
    return int(value)


def _check_secret(secret: str) -> None:
    if len(secret) < MIN_SECRET_LENGTH:
        raise ConfigError(f"shared secret must be at least {MIN_SECRET_LENGTH} characters")
    if "\n" in secret or "\r" in secret:
        raise ConfigError("shared secret must be a single line")
