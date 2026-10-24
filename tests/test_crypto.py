"""Handshake crypto and the shared-secret proof."""

from __future__ import annotations

import unittest

from d2rsync.auth import client_proof, proofs_match, server_proof
from d2rsync.crypto import open_seal, seal, session_keys
from d2rsync.errors import AuthError


class CryptoTests(unittest.TestCase):
    def test_seal_roundtrip_and_tamper(self):
        enc, mac = session_keys("correct horse battery", b"c" * 16, b"s" * 16)
        blob = seal(enc, mac, b"Hero.d2s contents")
        self.assertEqual(open_seal(enc, mac, blob), b"Hero.d2s contents")
        other = seal(enc, mac, b"Hero.d2s contents")
        self.assertNotEqual(blob, other)
        flipped = bytearray(blob)
        flipped[20] ^= 0x01
        with self.assertRaises(AuthError):
            open_seal(enc, mac, bytes(flipped))
        wrong_enc, wrong_mac = session_keys("other secret", b"c" * 16, b"s" * 16)
        with self.assertRaises(AuthError):
            open_seal(wrong_enc, wrong_mac, blob)

    def test_proofs_are_role_separated_and_constant_time_compared(self):
        secret = "shared-secret"
        client = client_proof(secret, b"1" * 16, b"2" * 16)
        server = server_proof(secret, b"1" * 16, b"2" * 16)
        self.assertNotEqual(client, server)
        self.assertTrue(proofs_match(client, client_proof(secret, b"1" * 16, b"2" * 16)))
        self.assertFalse(proofs_match(client, server))
        self.assertFalse(proofs_match(client, client_proof("nope-nope", b"1" * 16, b"2" * 16)))


if __name__ == "__main__":
    unittest.main()
