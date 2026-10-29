import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from core import image_tiler as tiler
from core import vault
from core.config import Config


def _small_cfg(**overrides) -> Config:
    base = dict(canvas_side=64, chunk_size=4096, rs_parity_percent=50, encryption_enabled=True)
    base.update(overrides)
    return Config(**base)


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.payload = os.urandom(20_000)
        self.src = self.root / "secret.bin"
        self.src.write_bytes(self.payload)

    def tearDown(self):
        self._tmp.cleanup()

    def _parity_m(self, tile: Path) -> int:
        _side, raw = tiler.read_tile(tile)
        header, _ = tiler.unpack_payload(raw)
        return header["m"]

    def test_encrypted_roundtrip_after_losing_m_tiles(self):
        cfg = _small_cfg()
        tiles = vault.encode_vault(self.src, self.root / "out", cfg, passphrase="correct horse")
        m = self._parity_m(tiles[0])
        self.assertGreaterEqual(m, 1)

        survivors = tiles[m:]  # drop m tiles; exactly k remain
        names, warnings = vault.decode_vault(survivors, self.root / "restored", passphrase="correct horse")

        self.assertEqual(names, ["secret.bin"])
        self.assertEqual((self.root / "restored" / "secret.bin").read_bytes(), self.payload)
        self.assertEqual(len(warnings), 1)  # exactly k survived -> informational notice

    def test_full_set_restores_without_warning(self):
        cfg = _small_cfg()
        tiles = vault.encode_vault(self.src, self.root / "out", cfg, passphrase="pw")
        _names, warnings = vault.decode_vault(tiles, self.root / "restored", passphrase="pw")
        self.assertEqual(warnings, [])
        self.assertEqual((self.root / "restored" / "secret.bin").read_bytes(), self.payload)

    def test_wrong_passphrase_is_rejected(self):
        cfg = _small_cfg()
        tiles = vault.encode_vault(self.src, self.root / "out", cfg, passphrase="right")
        with self.assertRaises(ValueError):
            vault.decode_vault(tiles, self.root / "restored", passphrase="wrong")

    def test_unencrypted_no_parity_needs_every_tile(self):
        cfg = _small_cfg(encryption_enabled=False, rs_parity_percent=0)
        tiles = vault.encode_vault(self.src, self.root / "out", cfg)
        with self.assertRaises(ValueError):
            vault.decode_vault(tiles[:-1], self.root / "partial")
        _names, warnings = vault.decode_vault(tiles, self.root / "restored")
        self.assertEqual(warnings, [])
        self.assertEqual((self.root / "restored" / "secret.bin").read_bytes(), self.payload)

    def test_tiles_are_valid_png_readable_by_pillow(self):
        cfg = _small_cfg()
        tiles = vault.encode_vault(self.src, self.root / "out", cfg, passphrase="pw")
        side, raw = tiler.read_tile(tiles[0])
        img = Image.open(tiles[0])
        img.load()
        self.assertEqual(img.size, (side, side))
        self.assertEqual(img.convert("RGB").tobytes(), raw)


if __name__ == "__main__":
    unittest.main()
