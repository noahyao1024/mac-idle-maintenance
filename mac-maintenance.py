#!/usr/bin/env python3
"""Conservative macOS maintenance. Preview by default; --apply makes changes."""
import argparse
import datetime as dt
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import math

HOME = Path.home()
BASE = HOME / "Library/Application Support/MacIdleMaintenance"
DEFAULTS = {
    "simulator_age_days": 3,
    "memory_pressure_cleanup": False,
    "memory_pressure_simulator_age_hours": 24,
    "minimum_idle_minutes": 15,
    "npm_cache_max_gib": 8,
    "npm_cleanup_cooldown_days": 7,
    "derived_data_age_days": 30,
    "log_age_days": 30,
    "excluded_simulator_ids": [],
    "developer_dir": "",
}
LOG = logging.getLogger("maintenance")


def developer_environment(config):
    """Prefer an explicit Xcode, then the selected Xcode, then installed Xcodes."""
    candidates = [config.get("developer_dir"), os.environ.get("DEVELOPER_DIR")]
    try:
        candidates.append(command(["/usr/bin/xcode-select", "-p"]).strip())
    except (RuntimeError, OSError):
        pass
    candidates.extend(str(p / "Contents/Developer") for p in sorted(Path("/Applications").glob("Xcode*.app")))
    for candidate in candidates:
        if candidate and (Path(candidate) / "usr/bin/simctl").is_file():
            return dict(os.environ, DEVELOPER_DIR=str(candidate))
    return None


def validate_config(config):
    for key, default in DEFAULTS.items():
        value = config[key]
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise ValueError(f"Invalid boolean: {key}")
        elif isinstance(default, (int, float)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"Invalid positive finite threshold: {key}")
    exclusions = config["excluded_simulator_ids"]
    if not isinstance(exclusions, list) or any(not isinstance(value, str) for value in exclusions):
        raise ValueError("excluded_simulator_ids must be a list of device ID strings")
    if not isinstance(config["developer_dir"], str):
        raise ValueError("developer_dir must be a path string, or empty for auto-detection")


def command(args, timeout=60, env=None):
    result = subprocess.run(args, text=True, capture_output=True,
                            timeout=timeout, env=env, cwd=str(HOME))
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed ({result.returncode}): {result.stderr[-500:]}")
    return result.stdout


def parse_date(value):
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def simulator_candidates(devices, now, days, exclusions):
    candidates = []
    for group in devices.values():
        for device in group:
            used = parse_date(device.get("lastUsedAt"))
            if (device.get("state") == "Booted" and used is not None
                    and device.get("udid") not in exclusions
                    and now - used >= days * 86400):
                candidates.append(device)
    return candidates


def idle_minutes():
    output = command(["/usr/sbin/ioreg", "-c", "IOHIDSystem", "-d", "4"])
    match = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', output)
    return int(match[1]) / 60e9 if match else None


def process_guards():
    # Do not log complete argv: it can contain credentials.
    processes = command(["/bin/ps", "-axo", "comm=,args="])
    developer_busy = any(re.search(pattern, processes, re.M) for pattern in [
        r"/Xcode\.app/Contents/MacOS/Xcode(?:\s|$)",
        r"/Simulator\.app/Contents/MacOS/Simulator(?:\s|$)",
        r"/(?:xcodebuild|xctest|swift-frontend|XCBBuildService)(?:\s|$)",
        r"[^\s/]+\.xctrunner/",
    ])
    package_busy = bool(re.search(
        r"(?:^|[/\s])(?:npm|npx|pnpm|yarn)(?:\s|$)|npm-cli\.js|pnpm\.cjs",
        processes, re.M))
    return developer_busy, package_busy


def read_memory_pressure():
    """Darwin reports normal=1, warning=2, critical=4; unknown fails closed."""
    try:
        level = int(command(["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"]))
        return level if level in {1, 2, 4} else None
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
        return None


def parse_memory_stats(vm_stats, system_stats):
    page_size = re.search(r"page size of (\d+) bytes", vm_stats)
    physical = re.search(r"hw.memsize:\s*(\d+)", system_stats)
    swap = re.search(r"used\s*=\s*([\d.]+)([BKMGT])", system_stats)
    if not page_size or not physical or not swap:
        raise ValueError("Incomplete memory statistics")
    pages = dict((name.strip(), int(count)) for name, count in
                 re.findall(r"^([^:\n]+):\s*(\d+)\.", vm_stats, re.M))
    page_mib = int(page_size[1]) / 2**20
    return {
        "physical_mib": int(physical[1]) / 2**20,
        "free_mib": pages["Pages free"] * page_mib,
        "inactive_mib": pages["Pages inactive"] * page_mib,
        "wired_mib": pages["Pages wired down"] * page_mib,
        "compressed_mib": pages["Pages occupied by compressor"] * page_mib,
        "swap_used_mib": float(swap[1]) * 1024**"BKMGT".index(swap[2]) / 2**20,
    }


def report_memory(stage):
    """Read-only diagnostics run even when activity guards skip cleanup."""
    try:
        stats = parse_memory_stats(command(["/usr/bin/vm_stat"]), command([
            "/usr/sbin/sysctl", "hw.memsize", "vm.swapusage"]))
        pressure = {1: "normal", 2: "warning", 4: "critical"}.get(read_memory_pressure(), "unknown")
        LOG.info("Memory %s: pressure=%s %s", stage, pressure,
                 " ".join(f"{key}={value:.1f}" for key, value in stats.items()))
        # RSS includes shared pages, and is not a prediction of reclaimed RAM.
        # Executable names only: never read or log process arguments.
        processes = command(["/bin/ps", "-axo", "rss=,comm="])
        largest = []
        for line in processes.splitlines():
            fields = line.strip().split(None, 1)
            if len(fields) == 2 and fields[0].isdigit():
                largest.append((int(fields[0]), Path(fields[1]).name))
        LOG.info("Largest process RSS (MiB; shared pages included): %s", json.dumps([
            {"executable": name, "rss_mib": round(rss / 1024, 1)}
            for rss, name in sorted(largest, reverse=True)[:5]], ensure_ascii=False))
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        LOG.warning("Memory %s diagnostics unavailable: %s", stage, error)


def simulator_cleanup_age(config, pressure):
    if config["memory_pressure_cleanup"] and pressure in {2, 4}:
        return min(config["simulator_age_days"], config["memory_pressure_simulator_age_hours"] / 24)
    return config["simulator_age_days"]


def allocated_bytes(path):
    if not path.exists() or path.is_symlink():
        return 0
    # du reports allocated storage, not inflated sparse-file logical sizes.
    return int(command(["/usr/bin/du", "-sk", str(path)], timeout=180).split()[0]) * 1024


def newest_mtime(path):
    newest = path.lstat().st_mtime
    for root, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(root) / name).is_symlink()]
        for name in dirs + files:
            item = Path(root) / name
            if not item.is_symlink():
                newest = max(newest, item.lstat().st_mtime)
    return newest


def safe_direct_child(path, parent):
    return (not parent.is_symlink() and not path.is_symlink()
            and path.parent == parent and path.resolve().parent == parent.resolve())


def maintain_simulators(config, apply, now):
    env = developer_environment(config)
    if env is None:
        LOG.info("Skip simulators: full Xcode is not installed or simctl is unavailable")
        return
    devices = json.loads(command(["/usr/bin/xcrun", "simctl", "list", "devices", "--json"],
                                 timeout=120, env=env))["devices"]
    age_days = simulator_cleanup_age(config, read_memory_pressure())
    LOG.info("Simulator minimum age: %.1f hours (memory pressure cleanup=%s)",
             age_days * 24, config["memory_pressure_cleanup"])
    candidates = simulator_candidates(devices, now, age_days,
                                      config["excluded_simulator_ids"])
    for device in candidates:
        LOG.info("%s shutdown simulator: %s [%s], lastUsedAt=%s",
                 "APPLY" if apply else "PLAN", device["name"], device["udid"], device["lastUsedAt"])
        if apply:
            # Recheck activity and device state immediately before each shutdown.
            idle = idle_minutes()
            if process_guards()[0] or idle is None or idle < config["minimum_idle_minutes"]:
                LOG.info("Stop simulator cleanup: developer activity detected")
                break
            current = json.loads(command(["/usr/bin/xcrun", "simctl", "list", "devices", "--json"],
                                         timeout=120, env=env))["devices"]
            # Recover the normal age threshold if memory pressure has subsided.
            current_age = simulator_cleanup_age(config, read_memory_pressure())
            eligible = simulator_candidates(current, time.time(), current_age,
                                             config["excluded_simulator_ids"])
            if any(d["udid"] == device["udid"] for d in eligible):
                command(["/usr/bin/xcrun", "simctl", "shutdown", device["udid"]],
                        timeout=120, env=env)
    LOG.info("Eligible old booted simulators: %d (device data is retained)", len(candidates))


def maintain_npm(config, apply, now, state):
    cache_root = HOME / ".npm"
    cache = cache_root / "_cacache"
    if cache_root.is_symlink() or cache.is_symlink():
        LOG.info("Skip symlinked npm cache")
        return
    size = allocated_bytes(cache)
    LOG.info("npm download cache: %.2f GiB", size / 2**30)
    if size <= config["npm_cache_max_gib"] * 2**30:
        return
    if now - state.get("npm_cleaned_at", 0) < config["npm_cleanup_cooldown_days"] * 86400:
        LOG.info("Skip npm cache: cleanup cooldown")
        return
    if process_guards()[1]:
        LOG.info("Skip npm cache: package manager is running")
        return
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("npm is not available on PATH")
    LOG.info("%s clear npm download cache only: %s", "APPLY" if apply else "PLAN", cache)
    if apply:
        command([npm, "cache", "clean", "--force", "--ignore-scripts", "--cache", str(cache_root)],
                timeout=900)
        state["npm_cleaned_at"] = time.time()


def maintain_build_cache(config, apply, now):
    parent = HOME / "Library/Developer/Xcode/DerivedData"
    if not parent.is_dir() or parent.is_symlink():
        return
    cutoff = now - config["derived_data_age_days"] * 86400
    for path in sorted(parent.iterdir()):
        if not path.is_dir() or not safe_direct_child(path, parent):
            continue
        if newest_mtime(path) >= cutoff:
            continue
        size = allocated_bytes(path)
        LOG.info("%s delete stale generated build cache: %s (%.1f MiB)",
                 "APPLY" if apply else "PLAN", path.name, size / 2**20)
        if apply:
            if process_guards()[0]:
                LOG.info("Stop build-cache cleanup: developer activity detected")
                break
            shutil.rmtree(path)


def maintain_logs(config, apply, now):
    cutoff = now - config["log_age_days"] * 86400
    roots = [HOME / ".npm/_logs", HOME / "Library/Logs/CoreSimulator"]
    count = total = 0
    for parent in roots:
        if not parent.is_dir() or parent.is_symlink():
            continue
        for root, dirs, files in os.walk(parent, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
            for name in files:
                path = Path(root) / name
                if path.is_symlink() or not path.is_file() or path.suffix not in {".log", ".gz"}:
                    continue
                stat = path.stat()
                if stat.st_mtime >= cutoff:
                    continue
                count += 1
                total += stat.st_blocks * 512
                if apply:
                    path.unlink()
    LOG.info("%s old npm/simulator logs: %d files, %.1f MiB", "Removed" if apply else "PLAN",
             count, total / 2**20)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply the previewed maintenance rules")
    parser.add_argument("--config", type=Path, default=BASE / "config.json")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This maintenance command supports macOS only")
    if os.geteuid() == 0:
        parser.error("Run as your normal macOS user, without sudo")
    BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_dir = HOME / "Library/Logs/MacIdleMaintenance"
    log_dir.mkdir(parents=True, exist_ok=True)
    LOG.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for handler in [logging.StreamHandler(sys.stdout),
                    RotatingFileHandler(log_dir / "maintenance.log", maxBytes=2**20, backupCount=4)]:
        handler.setFormatter(formatter)
        LOG.addHandler(handler)
    with (BASE / "maintenance.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            LOG.info("Another maintenance run is active; skipping")
            return 0
        config = dict(DEFAULTS)
        if args.config.exists():
            config.update(json.loads(args.config.read_text()))
        validate_config(config)
        idle = idle_minutes()
        developer_busy, _ = process_guards()
        LOG.info("Mode=%s idle_minutes=%s developer_busy=%s free_disk=%.2f GiB",
                 "APPLY" if args.apply else "PREVIEW", round(idle, 1) if idle is not None else None,
                 developer_busy, shutil.disk_usage(HOME).free / 2**30)
        report_memory("before")
        if args.apply and (idle is None or idle < config["minimum_idle_minutes"]):
            LOG.info("Skip changes: computer is active or idle status is unknown")
            return 0
        state_path = BASE / "state.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        failures = 0
        jobs = [("simulators", lambda: maintain_simulators(config, args.apply, time.time())),
                ("npm cache", lambda: maintain_npm(config, args.apply, time.time(), state)),
                ("build cache", lambda: maintain_build_cache(config, args.apply, time.time())),
                ("logs", lambda: maintain_logs(config, args.apply, time.time()))]
        for name, job in jobs:
            if developer_busy and name in {"simulators", "build cache", "logs"}:
                LOG.info("Skip %s: Xcode, Simulator UI, or a build/test is running", name)
                continue
            try:
                if args.apply:
                    idle = idle_minutes()
                    if idle is None or idle < config["minimum_idle_minutes"]:
                        LOG.info("Stop maintenance: user activity resumed")
                        break
                job()
            except Exception:
                failures += 1
                LOG.exception("Maintenance step failed: %s", name)
        if args.apply:
            temp = state_path.with_suffix(".tmp")
            temp.write_text(json.dumps(state, indent=2) + "\n")
            temp.replace(state_path)
        if args.apply:
            report_memory("after")
        LOG.info("Finished: free_disk=%.2f GiB failures=%d", shutil.disk_usage(HOME).free / 2**30, failures)
        return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
