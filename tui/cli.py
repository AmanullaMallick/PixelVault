"""Terminal front end (stdlib only) with a live progress bar.

    python -m tui.cli encode <file-or-folder> <out-dir> [--no-encrypt] [--profile redmi|default] [--yes]
    python -m tui.cli decode <folder | tile.png ...> --out <dest-dir> [--vault ID]

Passphrases are read with getpass, never from the command line.
Each encoded vault goes into its own subfolder of <out-dir>.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from core import image_tiler as tiler
from core import vault
from core.config import Config, load_settings
from tui.progress import make_callback

ROOT_CONFIG = Path(__file__).resolve().parent.parent / "config.json"


def _config(args):
    """Settings from config.json, with --profile overriding the file's profile."""
    cfg, default_folder, profile = load_settings(ROOT_CONFIG)
    if args.profile and args.profile != profile:
        cfg = Config.redmi_note_9() if args.profile == "redmi" else Config()
    if args.no_encrypt:
        cfg.encryption_enabled = False
    return cfg, default_folder


def _confirm(question: str) -> bool:
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def cmd_encode(args) -> int:
    cfg, default_folder = _config(args)
    out_dir = args.out_dir
    if out_dir is None:
        if default_folder and _confirm(f"Use your default folder ({default_folder})?"):
            out_dir = default_folder
        else:
            print("No output folder given. Pass one, or set default_output_folder in config.json.", file=sys.stderr)
            return 1
    warning = vault.export_warning(Path(args.source), cfg)
    if warning and not args.yes:
        print(f"Notice: {warning}", file=sys.stderr)
        if not _confirm("Continue with parity?"):
            print("Cancelled.", file=sys.stderr)
            return 1

    passphrase = None
    if cfg.encryption_enabled:
        passphrase = getpass.getpass("Passphrase: ")
        if passphrase != getpass.getpass("Repeat passphrase: "):
            print("Passphrases do not match.", file=sys.stderr)
            return 2
    tiles = vault.encode_vault(
        Path(args.source), Path(out_dir), cfg,
        passphrase=passphrase, progress=make_callback(),
    )
    print(f"Wrote {len(tiles)} tiles to {tiles[0].parent}")
    return 0


def _resolve_tiles(args) -> list[Path]:
    if len(args.tiles) == 1 and Path(args.tiles[0]).is_dir():
        groups = vault.scan_folder(Path(args.tiles[0]))
        if not groups:
            raise ValueError("no PixelVault tiles found in that folder")
        if args.vault:
            matches = [fid for fid in groups if fid.startswith(args.vault)]
            if len(matches) != 1:
                raise ValueError(f"--vault '{args.vault}' matches {len(matches)} vaults")
            return groups[matches[0]]
        if len(groups) > 1:
            lines = [f"  {fid[:8]}  ({len(ts)} tiles)" for fid, ts in groups.items()]
            raise ValueError("folder holds several vaults; choose one with --vault:\n" + "\n".join(lines))
        return next(iter(groups.values()))
    return [Path(t) for t in args.tiles]


def cmd_decode(args) -> int:
    tiles = _resolve_tiles(args)
    header = tiler.read_tile_stream(tiles[0], vault._Discard())
    passphrase = getpass.getpass("Passphrase: ") if header["is_encrypted"] else None
    names, warnings = vault.decode_vault(tiles, Path(args.out), passphrase=passphrase, progress=make_callback())
    for w in warnings:
        print(f"Notice: {w}", file=sys.stderr)
    print(f"Restored {', '.join(names)} into {args.out}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="pixelvault")
    sub = parser.add_subparsers(dest="command", required=True)

    enc = sub.add_parser("encode", help="pack a file or folder into PNG tiles")
    enc.add_argument("source")
    enc.add_argument("out_dir", nargs="?", help="output folder; defaults to config.json's default_output_folder")
    enc.add_argument("--no-encrypt", action="store_true")
    enc.add_argument("--profile", choices=["default", "redmi"], default=None,
                     help="overrides the profile in config.json")
    enc.add_argument("--yes", action="store_true", help="skip the parity storage prompt")
    enc.set_defaults(func=cmd_encode)

    dec = sub.add_parser("decode", help="restore from tiles or a folder of tiles")
    dec.add_argument("tiles", nargs="+", help="a folder, or one or more tile files")
    dec.add_argument("--out", required=True)
    dec.add_argument("--vault", help="file_id prefix, when a folder holds several vaults")
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
