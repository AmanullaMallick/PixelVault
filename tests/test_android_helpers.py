import unittest

from android.helpers import (
    describe_plan, format_bytes, lit_cells, overall_progress, stage_label, tile_estimate,
)
from core.config import Config
from core.vault import parity_warning_for_size


class TestAndroidHelpers(unittest.TestCase):
    def test_overall_progress_is_monotonic_across_encode_stages(self):
        steps = [("archive", 1, 1), ("encrypt", 1, 2), ("encrypt", 2, 2),
                 ("parity", 1, 2), ("write_tiles", 1, 4), ("write_tiles", 4, 4)]
        values = [overall_progress(*s) for s in steps]
        self.assertEqual(values, sorted(values))
        self.assertEqual(values[-1], 1.0)

    def test_chunk_and_encrypt_share_a_slot(self):
        self.assertEqual(overall_progress("chunk", 1, 2), overall_progress("encrypt", 1, 2))

    def test_restore_stages(self):
        self.assertEqual(overall_progress("read_tiles", 0, 5), 0.0)
        self.assertEqual(overall_progress("decrypt", 3, 3), 1.0)

    def test_unknown_stage_and_zero_total(self):
        self.assertEqual(overall_progress("mystery", 1, 1), 0.0)
        self.assertEqual(overall_progress("write_tiles", 5, 0), 0.75)

    def test_lit_cells_clamps(self):
        self.assertEqual(lit_cells(-1, 72), 0)
        self.assertEqual(lit_cells(0.5, 72), 36)
        self.assertEqual(lit_cells(2, 72), 72)

    def test_format_bytes(self):
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(1536), "1.5 KB")
        self.assertEqual(format_bytes(5 * 1024 * 1024), "5.0 MB")

    def test_tile_estimate_matches_core_warning(self):
        cfg = Config(rs_parity_percent=10)
        total = 9 * 1024 * 1024
        k, m = tile_estimate(total, cfg.chunk_size, 10)
        self.assertEqual((k, m), (3, cfg.parity_count(3)))
        self.assertIn(f"{m} extra", parity_warning_for_size(total, cfg))

    def test_no_parity_gives_zero_recovery(self):
        self.assertEqual(tile_estimate(1, 4 * 1024 * 1024, 0), (1, 0))
        self.assertNotIn("recovery", describe_plan(1, 100, 4 * 1024 * 1024, 0))

    def test_describe_plan_plurals(self):
        text = describe_plan(2, 10 * 1024 * 1024, 4 * 1024 * 1024, 10)
        self.assertEqual(text, "2 items, 10.0 MB. About 3 tiles + 1 recovery.")

    def test_stage_label_fallback(self):
        self.assertEqual(stage_label("write_tiles"), "Writing tiles")
        self.assertEqual(stage_label("new_stage"), "New stage")


if __name__ == "__main__":
    unittest.main()
