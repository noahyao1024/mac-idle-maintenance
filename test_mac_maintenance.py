import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("maintenance", Path(__file__).with_name("mac-maintenance.py"))
maintenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maintenance)


class SafetyTests(unittest.TestCase):
    def test_config_rejects_unsafe_thresholds_and_malformed_exclusions(self):
        for value in [0, -1, True, float("nan"), float("inf"), "3"]:
            with self.subTest(value=value):
                config = dict(maintenance.DEFAULTS, simulator_age_days=value)
                with self.assertRaises(ValueError):
                    maintenance.validate_config(config)
        with self.assertRaises(ValueError):
            maintenance.validate_config(dict(maintenance.DEFAULTS, excluded_simulator_ids="all"))

    def test_missing_xcode_skips_simulator_step(self):
        with mock.patch.object(maintenance, "developer_environment", return_value=None), \
                mock.patch.object(maintenance, "command") as command:
            maintenance.maintain_simulators(maintenance.DEFAULTS, True, 0)
        command.assert_not_called()

    def test_only_old_booted_devices_with_known_dates_are_selected(self):
        now = maintenance.parse_date("2026-11-06T12:00:00Z")
        def device(name, date, state="Booted"):
            return {"udid": name, "lastUsedAt": date, "state": state}
        devices = {"runtime": [
            device("old", "2026-11-01T12:00:00Z"),
            device("recent", "2026-11-05T12:00:00Z"),
            device("unknown", None),
            device("invalid", "not-a-date"),
            device("naive", "2026-11-01T12:00:00"),
            device("future", "2026-12-01T12:00:00Z"),
            device("stopped", "2026-11-01T12:00:00Z", "Shutdown"),
            device("excluded", "2026-11-01T12:00:00Z"),
        ]}
        actual = maintenance.simulator_candidates(devices, now, 3, ["excluded"])
        self.assertEqual([d["udid"] for d in actual], ["old"])

    def test_delete_guard_rejects_symlink_and_nonchild(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "DerivedData"
            parent.mkdir()
            child = parent / "old"
            child.mkdir()
            outside = root / "project"
            outside.mkdir()
            link = parent / "link"
            link.symlink_to(outside, target_is_directory=True)
            self.assertTrue(maintenance.safe_direct_child(child, parent))
            self.assertFalse(maintenance.safe_direct_child(link, parent))
            self.assertFalse(maintenance.safe_direct_child(outside, parent))
            nested = child / "nested"
            nested.mkdir()
            self.assertFalse(maintenance.safe_direct_child(nested, parent))

    def test_recent_nested_file_keeps_build_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / "nested"
            child.mkdir()
            recent = child / "build.db"
            recent.write_text("active build")
            os.utime(recent, (500, 500))
            os.utime(child, (100, 100))
            os.utime(root, (100, 100))
            self.assertEqual(maintenance.newest_mtime(root), 500)


if __name__ == "__main__":
    unittest.main()
