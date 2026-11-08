import importlib.util
import contextlib
import io
import json
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
        for value in ["true", 1, None]:
            with self.subTest(memory_pressure_cleanup=value), self.assertRaises(ValueError):
                maintenance.validate_config(dict(maintenance.DEFAULTS, memory_pressure_cleanup=value))
        for value in [0, -1, True, float("nan")]:
            with self.subTest(memory_pressure_simulator_age_hours=value), self.assertRaises(ValueError):
                maintenance.validate_config(dict(maintenance.DEFAULTS, memory_pressure_simulator_age_hours=value))

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


class MemoryTests(unittest.TestCase):
    def test_memory_stats_use_reported_page_size_and_swap_unit(self):
        for page_size in [4096, 16384]:
            for size, expected in [("1536.50M", 1536.5), ("1.50G", 1536), ("1024K", 1)]:
                with self.subTest(page_size=page_size, swap=size):
                    vm_stats = (f"Mach Virtual Memory Statistics: (page size of {page_size} bytes)\n"
                                "Pages free: 256.\nPages inactive: 128.\nPages wired down: 512.\n"
                                "Pages occupied by compressor: 64.\n")
                    system_stats = f"hw.memsize: 17179869184\nvm.swapusage: total = 16384M used = {size} free = 1M"
                    result = maintenance.parse_memory_stats(vm_stats, system_stats)
                    self.assertEqual(result["physical_mib"], 16384)
                    self.assertEqual(result["free_mib"], 256 * page_size / 2**20)
                    self.assertEqual(result["compressed_mib"], 64 * page_size / 2**20)
                    self.assertEqual(result["swap_used_mib"], expected)
        with self.assertRaises(ValueError):
            maintenance.parse_memory_stats("unavailable", "unavailable")

    def test_pressure_cleanup_requires_opt_in_and_known_pressure(self):
        config = dict(maintenance.DEFAULTS)
        self.assertEqual(maintenance.simulator_cleanup_age(config, 4), 3)
        config["memory_pressure_cleanup"] = True
        for pressure in [None, 0, 1, 3, 8]:
            self.assertEqual(maintenance.simulator_cleanup_age(config, pressure), 3)
        for pressure in [2, 4]:
            self.assertEqual(maintenance.simulator_cleanup_age(config, pressure), 1)
        config["simulator_age_days"] = 0.5
        self.assertEqual(maintenance.simulator_cleanup_age(config, 2), 0.5)

    def test_missing_or_unrecognized_pressure_is_unknown(self):
        for output in ["unexpected", "8"]:
            with mock.patch.object(maintenance, "command", return_value=output):
                self.assertIsNone(maintenance.read_memory_pressure())
        with mock.patch.object(maintenance, "command", side_effect=RuntimeError("unsupported")):
            self.assertIsNone(maintenance.read_memory_pressure())

    def run_simulator_cleanup(self, pressure, idle=60, developer_busy=False, apply=True):
        now = maintenance.parse_date("2026-11-08T12:00:00Z")
        devices = {"runtime": [
            {"udid": "old", "name": "old", "state": "Booted", "lastUsedAt": "2026-11-06T12:00:00Z"},
            {"udid": "recent", "name": "recent", "state": "Booted", "lastUsedAt": "2026-11-08T06:00:00Z"},
            {"udid": "protected", "name": "protected", "state": "Booted", "lastUsedAt": "2026-11-01T12:00:00Z"},
        ]}
        config = dict(maintenance.DEFAULTS, memory_pressure_cleanup=True, excluded_simulator_ids=["protected"])
        with mock.patch.object(maintenance, "developer_environment", return_value={}), \
                mock.patch.object(maintenance, "read_memory_pressure", side_effect=pressure), \
                mock.patch.object(maintenance, "idle_minutes", return_value=idle), \
                mock.patch.object(maintenance, "process_guards", return_value=(developer_busy, False)), \
                mock.patch.object(maintenance.time, "time", return_value=now), \
                mock.patch.object(maintenance, "command", return_value=json.dumps({"devices": devices})) as command:
            maintenance.maintain_simulators(config, apply, now)
        return [call.args[0] for call in command.call_args_list if "shutdown" in call.args[0]]

    def test_pressure_reclaims_only_old_unprotected_simulator(self):
        self.assertEqual(self.run_simulator_cleanup([2, 2]),
                         [["/usr/bin/xcrun", "simctl", "shutdown", "old"]])

    def test_no_shutdown_after_pressure_recovers_or_activity_resumes(self):
        self.assertEqual(self.run_simulator_cleanup([2, 1]), [])
        self.assertEqual(self.run_simulator_cleanup([2], idle=0), [])
        self.assertEqual(self.run_simulator_cleanup([2], developer_busy=True), [])
        self.assertEqual(self.run_simulator_cleanup([2], apply=False), [])

    def test_active_apply_still_reports_memory_without_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with mock.patch.object(maintenance, "HOME", home), \
                    mock.patch.object(maintenance, "BASE", home / "maintenance"), \
                    mock.patch.object(maintenance.sys, "platform", "darwin"), \
                    mock.patch.object(maintenance.os, "geteuid", return_value=501), \
                    mock.patch.object(maintenance.sys, "argv", ["mac-maintenance.py", "--apply"]), \
                    mock.patch.object(maintenance, "idle_minutes", return_value=0), \
                    mock.patch.object(maintenance, "process_guards", return_value=(False, False)), \
                    mock.patch.object(maintenance, "report_memory") as report, \
                    mock.patch.object(maintenance, "maintain_simulators") as cleanup, \
                    contextlib.redirect_stdout(io.StringIO()):
                try:
                    self.assertEqual(maintenance.main(), 0)
                    report.assert_called_once_with("before")
                    cleanup.assert_not_called()
                    self.assertFalse((home / "maintenance/state.json").exists())
                finally:
                    for handler in maintenance.LOG.handlers[:]:
                        handler.close()
                        maintenance.LOG.removeHandler(handler)


if __name__ == "__main__":
    unittest.main()
