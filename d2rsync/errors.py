"""Errors the CLI turns into short messages."""


class D2RSyncError(Exception):
    """Base error for expected failures."""


class ConfigError(D2RSyncError):
    """Configuration is missing or invalid."""


class SavePathError(D2RSyncError):
    """The save folder could not be resolved."""


class AuthError(D2RSyncError):
    """The peer failed the shared-secret handshake."""


class ProtocolError(D2RSyncError):
    """The peer sent a message this version cannot handle."""


class GameRunningError(D2RSyncError):
    """The game process is running, so saves may be mid-write."""


class SyncIOError(D2RSyncError):
    """A file or network operation failed."""
