import json
import tempfile
import unittest
from pathlib import Path

from core.config import load_settings


class TestSettings(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def _write(self, data):
        p = self.dir / "config.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_missing_file_gives_defaults(self):
        cfg, folder, profile = load_settings(self.dir / "absent.json")
        self.assertEqual((cfg.canvas_side, folder, profile), (4984, None, "default"))

    def test_redmi_profile_applies_preset(self):
        cfg, _, profile = load_settings(self._write({"profile": "redmi"}))
        self.assertEqual((profile, cfg.workers), ("redmi", 4))

    def test_explicit_key_overrides_profile(self):
        cfg, folder, _ = load_settings(self._write({"profile": "redmi", "workers": 2,
                                                    "default_output_folder": "D:/vault"}))
        self.assertEqual(cfg.workers, 2)
        self.assertEqual(folder, "D:/vault")

    def test_unknown_key_is_rejected(self):
        with self.assertRaises(ValueError):
            load_settings(self._write({"canvs_side": 100}))

    def test_iteration_floor_still_enforced(self):
        with self.assertRaises(ValueError):
            load_settings(self._write({"pbkdf2_iterations": 1000}))


if __name__ == "__main__":
    unittest.main()


class TestSaveSettings(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "config.json"

    def test_save_merges_and_keeps_other_keys(self):
        from core.config import save_settings
        self.path.write_text(json.dumps({"profile": "redmi"}), encoding="utf-8")
        save_settings(self.path, {"rs_parity_percent": 0})
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data, {"profile": "redmi", "rs_parity_percent": 0})

    def test_invalid_value_is_not_written(self):
        from core.config import save_settings
        self.path.write_text(json.dumps({"profile": "default"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            save_settings(self.path, {"rs_parity_percent": 99})
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data, {"profile": "default"})
