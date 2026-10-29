import os
import unittest

from cryptography.exceptions import InvalidTag

from core import crypto_engine as ce


class TestCrypto(unittest.TestCase):
    def setUp(self):
        self.salt = ce.new_salt()
        self.key = ce.derive_key("correct horse battery staple", self.salt)

    def test_roundtrip(self):
        msg = os.urandom(4096)
        nonce, ct = ce.encrypt_chunk(self.key, msg, aad=b"chunk=0")
        self.assertEqual(ce.decrypt_chunk(self.key, nonce, ct, aad=b"chunk=0"), msg)

    def test_nonces_are_unique_per_tile(self):
        nonces = {ce.encrypt_chunk(self.key, b"x")[0] for _ in range(200)}
        self.assertEqual(len(nonces), 200)

    def test_tampered_ciphertext_fails(self):
        nonce, ct = ce.encrypt_chunk(self.key, b"hello world")
        bad = bytes([ct[0] ^ 1]) + ct[1:]
        with self.assertRaises(InvalidTag):
            ce.decrypt_chunk(self.key, nonce, bad)

    def test_wrong_aad_fails(self):
        nonce, ct = ce.encrypt_chunk(self.key, b"data", aad=b"chunk=1")
        with self.assertRaises(InvalidTag):
            ce.decrypt_chunk(self.key, nonce, ct, aad=b"chunk=2")

    def test_iteration_floor_enforced(self):
        with self.assertRaises(ValueError):
            ce.derive_key("pw", self.salt, iterations=599_999)


if __name__ == "__main__":
    unittest.main()
