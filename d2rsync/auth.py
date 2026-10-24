"""Mutual proof that both sides know the shared secret.

The secret itself never crosses the network. Each side sends
HMAC(secret, role | both nonces).
"""

from __future__ import annotations

import hashlib
import hmac


def client_proof(secret: str, client_nonce: bytes, server_nonce: bytes) -> str:
    return _proof("client", secret, client_nonce, server_nonce)


def server_proof(secret: str, client_nonce: bytes, server_nonce: bytes) -> str:
    return _proof("server", secret, client_nonce, server_nonce)


def proofs_match(expected: str, presented: str) -> bool:
    if len(expected) != len(presented):
        return False
    return hmac.compare_digest(expected, presented)


def _proof(role: str, secret: str, client_nonce: bytes, server_nonce: bytes) -> str:
    message = role.encode("ascii") + b"|" + client_nonce + b"|" + server_nonce
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
