#!/usr/bin/env python3
"""Install or disable a per-user macOS launchd maintenance job."""
import argparse
import json
import math
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

LABEL = "io.github.noahyao1024.mac-idle-maintenance"


def launchctl(*args, missing_ok=False):
    result = subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True)
    if result.returncode and not missing_ok:
        raise RuntimeError(result.stderr.strip() or f"launchctl failed: {args}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable-cleanup", action="store_true", help="Schedule actual cleanup; default is preview only")
    parser.add_argument("--interval-hours", type=float, default=6)
    parser.add_argument("--no-start", action="store_true", help="Write files without loading the scheduled job")
    parser.add_argument("--uninstall", action="store_true", help="Disable the job and remove its plist; retain config, script and logs")
    args = parser.parse_args()
    if sys.platform != "darwin" or os.geteuid() == 0:
        parser.error("Use macOS as your normal user, without sudo")
    if not math.isfinite(args.interval_hours) or args.interval_hours < 1:
        parser.error("--interval-hours must be a finite number of at least 1")
    home = Path.home()
    base = home / "Library/Application Support/MacIdleMaintenance"
    plist_path = home / "Library/LaunchAgents" / f"{LABEL}.plist"
    domain = f"gui/{os.getuid()}"
    if args.uninstall:
        launchctl("bootout", f"{domain}/{LABEL}", missing_ok=True)
        plist_path.unlink(missing_ok=True)
        print("Scheduled job disabled. Script, configuration and logs were retained.")
        return
    source = Path(__file__).resolve().parent
    python = shutil.which("python3") or sys.executable
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ["mac-maintenance.py", "README.md", "README.zh-CN.md", "config.example.json", "LICENSE"]:
        if (source / name).resolve() != (base / name).resolve():
            shutil.copy2(source / name, base / name)
    (base / "mac-maintenance.py").chmod(0o700)
    config = base / "config.json"
    if not config.exists():
        config.write_text((source / "config.example.json").read_text())
        config.chmod(0o600)
    # Check that a pre-existing user configuration is parseable; never overwrite it.
    json.loads(config.read_text())
    argv = [python, str(base / "mac-maintenance.py")]
    if args.enable_cleanup:
        argv.append("--apply")
    job = {
        "Label": LABEL,
        "ProgramArguments": argv,
        "RunAtLoad": True,
        "StartInterval": int(args.interval_hours * 3600),
        "ProcessType": "Background",
        "Nice": 10,
        "LowPriorityIO": True,
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }
    # Do not unload an existing job when merely preparing files for review.
    if not args.no_start:
        launchctl("bootout", f"{domain}/{LABEL}", missing_ok=True)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_bytes(plistlib.dumps(job))
    plist_path.chmod(0o600)
    if not args.no_start:
        launchctl("bootstrap", domain, str(plist_path))
    print(f"Installed: {base}")
    print(f"Mode: {'cleanup enabled' if args.enable_cleanup else 'preview only'}")
    print(f"Interval: {args.interval_hours:g} hours; run at login when loaded")
    print(f"Job {'not loaded (--no-start)' if args.no_start else 'loaded'}: {plist_path}")


if __name__ == "__main__":
    main()
