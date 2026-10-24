"""Length-prefixed messages. After the handshake every frame is encrypted."""

from __future__ import annotations

import json
import socket
import struct

from d2rsync.crypto import open_seal, seal
from d2rsync.errors import AuthError, ProtocolError

PROTOCOL_VERSION = 1
MAX_FRAME = 34 * 1024 * 1024
MAX_JSON = 2 * 1024 * 1024


class Conn:
    def __init__(self, sock: socket.socket, enc_key: bytes | None = None, mac_key: bytes | None = None):
        self.sock = sock
        self.enc_key = enc_key
        self.mac_key = mac_key

    def enable_encryption(self, enc_key: bytes, mac_key: bytes) -> None:
        self.enc_key = enc_key
        self.mac_key = mac_key

    def send_msg(self, payload: dict) -> None:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_bytes(raw)

    def recv_msg(self) -> dict:
        raw = self.recv_bytes(MAX_JSON)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProtocolError("peer sent a message that is not JSON") from exc
        if not isinstance(data, dict) or "t" not in data:
            raise ProtocolError("peer sent a message without a type")
        return data

    def send_bytes(self, data: bytes) -> None:
        frame = data
        if self.enc_key is not None:
            assert self.mac_key is not None
            frame = seal(self.enc_key, self.mac_key, data)
        if len(frame) > MAX_FRAME:
            raise ProtocolError("message is too large")
        self.sock.sendall(struct.pack("!I", len(frame)) + frame)

    def recv_bytes(self, max_plain: int) -> bytes:
        header = _read_exact(self.sock, 4)
        size = struct.unpack("!I", header)[0]
        if size > MAX_FRAME:
            raise ProtocolError("peer announced a frame that is too large")
        frame = _read_exact(self.sock, size)
        if self.enc_key is not None:
            assert self.mac_key is not None
            try:
                data = open_seal(self.enc_key, self.mac_key, frame)
            except AuthError as exc:
                raise ProtocolError(str(exc)) from exc
        else:
            data = frame
        if len(data) > max_plain:
            raise ProtocolError("peer sent a payload that is too large")
        return data


def _read_exact(sock: socket.socket, size: int) -> bytes:
    if size < 0:
        raise ProtocolError("negative frame length")
    buf = bytearray()
    while len(buf) < size:
        try:
            chunk = sock.recv(size - len(buf))
        except socket.timeout as exc:
            raise ProtocolError("timed out waiting for the peer") from exc
        except OSError as exc:
            raise ProtocolError(f"connection failed: {exc}") from exc
        if not chunk:
            raise ProtocolError("connection closed")
        buf.extend(chunk)
    return bytes(buf)
