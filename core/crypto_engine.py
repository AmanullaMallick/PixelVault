"""PBKDF2 key derivation and per-tile AES-256-GCM encryption.

Each tile gets its own random 12-byte nonce. The salt is per vault (per file),
so one passphrase + salt yields one key for all tiles of that vault.
"""

import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

SALT_BYTES = 16
NONCE_BYTES = 12
KEY_BYTES = 32
MIN_PBKDF2_ITERATIONS = 600_000


def new_salt() -> bytes:
    return os.urandom(SALT_BYTES)


def derive_key(passphrase: str, salt: bytes, iterations: int = MIN_PBKDF2_ITERATIONS) -> bytes:
    """Derive a 256-bit AES key with PBKDF2-HMAC-SHA256.

    Refuses iteration counts below the SRS floor so the setting cannot be
    lowered by accident.
    """
    if iterations < MIN_PBKDF2_ITERATIONS:
        raise ValueError(
            f"pbkdf2_iterations must be >= {MIN_PBKDF2_ITERATIONS}, got {iterations}"
        )
    if len(salt) != SALT_BYTES:
        raise ValueError(f"salt must be {SALT_BYTES} bytes")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_BYTES,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_chunk(key: bytes, plaintext: bytes, aad: bytes = b"") -> tuple[bytes, bytes]:
    """Encrypt one chunk. Returns (nonce, ciphertext_with_tag).

    `aad` binds the ciphertext to its header fields (e.g. chunk index), so a
    tile cannot be moved to a different position without failing authentication.
    """
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    return nonce, ciphertext


def decrypt_chunk(key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes = b"") -> bytes:
    """Decrypt and authenticate one chunk. Raises InvalidTag on any tampering."""
    if len(nonce) != NONCE_BYTES:
        raise ValueError(f"nonce must be {NONCE_BYTES} bytes")
    return AESGCM(key).decrypt(nonce, ciphertext, aad)
