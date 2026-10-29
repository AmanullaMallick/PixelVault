"""End-to-end pipeline: path -> tar -> chunks -> encrypt -> RS parity -> PNG tiles,
and the reverse. Streaming throughout: no step holds the whole archive in memory.

Design notes:
- Every tile header carries the full per-chunk lists (nonces, ciphertext lengths)
  and the salt. The lost tile's nonce is therefore copied into every survivor.
- Chunk AAD binds ciphertext to (file_id, chunk index), so reordering tiles
  fails authentication.
- Reed-Solomon runs over ciphertext shards, so a missing shard is rebuilt
  before decryption, and each chunk's GCM tag is checked afterwards.
- Tile write/read runs on a thread pool. Progress is reported through an
  optional callback progress(stage, done, total). The core draws nothing.
"""

from __future__ import annotations

import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

from cryptography.exceptions import InvalidTag

from core import archiver, crypto_engine as ce, ecc_matrix as ecc, image_tiler as tiler
from core.config import Config

VAULT_VERSION = "1.0"

ProgressCb = Optional[Callable[[str, int, int], None]]


def _report(cb: ProgressCb, stage: str, done: int, total: int) -> None:
    if cb is not None:
        cb(stage, done, total)


def _aad(file_id: str, index: int) -> bytes:
    return f"{file_id}:{index}".encode("ascii")


def _tile_name(shard_index: int) -> str:
    return f"vault_block_{shard_index + 1:02d}.png"


def encode_vault(
    src: Path,
    out_dir: Path,
    cfg: Config,
    passphrase: str | None = None,
    progress: ProgressCb = None,
    work_dir: Path | None = None,
) -> list[Path]:
    cfg.validate()
    if cfg.encryption_enabled and not passphrase:
        raise ValueError("passphrase required when encryption is enabled")

    src, out_dir = Path(src), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    file_id = uuid.uuid4().hex

    with tempfile.TemporaryDirectory(prefix="pv_enc_", dir=work_dir) as work_s:
        work = Path(work_s)

        # 1. archive to disk
        tar_path = work / "archive.tar"
        with open(tar_path, "wb") as f:
            archiver.pack(src, f)
        _report(progress, "archive", 1, 1)

        # 2. chunk (and encrypt) into shard files, one chunk in memory at a time
        total_bytes = tar_path.stat().st_size
        n_chunks = -(-total_bytes // cfg.chunk_size)
        key = salt_hex = None
        if cfg.encryption_enabled:
            salt = ce.new_salt()
            key = ce.derive_key(passphrase, salt, cfg.pbkdf2_iterations)
            salt_hex = salt.hex()
        nonces_hex: list[str] = []
        data_paths: list[Path] = []
        ct_lengths: list[int] = []
        with open(tar_path, "rb") as tf:
            for i in range(n_chunks):
                chunk = tf.read(cfg.chunk_size)
                if key is not None:
                    nonce, payload = ce.encrypt_chunk(key, chunk, _aad(file_id, i))
                    nonces_hex.append(nonce.hex())
                else:
                    payload = chunk
                p = work / f"data_{i:05d}.bin"
                p.write_bytes(payload)
                data_paths.append(p)
                ct_lengths.append(len(payload))
                _report(progress, "encrypt" if key is not None else "chunk", i + 1, n_chunks)

        # 3. erasure parity, block by block
        k = len(data_paths)
        m = cfg.parity_count(k)
        shard_len = max(ct_lengths)
        parity_paths = [work / f"parity_{j:03d}.bin" for j in range(m)]
        ecc.encode_files(
            data_paths, parity_paths, shard_len, cfg.ecc_block_bytes,
            progress=lambda done, total: _report(progress, "parity", done, total),
        )

        # 4. write tiles in parallel
        shard_paths = data_paths + parity_paths
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
            "nonces_hex": nonces_hex if cfg.encryption_enabled else None,
            "ct_lengths": ct_lengths,
            "shard_len": shard_len,
        }

        def write_one(idx: int) -> None:
            header = {**common, "shard_index": idx, "is_parity": idx >= k}
            with open(shard_paths[idx], "rb") as pf:
                tiler.write_tile_stream(
                    out_dir / _tile_name(idx), cfg.canvas_side, header, pf, shard_len
                )

        total = len(shard_paths)
        with ThreadPoolExecutor(max_workers=cfg.workers) as ex:
            futures = [ex.submit(write_one, i) for i in range(total)]
            for done, fut in enumerate(as_completed(futures), start=1):
                fut.result()  # re-raise any worker error
                _report(progress, "write_tiles", done, total)

    return [out_dir / _tile_name(i) for i in range(total)]


def decode_vault(
    tile_paths: list[Path],
    dest_dir: Path,
    passphrase: str | None = None,
    cfg: Config | None = None,
    progress: ProgressCb = None,
    work_dir: Path | None = None,
) -> tuple[list[str], list[str]]:
    """Restore a vault from any k of its tiles. Returns (member names, warnings)."""
    cfg = cfg or Config()
    tile_paths = [Path(p) for p in tile_paths]
    dest_dir = Path(dest_dir)

    with tempfile.TemporaryDirectory(prefix="pv_dec_", dir=work_dir) as work_s:
        work = Path(work_s)

        # 1. read every supplied tile's payload to a file, in parallel
        def read_one(n: int):
            out = work / f"tile_{n:03d}.bin"
            with open(out, "wb") as payload_out:
                header = tiler.read_tile_stream(tile_paths[n], payload_out)
            return header, out

        total = len(tile_paths)
        results = [None] * total
        with ThreadPoolExecutor(max_workers=cfg.workers) as ex:
            futures = {ex.submit(read_one, n): n for n in range(total)}
            for done, fut in enumerate(as_completed(futures), start=1):
                results[futures[fut]] = fut.result()
                _report(progress, "read_tiles", done, total)

        headers = [r[0] for r in results]
        h0 = headers[0]
        if len({h["file_id"] for h in headers}) != 1:
            raise ValueError("tiles belong to more than one vault")
        k, m, shard_len = h0["k"], h0["m"], h0["shard_len"]
        if any(h["shard_len"] != shard_len or h["k"] != k or h["m"] != m for h in headers):
            raise ValueError("tile headers disagree about the vault layout")

        survivors: dict[int, Path] = {}
        for h, (_, path) in zip(headers, results):
            survivors.setdefault(h["shard_index"], path)
        if len(survivors) < k:
            raise ValueError(f"need at least {k} distinct tiles, found {len(survivors)}")

        warnings: list[str] = []
        if m > 0 and len(survivors) == k:
            warnings.append(
                "No redundancy left: exactly k tiles were supplied. "
                "Losing any one more tile will make restore impossible."
            )

        # 2. rebuild the k ciphertext shards, block by block
        data_paths = [work / f"data_{i:05d}.bin" for i in range(k)]
        ecc.decode_files(
            survivors, data_paths, k, m, shard_len, cfg.ecc_block_bytes,
            progress=lambda done, total_b: _report(progress, "rebuild", done, total_b),
        )

        # 3. decrypt each chunk (or copy it) into the tar file
        key = None
        if h0["is_encrypted"]:
            if not passphrase:
                raise ValueError("passphrase required to restore an encrypted vault")
            key = ce.derive_key(passphrase, bytes.fromhex(h0["salt_hex"]), h0["pbkdf2_iterations"])

        tar_path = work / "archive.tar"
        with open(tar_path, "wb") as tar_f:
            for i in range(k):
                with open(data_paths[i], "rb") as df:
                    ct = df.read(h0["ct_lengths"][i])
                if key is not None:
                    try:
                        plain = ce.decrypt_chunk(
                            key, bytes.fromhex(h0["nonces_hex"][i]), ct, _aad(h0["file_id"], i)
                        )
                    except InvalidTag:
                        raise ValueError(
                            "decryption failed: wrong passphrase or tampered tile"
                        ) from None
                else:
                    plain = ct
                tar_f.write(plain)
                _report(progress, "decrypt", i + 1, k)

        # 4. extract
        with open(tar_path, "rb") as tar_f:
            names = archiver.extract(tar_f, dest_dir)
    return names, warnings
