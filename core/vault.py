"""End-to-end pipeline: path -> tar -> chunks -> encrypt -> RS parity -> PNG tiles,
and the reverse. Streaming throughout: no step holds the whole archive in memory.

Design notes:
- The per-chunk nonce list and ciphertext length list live in the manifest,
  which is encrypted when encryption is on and is present in every tile. A lost
  tile's nonce is therefore recoverable from any k survivors, and nothing about
  chunk sizes is visible without the passphrase.
- The plaintext header holds only tile geometry (k, m, shard_len, canvas, chunk
  size) and the KDF salt and settings needed to derive the key.
- Chunk AAD binds ciphertext to (file_id, chunk index), so reordering tiles
  fails authentication.
- Reed-Solomon runs over ciphertext shards, so a missing shard is rebuilt
  before decryption, and each chunk's GCM tag is checked afterwards.
- Tile write/read runs on a thread pool. Progress is reported through an
  optional callback progress(stage, done, total). The core draws nothing.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

from cryptography.exceptions import InvalidTag

from core import archiver, crypto_engine as ce, ecc_matrix as ecc, image_tiler as tiler
from core.config import Config

VAULT_VERSION = "1.2"


def _manifest_aad(file_id: str) -> bytes:
    return f"manifest:{file_id}".encode("ascii")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()

ProgressCb = Optional[Callable[[str, int, int], None]]


def _report(cb: ProgressCb, stage: str, done: int, total: int) -> None:
    if cb is not None:
        cb(stage, done, total)


def _aad(file_id: str, index: int) -> bytes:
    return f"{file_id}:{index}".encode("ascii")


def _tile_name(shard_index: int) -> str:
    return f"vault_block_{shard_index + 1:02d}.png"


def parity_warning_for_size(total_bytes: int, cfg: Config) -> str | None:
    """Warning text if parity adds whole images for `total_bytes` of input, else None.

    The size is an estimate from source bytes; tar headers add a little more.
    """
    cfg.validate()
    k = max(1, -(-total_bytes // cfg.chunk_size))
    m = cfg.parity_count(k)
    if m == 0:
        return None
    pct = 100 * m / k
    return (
        f"Parity at {cfg.rs_parity_percent}% adds {m} extra image(s) "
        f"(about +{pct:.0f}% storage). Continue, or set parity to 0?"
    )


def export_warning(src: Path, cfg: Config) -> str | None:
    """Warning for a file or folder. See parity_warning_for_size."""
    src = Path(src)
    if src.is_dir():
        total = sum(p.stat().st_size for p in src.rglob("*") if p.is_file())
    else:
        total = src.stat().st_size
    return parity_warning_for_size(total, cfg)


def scan_folder(folder: Path) -> dict[str, list[Path]]:
    """Group PNG tiles in `folder` by vault file_id. Names are ignored; headers decide."""
    groups: dict[str, list[Path]] = {}
    for p in sorted(Path(folder).glob("*.png")):
        try:
            header = tiler.read_tile_stream(p, _Discard())
        except (ValueError, OSError):
            continue  # not a PixelVault tile
        groups.setdefault(header["file_id"], []).append(p)
    return groups


class _Discard:
    def write(self, data: bytes) -> int:
        return len(data)


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

    src = Path(src)
    file_id = uuid.uuid4().hex
    out_dir = Path(out_dir) / f"vault_{file_id[:8]}"
    if out_dir.exists():
        raise ValueError(f"{out_dir} already exists; refusing to overwrite")
    out_dir.mkdir(parents=True)

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

        # the manifest is built after chunking, since it carries the per-chunk lists
        manifest_doc = {
            "original_name": src.name,
            "archive_sha256": _sha256_file(tar_path),
            "archive_size": tar_path.stat().st_size,
            "ct_lengths": ct_lengths,
        }
        if key is not None:
            manifest_doc["nonces_hex"] = nonces_hex

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
            "shard_len": shard_len,
        }
        manifest = json.dumps(manifest_doc, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if cfg.encryption_enabled:
            m_nonce, m_ct = ce.encrypt_chunk(key, manifest, _manifest_aad(file_id))
            common["manifest_nonce_hex"] = m_nonce.hex()
            common["manifest_ct_hex"] = m_ct.hex()
        else:
            common["manifest_plain"] = manifest.decode("utf-8")

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

        if key is not None:
            try:
                manifest_bytes = ce.decrypt_chunk(
                    key, bytes.fromhex(h0["manifest_nonce_hex"]),
                    bytes.fromhex(h0["manifest_ct_hex"]), _manifest_aad(h0["file_id"]),
                )
            except InvalidTag:
                raise ValueError("decryption failed: wrong passphrase or tampered tile") from None
        else:
            manifest_bytes = h0["manifest_plain"].encode("utf-8")
        manifest = json.loads(manifest_bytes.decode("utf-8"))

        tar_path = work / "archive.tar"
        with open(tar_path, "wb") as tar_f:
            for i in range(k):
                with open(data_paths[i], "rb") as df:
                    ct = df.read(manifest["ct_lengths"][i])
                if key is not None:
                    try:
                        plain = ce.decrypt_chunk(
                            key, bytes.fromhex(manifest["nonces_hex"][i]), ct, _aad(h0["file_id"], i)
                        )
                    except InvalidTag:
                        raise ValueError(
                            "decryption failed: wrong passphrase or tampered tile"
                        ) from None
                else:
                    plain = ct
                tar_f.write(plain)
                _report(progress, "decrypt", i + 1, k)

        # 4. verify the whole archive against the manifest hash, then extract
        if _sha256_file(tar_path) != manifest["archive_sha256"]:
            raise ValueError("archive hash mismatch: restored data does not match the original")
        with open(tar_path, "rb") as tar_f:
            members = archiver.list_names(tar_f)
        clashes = [n for n in members if (dest_dir / n).exists()]
        if clashes:
            raise ValueError(f"would overwrite existing files: {', '.join(clashes[:5])}")
        with open(tar_path, "rb") as tar_f:
            names = archiver.extract(tar_f, dest_dir)
    return names, warnings
