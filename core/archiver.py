"""Tar packing and unpacking, read in fixed-size chunks.

The tar is written to a temporary file on disk, not held in memory, and read
back in chunk_size pieces. Extraction uses tarfile's "data" filter, which
rejects absolute paths and path traversal in archive members.
"""

from __future__ import annotations

import tarfile
from pathlib import Path
from typing import BinaryIO, Iterator


def pack(src: Path, fileobj: BinaryIO) -> None:
    """Write a tar of `src` (file or directory) into `fileobj`."""
    src = Path(src)
    with tarfile.open(fileobj=fileobj, mode="w", format=tarfile.PAX_FORMAT) as tar:
        tar.add(src, arcname=src.name)


def iter_chunks(fileobj: BinaryIO, size: int) -> Iterator[bytes]:
    """Yield successive `size`-byte pieces of a file object."""
    while True:
        block = fileobj.read(size)
        if not block:
            return
        yield block


def list_names(fileobj: BinaryIO) -> list[str]:
    """Member names of a tar stream, without extracting anything."""
    with tarfile.open(fileobj=fileobj, mode="r:") as tar:
        return tar.getnames()


def extract(fileobj: BinaryIO, dest: Path) -> list[str]:
    """Extract a tar stream into `dest`. Returns member names."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=fileobj, mode="r:") as tar:
        names = tar.getnames()
        tar.extractall(dest, filter="data")
    return names
