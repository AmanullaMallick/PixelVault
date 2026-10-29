"""Raw bytes <-> lossless RGB PNG tiles.

A tile is a square RGB image of side N. Its raw pixel bytes are laid out as:

    [4-byte big-endian header length][UTF-8 JSON header][zero pad to 3-byte boundary]
    [payload (shard_len bytes)][zero padding to N*N*3]

PNG is written by hand with zlib at level 0, streamed one scanline at a time,
so no full image is built in memory by the writer. Filter type is always 0.
"""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _write_chunk(f, ctype: bytes, data: bytes) -> None:
    f.write(struct.pack(">I", len(data)))
    f.write(ctype)
    f.write(data)
    f.write(struct.pack(">I", zlib.crc32(data, zlib.crc32(ctype)) & 0xFFFFFFFF))


# --- raw layout

def pack_payload(header: dict, payload: bytes, side: int) -> bytes:
    """Build the full raw pixel buffer (side*side*3 bytes) for one tile."""
    if header.get("shard_len") != len(payload):
        raise ValueError("header shard_len must equal payload length")
    capacity = side * side * 3
    hj = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    prefix = struct.pack(">I", len(hj)) + hj
    prefix += b"\x00" * ((-len(prefix)) % 3)
    if len(prefix) + len(payload) > capacity:
        raise ValueError(
            f"tile too small: need {len(prefix) + len(payload)} bytes, capacity {capacity}"
        )
    raw = prefix + payload
    return raw + b"\x00" * (capacity - len(raw))


def unpack_payload(raw: bytes) -> tuple[dict, bytes]:
    """Inverse of pack_payload. Returns (header, payload)."""
    (hl,) = struct.unpack(">I", raw[:4])
    header = json.loads(raw[4 : 4 + hl].decode("utf-8"))
    region = 4 + hl
    region += (-region) % 3
    n = header["shard_len"]
    return header, raw[region : region + n]


# --- PNG I/O

def write_tile(path: Path, side: int, raw: bytes) -> None:
    """Write raw RGB bytes as a lossless PNG, one scanline at a time."""
    row_len = side * 3
    if len(raw) != side * row_len:
        raise ValueError("raw length does not match side*side*3")
    compressor = zlib.compressobj(0)  # level 0: stored blocks, no compression
    with open(path, "wb") as f:
        f.write(PNG_SIGNATURE)
        _write_chunk(f, b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
        for r in range(side):
            out = compressor.compress(b"\x00" + raw[r * row_len : (r + 1) * row_len])
            if out:
                _write_chunk(f, b"IDAT", out)
        tail = compressor.flush()
        if tail:
            _write_chunk(f, b"IDAT", tail)
        _write_chunk(f, b"IEND", b"")


def read_tile(path: Path) -> tuple[int, bytes]:
    """Read a tile written by write_tile. Returns (side, raw RGB bytes)."""
    data = Path(path).read_bytes()
    if data[:8] != PNG_SIGNATURE:
        raise ValueError(f"{path} is not a PNG file")
    pos = 8
    side = None
    idat: list[bytes] = []
    while pos < len(data):
        (n,) = struct.unpack(">I", data[pos : pos + 4])
        ctype = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + n]
        (crc,) = struct.unpack(">I", data[pos + 8 + n : pos + 12 + n])
        if zlib.crc32(body, zlib.crc32(ctype)) & 0xFFFFFFFF != crc:
            raise ValueError(f"{path}: CRC mismatch in {ctype!r} chunk")
        pos += 12 + n
        if ctype == b"IHDR":
            w, h, bd, ct, _cm, _fm, il = struct.unpack(">IIBBBBB", body)
            if w != h or bd != 8 or ct != 2 or il != 0:
                raise ValueError(f"{path}: unsupported PNG format")
            side = w
        elif ctype == b"IDAT":
            idat.append(body)
        elif ctype == b"IEND":
            break
    if side is None:
        raise ValueError(f"{path}: missing IHDR")

    row_len = side * 3
    rows = zlib.decompress(b"".join(idat))
    if len(rows) != side * (row_len + 1):
        raise ValueError(f"{path}: unexpected pixel data length")
    out = bytearray()
    for r in range(side):
        off = r * (row_len + 1)
        if rows[off] != 0:
            raise ValueError(f"{path}: unsupported PNG filter type")
        out += rows[off + 1 : off + 1 + row_len]
    return side, bytes(out)
