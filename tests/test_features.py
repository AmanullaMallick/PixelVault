import argparse
import os
import tempfile
import unittest
from pathlib import Path

from core import vault
from core.config import Config
from tui import cli


def _cfg(**kw):
    base = dict(canvas_side=64, chunk_size=8192, rs_parity_percent=10,
                encryption_enabled=False, workers=2, ecc_block_bytes=1000)
    base.update(kw)
    return Config(**base)


class TestFeatures(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.src = self.root / "a.bin"
        self.src.write_bytes(os.urandom(20_000))

    def test_export_warning_only_when_parity_adds_images(self):
        self.assertIsNotNone(vault.export_warning(self.src, _cfg(rs_parity_percent=10)))
        self.assertIsNone(vault.export_warning(self.src, _cfg(rs_parity_percent=0)))

    def test_each_encode_gets_its_own_subfolder_and_no_overwrite(self):
        out = self.root / "out"
        t1 = vault.encode_vault(self.src, out, _cfg())
        t2 = vault.encode_vault(self.src, out, _cfg())
        self.assertNotEqual(t1[0].parent, t2[0].parent)
        self.assertEqual(t1[0].parent.parent, out)

    def test_decode_refuses_to_overwrite_existing_files(self):
        tiles = vault.encode_vault(self.src, self.root / "out", _cfg())
        dest = self.root / "restore"
        vault.decode_vault(tiles, dest)
        with self.assertRaises(ValueError):
            vault.decode_vault(tiles, dest)

    def test_folder_scan_separates_two_vaults_by_header_not_name(self):
        other = self.root / "b.bin"
        other.write_bytes(os.urandom(9_000))
        a = vault.encode_vault(self.src, self.root / "outA", _cfg())
        b = vault.encode_vault(other, self.root / "outB", _cfg())
        mixed = self.root / "mixed"
        mixed.mkdir()
        for i, p in enumerate(a + b):
            (mixed / f"whatever_{i}.png").write_bytes(p.read_bytes())
        groups = vault.scan_folder(mixed)
        self.assertEqual(len(groups), 2)
        self.assertEqual(sorted(len(v) for v in groups.values()), sorted([len(a), len(b)]))

    def test_cli_requires_vault_choice_when_folder_has_two_vaults(self):
        other = self.root / "b.bin"
        other.write_bytes(os.urandom(9_000))
        a = vault.encode_vault(self.src, self.root / "outA", _cfg())
        b = vault.encode_vault(other, self.root / "outB", _cfg())
        mixed = self.root / "mixed"
        mixed.mkdir()
        for i, p in enumerate(a + b):
            (mixed / f"t{i}.png").write_bytes(p.read_bytes())
        args = argparse.Namespace(tiles=[str(mixed)], vault=None)
        with self.assertRaises(ValueError):
            cli._resolve_tiles(args)
        fid = vault.scan_folder(mixed)
        pick = next(iter(fid))
        args.vault = pick[:8]
        self.assertEqual(len(cli._resolve_tiles(args)), len(fid[pick]))


if __name__ == "__main__":
    unittest.main()
