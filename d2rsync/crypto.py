"""Shared-secret encryption for save payloads.

The standard library has no AES. After the handshake, each message is
encrypted with an HMAC-SHA256 keystream and authenticated with a second
HMAC. A device that does not know the secret cannot read or forge saves.
This is meant for a home LAN, not as a replacement for a VPN.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from d2rsync.errors import AuthError

NONCE_LEN = 16
MAC_LEN = 32


def session_keys(secret: str, client_nonce: bytes, server_nonce: bytes) -> tuple[bytes, bytes]:
    material = hmac.new(
        secret.encode("utf-8"),
        b"session|" + client_nonce + b"|" + server_nonce,
        hashlib.sha256,
    ).digest()
    enc_key = hmac.new(material, b"enc", hashlib.sha256).digest()
    mac_key = hmac.new(material, b"mac", hashlib.sha256).digest()
    return enc_key, mac_key


def seal(enc_key: bytes, mac_key: bytes, plaintext: bytes) -> bytes:
    nonce = secrets.token_bytes(NONCE_LEN)
    ciphertext = _xor(plaintext, _keystream(enc_key, nonce, len(plaintext)))
    mac = hmac.new(mac_key, b"mac|" + nonce + ciphertext, hashlib.sha256).digest()
    return nonce + ciphertext + mac


def open_seal(enc_key: bytes, mac_key: bytes, blob: bytes) -> bytes:
    if len(blob) < NONCE_LEN + MAC_LEN:
        raise AuthError("truncated encrypted message")
    nonce = blob[:NONCE_LEN]
    mac = blob[-MAC_LEN:]
    ciphertext = blob[NONCE_LEN:-MAC_LEN]
    expected = hmac.new(mac_key, b"mac|" + nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise AuthError("encrypted message failed authentication")
    return _xor(ciphertext, _keystream(enc_key, nonce, len(ciphertext)))


def _keystream(key: bytes, nonce: bytes, nbytes: int) -> bytes:
    if nbytes == 0:
        return b""
    out = bytearray()
    counter = 0
    while len(out) < nbytes:
        block = hmac.new(
            key,
            nonce + counter.to_bytes(8, "big"),
            hashlib.sha256,
        ).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:nbytes])


def _xor(data: bytes, stream: bytes) -> bytes:
    if len(data) != len(stream):
        raise AuthError("keystream length mismatch")
    return bytes(left ^ right for left, right in zip(data, stream))
