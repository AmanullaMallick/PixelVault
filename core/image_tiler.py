"""Raw bytes <-> lossless RGB PNG tiles.

A tile is a square RGB image of side N. Its raw pixel bytes are laid out as:

    [4-byte big-endian header length][UTF-8 JSON header][zero pad to 3-byte boundary]
    [payload (shard_len bytes)][zero padding to N*N*3]

PNG is written and read by hand, one scanline at a time, using zlib at level 0
(stored blocks, filter type 0). Streaming functions never hold a whole tile in
memory; the in-memory functions (pack_payload, read_tile, ...) remain for tests.
"""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path
from typing import BinaryIO

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _write_chunk(f, ctype: bytes, data: bytes) -> None:
    f.write(struct.pack(">I", len(data)))
    f.write(ctype)
    f.write(data)
    f.write(struct.pack(">I", zlib.crc32(data, zlib.crc32(ctype)) & 0xFFFFFFFF))


# --- raw layout helpers

def tile_prefix(header: dict) -> bytes:
    """Length-prefixed JSON header, padded to a 3-byte boundary."""
    hj = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    prefix = struct.pack(">I", len(hj)) + hj
    return prefix + b"\x00" * ((-len(prefix)) % 3)


def pack_payload(header: dict, payload: bytes, side: int) -> bytes:
    """Build the full raw pixel buffer (side*side*3 bytes) in memory."""
    if header.get("shard_len") != len(payload):
        raise ValueError("header shard_len must equal payload length")
    capacity = side * side * 3
    prefix = tile_prefix(header)
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


class _RawSource:
    """Produces the raw pixel stream (prefix + payload + zeros) on demand.

    The payload is read from a file object and zero-padded if the file is
    shorter than payload_len (e.g. a short last chunk padded for erasure coding).
    """

    def __init__(self, prefix: bytes, payload_f: BinaryIO, payload_len: int, capacity: int):
        self.prefix = prefix
        self.payload_f = payload_f
        self.payload_end = len(prefix) + payload_len
        self.capacity = capacity
        self.pos = 0

    def read(self, n: int) -> bytes:
        out = bytearray()
        while len(out) < n and self.pos < self.capacity:
            need = n - len(out)
            if self.pos < len(self.prefix):
                take = min(need, len(self.prefix) - self.pos)
                out += self.prefix[self.pos : self.pos + take]
            elif self.pos < self.payload_end:
                take = min(need, self.payload_end - self.pos)
                piece = self.payload_f.read(take)
                if len(piece) < take:
                    piece += b"\x00" * (take - len(piece))
                out += piece
            else:
                take = min(need, self.capacity - self.pos)
                out += b"\x00" * take
            self.pos += take
        if len(out) < n:
            out += b"\x00" * (n - len(out))
        return bytes(out)


# --- PNG writing

def _write_png(path: Path, side: int, next_row) -> None:
    """Write a side x side RGB PNG. next_row() returns the next side*3 raw bytes."""
    compressor = zlib.compressobj(0)
    with open(path, "wb") as f:
        f.write(PNG_SIGNATURE)
        _write_chunk(f, b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
        for _ in range(side):
            out = compressor.compress(b"\x00" + next_row())
            if out:
                _write_chunk(f, b"IDAT", out)
        tail = compressor.flush()
        if tail:
            _write_chunk(f, b"IDAT", tail)
        _write_chunk(f, b"IEND", b"")


def write_tile(path: Path, side: int, raw: bytes) -> None:
    """Write an in-memory raw buffer as a PNG (tests and small inputs)."""
    row_len = side * 3
    if len(raw) != side * row_len:
        raise ValueError("raw length does not match side*side*3")
    pos = [0]

    def next_row() -> bytes:
        start = pos[0]
        pos[0] += row_len
        return raw[start : start + row_len]

    _write_png(path, side, next_row)


def write_tile_stream(path: Path, side: int, header: dict, payload_f: BinaryIO, payload_len: int) -> None:
    """Write a tile by streaming the header and payload from a file object.

    Peak memory is one scanline (side*3 bytes) plus zlib's small internal state.
    """
    if header.get("shard_len") != payload_len:
        raise ValueError("header shard_len must equal payload_len")
    capacity = side * side * 3
    prefix = tile_prefix(header)
    if len(prefix) + payload_len > capacity:
        raise ValueError(
            f"tile too small: need {len(prefix) + payload_len} bytes, capacity {capacity}"
        )
    row_len = side * 3
    src = _RawSource(prefix, payload_f, payload_len, capacity)
    _write_png(path, side, lambda: src.read(row_len))


# --- PNG reading

def _parse_chunks(data: bytes, path):
    """Yield (ctype, body) for each PNG chunk in an in-memory file. CRC-checked."""
    pos = 8
    while pos < len(data):
        (n,) = struct.unpack(">I", data[pos : pos + 4])
        ctype = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + n]
        (crc,) = struct.unpack(">I", data[pos + 8 + n : pos + 12 + n])
        if zlib.crc32(body, zlib.crc32(ctype)) & 0xFFFFFFFF != crc:
            raise ValueError(f"{path}: CRC mismatch in {ctype!r} chunk")
        pos += 12 + n
        yield ctype, body
        if ctype == b"IEND":
            return


def _check_ihdr(body: bytes, path) -> int:
    w, h, bd, ct, _cm, _fm, il = struct.unpack(">IIBBBBB", body)
    if w != h or bd != 8 or ct != 2 or il != 0:
        raise ValueError(f"{path}: unsupported PNG format")
    return w


def read_tile(path: Path) -> tuple[int, bytes]:
    """Read a whole tile into memory. Returns (side, raw RGB bytes)."""
    data = Path(path).read_bytes()
    if data[:8] != PNG_SIGNATURE:
        raise ValueError(f"{path} is not a PNG file")
    side = None
    idat: list[bytes] = []
    for ctype, body in _parse_chunks(data, path):
        if ctype == b"IHDR":
            side = _check_ihdr(body, path)
        elif ctype == b"IDAT":
            idat.append(body)
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


class _HeaderSink:
    """Receives raw pixel bytes in order; parses the header, then writes the payload."""

    def __init__(self, out: BinaryIO):
        self.out = out
        self.buf = bytearray()
        self.hl = None
        self.header = None
        self.shard_len = None
        self.written = 0

    def feed(self, data: bytes) -> None:
        if self.header is None:
            self.buf += data
            if self.hl is None and len(self.buf) >= 4:
                (self.hl,) = struct.unpack(">I", bytes(self.buf[:4]))
            if self.hl is not None:
                region = 4 + self.hl
                region += (-region) % 3
                if len(self.buf) >= region:
                    self.header = json.loads(bytes(self.buf[4 : 4 + self.hl]).decode("utf-8"))
                    self.shard_len = self.header["shard_len"]
                    rest = bytes(self.buf[region:])
                    self.buf = None
                    self._payload(rest)
        else:
            self._payload(data)

    def _payload(self, data: bytes) -> None:
        take = min(len(data), self.shard_len - self.written)
        if take > 0:
            self.out.write(data[:take])
            self.written += take

    @property
    def complete(self) -> bool:
        return self.header is not None and self.written == self.shard_len


def read_tile_stream(path: Path, payload_out: BinaryIO) -> dict:
    """Stream a tile: write its payload to `payload_out`, return its header.

    Reads the PNG chunk by chunk and inflates IDAT data incrementally, one
    scanline at a time, so the whole decoded image is never held in memory.
    """
    path = Path(path)
    sink = _HeaderSink(payload_out)
    with open(path, "rb") as f:
        if f.read(8) != PNG_SIGNATURE:
            raise ValueError(f"{path} is not a PNG file")
        side = None
        inflater = zlib.decompressobj()
        pending = bytearray()
        while True:
            head = f.read(8)
            if len(head) < 8:
                raise ValueError(f"{path}: truncated PNG")
            (n,) = struct.unpack(">I", head[:4])
            ctype = head[4:8]
            body = f.read(n)
            crc_bytes = f.read(4)
            if len(body) != n or len(crc_bytes) != 4:
                raise ValueError(f"{path}: truncated PNG chunk")
            (crc,) = struct.unpack(">I", crc_bytes)
            if zlib.crc32(body, zlib.crc32(ctype)) & 0xFFFFFFFF != crc:
                raise ValueError(f"{path}: CRC mismatch in {ctype!r} chunk")
            if ctype == b"IHDR":
                side = _check_ihdr(body, path)
            elif ctype == b"IDAT":
                if side is None:
                    raise ValueError(f"{path}: IDAT before IHDR")
                pending += inflater.decompress(body)
                _consume_rows(pending, side, sink, path)
            elif ctype == b"IEND":
                break
        pending += inflater.flush()
        _consume_rows(pending, side, sink, path)
    if side is None or pending:
        raise ValueError(f"{path}: malformed pixel data")
    if not sink.complete:
        raise ValueError(f"{path}: payload incomplete")
    return sink.header


def _consume_rows(pending: bytearray, side: int, sink: _HeaderSink, path) -> None:
    row_len = side * 3
    step = row_len + 1
    off = 0
    while len(pending) - off >= step:
        if pending[off] != 0:
            raise ValueError(f"{path}: unsupported PNG filter type")
        sink.feed(bytes(pending[off + 1 : off + step]))
        off += step
    del pending[:off]
