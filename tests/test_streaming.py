import io
import itertools
import os
import tempfile
import unittest
from pathlib import Path

from core import ecc_matrix as ecc
from core import image_tiler as tiler
from core import vault
from core.config import Config


class TestStreamingErasure(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_encode_files_matches_in_memory_encode(self):
        k, m, length = 4, 2, 10_001  # odd length exercises the final short block
        shards = [os.urandom(length) for _ in range(k)]
        data_paths = []
        for i, s in enumerate(shards):
            p = self.root / f"d{i}.bin"
            p.write_bytes(s)
            data_paths.append(p)
        parity_paths = [self.root / f"p{j}.bin" for j in range(m)]
        ecc.encode_files(data_paths, parity_paths, length, block=997)
        self.assertEqual([p.read_bytes() for p in parity_paths], ecc.encode(shards, m))

    def test_decode_files_recovers_from_every_k_subset(self):
        k, m, length = 3, 2, 4096
        shards = [os.urandom(length) for _ in range(k)]
        parity = ecc.encode(shards, m)
        everything = shards + parity
        for keep in itertools.combinations(range(k + m), k):
            survivors = {}
            for i in keep:
                p = self.root / f"s{i}.bin"
                p.write_bytes(everything[i])
                survivors[i] = p
            outs = [self.root / f"out{l}.bin" for l in range(k)]
            ecc.decode_files(survivors, outs, k, m, length, block=512)
            self.assertEqual([o.read_bytes() for o in outs], shards, f"keep={keep}")


class TestStreamingTiles(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_streamed_tile_is_byte_identical_to_in_memory_tile(self):
        side = 48
        payload = os.urandom(1000)
        header = {"shard_len": len(payload), "k": 1, "m": 0, "shard_index": 0}
        raw = tiler.pack_payload(header, payload, side)

        mem_path = self.root / "mem.png"
        tiler.write_tile(mem_path, side, raw)
        stream_path = self.root / "stream.png"
        with io.BytesIO(payload) as pf:
            tiler.write_tile_stream(stream_path, side, header, pf, len(payload))
        self.assertEqual(mem_path.read_bytes(), stream_path.read_bytes())

    def test_read_tile_stream_recovers_header_and_payload(self):
        side = 48
        payload = os.urandom(900)
        header = {"shard_len": len(payload), "k": 1, "m": 0, "shard_index": 0, "file_id": "abc"}
        path = self.root / "t.png"
        with io.BytesIO(payload) as pf:
            tiler.write_tile_stream(path, side, header, pf, len(payload))
        out = io.BytesIO()
        got_header = tiler.read_tile_stream(path, out)
        self.assertEqual(got_header, header)
        self.assertEqual(out.getvalue(), payload)

    def test_short_payload_is_zero_padded_to_shard_len(self):
        side = 48
        real = os.urandom(300)
        shard_len = 700
        header = {"shard_len": shard_len, "k": 1, "m": 0, "shard_index": 0}
        path = self.root / "pad.png"
        with io.BytesIO(real) as pf:
            tiler.write_tile_stream(path, side, header, pf, shard_len)
        out = io.BytesIO()
        tiler.read_tile_stream(path, out)
        self.assertEqual(out.getvalue(), real + b"\x00" * (shard_len - len(real)))


class TestProgressAndProfile(unittest.TestCase):
    def test_progress_reports_every_stage_to_completion(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        src = root / "in.bin"
        src.write_bytes(os.urandom(30_000))
        cfg = Config(canvas_side=64, chunk_size=8192, rs_parity_percent=50,
                     encryption_enabled=False, workers=3, ecc_block_bytes=1000)
        seen = {}

        def cb(stage, done, total):
            seen.setdefault(stage, []).append((done, total))

        tiles = vault.encode_vault(src, root / "out", cfg, progress=cb)
        self.assertEqual(seen["write_tiles"][-1], (len(tiles), len(tiles)))
        self.assertIn("parity", seen)
        vault.decode_vault(tiles, root / "back", cfg=cfg, progress=cb)
        self.assertIn("rebuild", seen)
        self.assertEqual(seen["decrypt"][-1][0], seen["decrypt"][-1][1])

    def test_redmi_note_9_profile_is_valid(self):
        cfg = Config.redmi_note_9()
        cfg.validate()
        self.assertEqual(cfg.workers, 4)
        self.assertEqual(cfg.chunk_size, 4 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
