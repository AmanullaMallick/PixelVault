import io
import unittest

from tui.progress import ProgressState, format_bar, format_eta, make_callback


class TestProgress(unittest.TestCase):
    def test_bar_bounds_and_percentage(self):
        self.assertIn("  0.0%", format_bar(0.0))
        self.assertIn("100.0%", format_bar(1.0))
        self.assertIn("100.0%", format_bar(7.0))  # clamped
        self.assertEqual(format_bar(0.5, width=10).count("#"), 5)

    def test_eta_format(self):
        self.assertEqual(format_eta(None), "--:--")
        self.assertEqual(format_eta(75.9), "01:15")

    def test_eta_from_elapsed_rate(self):
        state = ProgressState()
        state.update("encrypt", 0, 10, now=0.0)
        state.update("encrypt", 5, 10, now=10.0)  # 5 items in 10 s
        self.assertAlmostEqual(state.eta(now=10.0), 10.0)

    def test_stage_change_resets_timer(self):
        state = ProgressState()
        state.update("encrypt", 9, 10, now=0.0)
        state.update("parity", 0, 10, now=50.0)
        self.assertIsNone(state.eta(now=50.0))  # nothing done in the new stage yet

    def test_callback_writes_line_and_newline_on_completion(self):
        buf = io.StringIO()
        cb = make_callback(buf)
        cb("encrypt", 1, 2)
        cb("encrypt", 2, 2)
        out = buf.getvalue()
        self.assertIn("Encrypting", out)
        self.assertTrue(out.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
