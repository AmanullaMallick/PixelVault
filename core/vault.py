"""End-to-end pipeline: path -> tar -> chunks -> encrypt -> RS parity -> PNG tiles,
and the reverse.

Design notes for this milestone:
- Every tile header carries the full per-chunk lists (nonces, ciphertext
  lengths) and the salt. This is what lets any k surviving tiles decode the
  vault: the lost tile's nonce is copied into every survivor.
- Chunk AAD binds ciphertext to (file_id, chunk index), so tiles can't be
  reordered without failing authentication.
- Reed-Solomon runs over ciphertext shards, so a missing shard is rebuilt
  before decryption, and each chunk's GCM tag is checked afterwards.
"""

from __future__ import annotations

import tarfile
import tempfile
import uuid
from pathlib import Path

from cryptography.exceptions import InvalidTag

from core import archiver, crypto_engine as ce, ecc_matrix as ecc, image_tiler as tiler
from core.config import Config

VAULT_VERSION = "1.0"


def _aad(file_id: str, index: int) -> bytes:
    return f"{file_id}:{index}".encode("ascii")


def _tile_name(shard_index: int) -> str:
    return f"vault_block_{shard_index + 1:02d}.png"


def encode_vault(src: Path, out_dir: Path, cfg: Config, passphrase: str | None = None) -> list[Path]:
    cfg.validate()
    if cfg.encryption_enabled and not passphrase:
        raise ValueError("passphrase required when encryption is enabled")

    src, out_dir = Path(src), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    file_id = uuid.uuid4().hex

    with tempfile.TemporaryFile() as tar_f:
        archiver.pack(src, tar_f)
        tar_f.seek(0)
        plain_chunks = list(archiver.iter_chunks(tar_f, cfg.chunk_size))

    salt_hex = None
    nonces_hex = None
    if cfg.encryption_enabled:
        salt = ce.new_salt()
        key = ce.derive_key(passphrase, salt, cfg.pbkdf2_iterations)
        salt_hex = salt.hex()
        nonces_hex, shards = [], []
        for i, chunk in enumerate(plain_chunks):
            nonce, ct = ce.encrypt_chunk(key, chunk, _aad(file_id, i))
            nonces_hex.append(nonce.hex())
            shards.append(ct)
    else:
        shards = plain_chunks

    k = len(shards)
    m = cfg.parity_count(k)
    shard_len = max(len(s) for s in shards)
    padded = [s.ljust(shard_len, b"\x00") for s in shards]
    all_shards = padded + ecc.encode(padded, m)

    common = {
        "vault_version": VAULT_VERSION,
        "file_id": file_id,
        "k": k,
        "m": m,
        "canvas_side": cfg.canvas_side,
        "chunk_size": cfg.chunk_size,
        "parity_percent": cfg.rs_parity_percent,
        "is_encrypted": cfg.encryption_enabled,
        "pbkdf2_iterations": cfg.pbkdf2_iterations if cfg.encryption_enabled else None,
        "salt_hex": salt_hex,
        "nonces_hex": nonces_hex,
        "ct_lengths": [len(s) for s in shards],
        "shard_len": shard_len,
    }

    paths = []
    for idx, payload in enumerate(all_shards):
        header = {**common, "shard_index": idx, "is_parity": idx >= k}
        raw = tiler.pack_payload(header, payload, cfg.canvas_side)
        path = out_dir / _tile_name(idx)
        tiler.write_tile(path, cfg.canvas_side, raw)
        paths.append(path)
    return paths


def decode_vault(tile_paths: list[Path], dest_dir: Path, passphrase: str | None = None) -> tuple[list[str], list[str]]:
    """Restore a vault from any k of its tiles.

    Returns (restored member names, warnings).
    """
    tiles = []
    for p in tile_paths:
        _side, raw = tiler.read_tile(p)
        tiles.append(tiler.unpack_payload(raw))

    file_ids = {h["file_id"] for h, _ in tiles}
    if len(file_ids) != 1:
        raise ValueError("tiles belong to more than one vault")

    h0 = tiles[0][0]
    k, m = h0["k"], h0["m"]

    survivors: dict[int, bytes] = {}
    for h, payload in tiles:
        survivors.setdefault(h["shard_index"], payload)
    if len(survivors) < k:
        raise ValueError(f"need at least {k} distinct tiles, found {len(survivors)}")

    warnings = []
    if m > 0 and len(survivors) == k:
        warnings.append(
            "No redundancy left: exactly k tiles were supplied. "
            "Losing any one more tile will make restore impossible."
        )

    data = ecc.decode(survivors, k, m)

    key = None
    if h0["is_encrypted"]:
        if not passphrase:
            raise ValueError("passphrase required to restore an encrypted vault")
        key = ce.derive_key(passphrase, bytes.fromhex(h0["salt_hex"]), h0["pbkdf2_iterations"])

    with tempfile.TemporaryFile() as tar_f:
        for i in range(k):
            ct = data[i][: h0["ct_lengths"][i]]
            if key is not None:
                try:
                    plain = ce.decrypt_chunk(
                        key, bytes.fromhex(h0["nonces_hex"][i]), ct, _aad(h0["file_id"], i)
                    )
                except InvalidTag:
                    raise ValueError("decryption failed: wrong passphrase or tampered tile") from None
            else:
                plain = ct
            tar_f.write(plain)
        tar_f.seek(0)
        names = archiver.extract(tar_f, Path(dest_dir))
    return names, warnings
