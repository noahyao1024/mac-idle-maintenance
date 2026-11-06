import contextlib
import importlib.util
import io
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("installer", Path(__file__).with_name("install.py"))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def run_install(self, home, args):
        with mock.patch.object(Path, "home", return_value=home), \
                mock.patch.object(installer.sys, "platform", "darwin"), \
                mock.patch.object(installer.os, "geteuid", return_value=501), \
                mock.patch.object(installer.sys, "argv", ["install.py", *args]), \
                mock.patch.object(installer, "launchctl") as launchctl, \
                contextlib.redirect_stdout(io.StringIO()):
            installer.main()
        return launchctl

    def test_no_start_preview_and_preserved_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            launchctl = self.run_install(home, ["--no-start"])
            launchctl.assert_not_called()
            base = home / "Library/Application Support/MacIdleMaintenance"
            plist_path = home / "Library/LaunchAgents" / f"{installer.LABEL}.plist"
            job = plistlib.loads(plist_path.read_bytes())
            self.assertNotIn("--apply", job["ProgramArguments"])
            self.assertEqual(job["StartInterval"], 21600)
            config = base / "config.json"
            custom = {"minimum_idle_minutes": 45}
            config.write_text(json.dumps(custom))
            self.run_install(home, ["--no-start", "--enable-cleanup", "--interval-hours", "12"])
            self.assertEqual(json.loads(config.read_text()), custom)
            job = plistlib.loads(plist_path.read_bytes())
            self.assertIn("--apply", job["ProgramArguments"])
            self.assertEqual(job["StartInterval"], 43200)
            self.assertTrue((base / "mac-maintenance.py").is_file())

    def test_uninstall_retains_user_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.run_install(home, ["--no-start"])
            self.run_install(home, ["--uninstall"])
            self.assertFalse((home / "Library/LaunchAgents" / f"{installer.LABEL}.plist").exists())
            self.assertTrue((home / "Library/Application Support/MacIdleMaintenance/config.json").exists())


if __name__ == "__main__":
    unittest.main()
