"""Erasure coding over GF(256): any k of (k + m) shards rebuild the data.

Pure Python, no third-party dependency (PyPI was unreachable when this was
written). Layout is systematic: the first k shards are the data itself, the
last m are parity computed with a Cauchy matrix, which is MDS, so every
k-row submatrix of [I; C] is invertible.

Two interfaces:
- encode() / decode(): in-memory, for small inputs and tests.
- encode_files() / decode_files(): work block by block on files, so memory
  use is k * block_bytes, not the whole data set.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

# --- GF(256) arithmetic, primitive polynomial 0x11d (same as common RS codecs)

_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _gf_inv(a: int) -> int:
    if a == 0:
        raise ZeroDivisionError("inverse of 0 in GF(256)")
    return _EXP[255 - _LOG[a]]


# Precomputed multiply-by-constant tables: scaling a block is one bytes.translate
# call (C speed) instead of a Python loop per byte.
_MUL_TABLE = [bytes(_gf_mul(c, b) for b in range(256)) for c in range(256)]


def _scale(block: bytes, c: int) -> bytes:
    return block.translate(_MUL_TABLE[c])


def _xor_bytes(parts: list[bytes], length: int) -> bytes:
    acc = 0
    for p in parts:
        acc ^= int.from_bytes(p, "big")
    return acc.to_bytes(length, "big")


# --- matrix helpers

def _cauchy_row(x: int, ys: list[int]) -> list[int]:
    return [_gf_inv(x ^ y) for y in ys]


def _generator_row(index: int, k: int, m: int) -> list[int]:
    """Row `index` of the (k+m) x k generator matrix [I; C]."""
    if index < k:
        return [1 if j == index else 0 for j in range(k)]
    j = index - k
    # x values are k..k+m-1, y values are 0..k-1: all distinct because k+m <= 256.
    return _cauchy_row(k + j, list(range(k)))


def _invert(matrix: list[list[int]]) -> list[list[int]]:
    n = len(matrix)
    aug = [row[:] + [1 if i == j else 0 for j in range(n)] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = next((r for r in range(col, n) if aug[r][col] != 0), None)
        if pivot is None:
            raise ValueError("singular matrix (should not happen for Cauchy/MDS)")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        inv = _gf_inv(aug[col][col])
        aug[col] = [_gf_mul(inv, v) for v in aug[col]]
        for r in range(n):
            if r != col and aug[r][col] != 0:
                f = aug[r][col]
                aug[r] = [a ^ _gf_mul(f, b) for a, b in zip(aug[r], aug[col])]
    return [row[n:] for row in aug]


# --- in-memory API

def encode(data_shards: list[bytes], m: int) -> list[bytes]:
    """Return the m parity shards for the given equal-length data shards."""
    k = len(data_shards)
    if k == 0:
        raise ValueError("need at least one data shard")
    if m == 0:
        return []
    if k + m > 256:
        raise ValueError("k + m must be <= 256 for GF(256)")
    length = len(data_shards[0])
    if any(len(s) != length for s in data_shards):
        raise ValueError("all shards must have the same length")

    parity = []
    for j in range(m):
        coeffs = _generator_row(k + j, k, m)
        parts = [_scale(data_shards[l], coeffs[l]) for l in range(k)]
        parity.append(_xor_bytes(parts, length))
    return parity


def decode(shards: dict[int, bytes], k: int, m: int) -> list[bytes]:
    """Rebuild the k data shards from any k surviving shards (in memory)."""
    if len(shards) < k:
        raise ValueError(f"need at least {k} shards, got {len(shards)}")
    if all(i in shards for i in range(k)):
        return [shards[i] for i in range(k)]

    chosen = sorted(shards)[:k]
    length = len(shards[chosen[0]])
    if any(len(shards[i]) != length for i in chosen):
        raise ValueError("all shards must have the same length")

    inv = _invert([_generator_row(i, k, m) for i in chosen])
    data = []
    for l in range(k):
        parts = [_scale(shards[chosen[r]], inv[l][r]) for r in range(k)]
        data.append(_xor_bytes(parts, length))
    return data


# --- streaming API (block by block, bounded memory)

ProgressFn = Optional[Callable[[int, int], None]]


def _read_block(f, n: int) -> bytes:
    """Read exactly n bytes, zero-padding past end of file."""
    block = f.read(n)
    if len(block) < n:
        block += b"\x00" * (n - len(block))
    return block


def encode_files(
    data_paths: list[Path],
    parity_paths: list[Path],
    length: int,
    block: int,
    progress: ProgressFn = None,
) -> None:
    """Compute parity files from data files, `block` bytes at a time.

    Each data file is zero-padded to `length` bytes. Each parity file is
    written with exactly `length` bytes.
    """
    k, m = len(data_paths), len(parity_paths)
    if m == 0:
        return
    if k == 0:
        raise ValueError("need at least one data shard")
    if k + m > 256:
        raise ValueError("k + m must be <= 256 for GF(256)")
    if block < 1:
        raise ValueError("block must be >= 1")

    coeffs = [_generator_row(k + j, k, m) for j in range(m)]
    ins = [open(p, "rb") for p in data_paths]
    outs = [open(p, "wb") for p in parity_paths]
    try:
        for off in range(0, length, block):
            n = min(block, length - off)
            blocks = [_read_block(f, n) for f in ins]
            for j, out in enumerate(outs):
                parts = [_scale(blocks[l], coeffs[j][l]) for l in range(k)]
                out.write(_xor_bytes(parts, n))
            if progress:
                progress(min(off + n, length), length)
    finally:
        for f in ins + outs:
            f.close()


def decode_files(
    survivor_paths: dict[int, Path],
    out_paths: list[Path],
    k: int,
    m: int,
    length: int,
    block: int,
    progress: ProgressFn = None,
) -> None:
    """Rebuild k data shard files from any k surviving shard files, block by block.

    Each survivor file must contain exactly `length` bytes.
    """
    if len(survivor_paths) < k:
        raise ValueError(f"need at least {k} shards, got {len(survivor_paths)}")
    if block < 1:
        raise ValueError("block must be >= 1")

    chosen = sorted(survivor_paths)[:k]
    inv = _invert([_generator_row(i, k, m) for i in chosen])
    ins = {i: open(survivor_paths[i], "rb") for i in chosen}
    outs = [open(p, "wb") for p in out_paths]
    try:
        for off in range(0, length, block):
            n = min(block, length - off)
            blocks = {i: _read_block(ins[i], n) for i in chosen}
            for l, out in enumerate(outs):
                parts = [_scale(blocks[chosen[r]], inv[l][r]) for r in range(k)]
                out.write(_xor_bytes(parts, n))
            if progress:
                progress(min(off + n, length), length)
    finally:
        for f in list(ins.values()) + outs:
            f.close()
