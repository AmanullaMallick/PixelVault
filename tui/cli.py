"""Terminal front end (stdlib only) with a live progress bar.

    python -m tui.cli encode <file-or-folder> <out-dir> [--no-encrypt] [--profile redmi|default]
    python -m tui.cli decode <tile> [<tile> ...] --out <dest-dir>

The passphrase is read with getpass, never from the command line.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from core import image_tiler as tiler
from core import vault
from core.config import Config
from tui.progress import make_callback


def _config(args) -> Config:
    cfg = Config.redmi_note_9() if args.profile == "redmi" else Config()
    if args.no_encrypt:
        cfg.encryption_enabled = False  # parity setting is kept as configured
    return cfg


def cmd_encode(args) -> int:
    cfg = _config(args)
    passphrase = None
    if cfg.encryption_enabled:
        passphrase = getpass.getpass("Passphrase: ")
        if passphrase != getpass.getpass("Repeat passphrase: "):
            print("Passphrases do not match.", file=sys.stderr)
            return 2
    tiles = vault.encode_vault(
        Path(args.source), Path(args.out_dir), cfg,
        passphrase=passphrase, progress=make_callback(),
    )
    print(f"Wrote {len(tiles)} tiles to {args.out_dir}")
    return 0


class _Discard:
    def write(self, data: bytes) -> int:
        return len(data)


def cmd_decode(args) -> int:
    # Ask for a passphrase only if the vault is encrypted (read from the first tile's header).
    header = tiler.read_tile_stream(Path(args.tiles[0]), _Discard())
    passphrase = None
    if header["is_encrypted"]:
        passphrase = getpass.getpass("Passphrase: ")
    names, warnings = vault.decode_vault(
        [Path(t) for t in args.tiles], Path(args.out),
        passphrase=passphrase, progress=make_callback(),
    )
    for w in warnings:
        print(f"Notice: {w}", file=sys.stderr)
    print(f"Restored {', '.join(names)} into {args.out}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="pixelvault")
    sub = parser.add_subparsers(dest="command", required=True)

    enc = sub.add_parser("encode", help="pack a file or folder into PNG tiles")
    enc.add_argument("source")
    enc.add_argument("out_dir")
    enc.add_argument("--no-encrypt", action="store_true")
    enc.add_argument("--profile", choices=["default", "redmi"], default="default")
    enc.set_defaults(func=cmd_encode)

    dec = sub.add_parser("decode", help="restore from tiles")
    dec.add_argument("tiles", nargs="+")
    dec.add_argument("--out", required=True)
    dec.set_defaults(func=cmd_decode)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
