import os
import tempfile
import unittest
from pathlib import Path

from core import image_tiler as tiler
from core import vault
from core.config import Config


class TestManifest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.src = self.root / "tax_records_2026.bin"
        self.src.write_bytes(os.urandom(12_000))

    def _cfg(self, **kw):
        base = dict(canvas_side=64, chunk_size=4096, rs_parity_percent=0,
                    encryption_enabled=True, workers=2)
        base.update(kw)
        return Config(**base)

    def test_filename_and_hash_absent_from_encrypted_tiles(self):
        tiles = vault.encode_vault(self.src, self.root / "out", self._cfg(), passphrase="pw")
        for t in tiles:
            _side, raw = tiler.read_tile(t)
            self.assertNotIn(b"tax_records_2026", raw)
            header, _ = tiler.unpack_payload(raw)
            self.assertNotIn("archive_sha256", str(header))
            self.assertNotIn("original_name", str(header))

    def test_chunk_lists_absent_from_plaintext_header(self):
        tiles = vault.encode_vault(self.src, self.root / "out", self._cfg(), passphrase="pw")
        for t in tiles:
            _side, raw = tiler.read_tile(t)
            header, _ = tiler.unpack_payload(raw)
            self.assertNotIn("ct_lengths", header)
            self.assertNotIn("nonces_hex", header)
            self.assertNotIn("manifest_plain", header)
            self.assertIn("manifest_ct_hex", header)

    def test_tampered_archive_hash_is_detected_when_unencrypted(self):
        tiles = vault.encode_vault(self.src, self.root / "out", self._cfg(encryption_enabled=False))
        # Rewrite every tile with a manifest whose hash is wrong.
        for t in tiles:
            side, raw = tiler.read_tile(t)
            h, payload = tiler.unpack_payload(raw)
            import json
            m = json.loads(h["manifest_plain"])
            m["archive_sha256"] = "0" * 64
            h["manifest_plain"] = json.dumps(m)
            tiler.write_tile(t, side, tiler.pack_payload(h, payload, side))
        with self.assertRaises(ValueError) as ctx:
            vault.decode_vault(tiles, self.root / "restore")
        self.assertIn("hash mismatch", str(ctx.exception))

    def test_encrypted_roundtrip_still_restores_and_name_is_recovered(self):
        tiles = vault.encode_vault(self.src, self.root / "out", self._cfg(), passphrase="pw")
        names, _ = vault.decode_vault(tiles, self.root / "restore", passphrase="pw")
        self.assertEqual(names, ["tax_records_2026.bin"])
        self.assertEqual((self.root / "restore" / names[0]).read_bytes(), self.src.read_bytes())


if __name__ == "__main__":
    unittest.main()
