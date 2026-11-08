# mac-idle-maintenance

Dependency-free maintenance for a developer's Mac: shut down old booted iOS
simulators and reclaim regenerable caches while the computer is idle.
Preview by default. No sudo or memory purging.

[中文说明](README.zh-CN.md) · [MIT license](LICENSE)

## Default rules

| Maintenance | Rule |
| --- | --- |
| Old booted simulators | Shut down when CoreSimulator's `lastUsedAt` is at least 3 days old |
| npm download cache | Clear `~/.npm/_cacache` above 8 GiB, at most once every 7 days |
| Xcode DerivedData | Remove direct child directories with nothing modified in 30 days |
| Old logs | Remove `.log` and `.gz` files older than 30 days under npm and CoreSimulator logs |
| Memory diagnostics | Log native memory pressure, physical/free/inactive/wired/compressed memory, swap used, and the five largest process RSS values |
| Memory pressure cleanup (opt-in) | At warning/critical pressure, allow shutdown of booted simulators unused for at least 24 hours |

Changes require **15 minutes without keyboard/mouse activity**. Unknown idle
state means no changes. Simulator/build/log cleanup skips recognized Xcode,
Simulator UI, build and test processes. npm cleanup skips recognized running
npm/npx/pnpm/yarn processes.

Simulator device data, runtime images, Xcode Archives, source code, installed
packages, downloads, LLM models, and swap files are retained. Ollama, OpenClaw,
and Multica are not stopped. Cleared caches must be rebuilt or downloaded again.

## Requirements

- macOS, a normal logged-in user, and **Python 3.9+** (`python3 --version`).
- Full Xcode for simulator maintenance, auto-detected. Other steps work without
  Xcode. Set `developer_dir` for a nonstandard installation.
- npm on PATH only when its download cache needs clearing.

## Quick start

```sh
git clone https://github.com/noahyao1024/mac-idle-maintenance.git
cd mac-idle-maintenance

# Preview: no shutdowns or deletions.
python3 mac-maintenance.py

# Apply once, subject to idle/activity guards.
python3 mac-maintenance.py --apply
```

## Schedule

```sh
# Preview-only job: login + every 6 hours.
python3 install.py

# Explicitly enable actual scheduled cleanup.
python3 install.py --enable-cleanup

# Optional interval, minimum 1 hour.
python3 install.py --enable-cleanup --interval-hours 12
```

The installer starts the job immediately unless `--no-start` is provided.
Existing configuration is preserved. Re-running without `--enable-cleanup`
returns the schedule to preview mode. Disable older copies of the maintenance
schedule before installing to avoid duplicate jobs.

| File | Location |
| --- | --- |
| Installed script and config | `~/Library/Application Support/MacIdleMaintenance/` |
| Rotating log, about 5 MiB maximum | `~/Library/Logs/MacIdleMaintenance/maintenance.log` |
| launchd job | `~/Library/LaunchAgents/io.github.noahyao1024.mac-idle-maintenance.plist` |

Disable the schedule, retaining the script, configuration, and logs:

```sh
python3 install.py --uninstall
```

This is a per-user LaunchAgent, not a system service. It runs while the user is
logged in, not while the Mac is off. Missed intervals do not guarantee immediate
execution after wake. A lock prevents overlapping runs.

## Configuration

Edit the installed `config.json`, using [config.example.json](config.example.json)
as a reference. Unspecified settings use defaults. For a one-off configuration:

```sh
python3 mac-maintenance.py --config /path/to/config.json
```

Add protected device UDIDs to `excluded_simulator_ids`. An empty `developer_dir`
selects Xcode automatically. Find device IDs with `xcrun simctl list devices`;
if needed, prefix it with `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer`.

### Memory monitoring and reclamation

Every check records memory diagnostics, even when user activity prevents cleanup.
Process diagnostics contain executable names and RSS only, without arguments.
RSS includes shared pages and is not an estimate of how much RAM a shutdown frees.

Set `"memory_pressure_cleanup": true` to reclaim memory from old booted simulators
sooner when macOS reports warning or critical pressure. The normal 3-day age is
then reduced to `memory_pressure_simulator_age_hours` (24 by default). This never
increases a shorter configured normal age. Idle, developer activity, exclusion,
and known-date guards still apply; pressure and eligibility are checked again
before each shutdown. Normal or unknown pressure uses the normal age threshold.
Swap use alone never triggers the shorter threshold. This is a scheduled check,
not a continuous pressure watcher; use a 1-hour schedule for more frequent checks.

Memory reclamation shuts down eligible simulators and retains their data. It
does not unload resident LLMs or stop other services. macOS manages compression
and swap; the tool does not run `purge` or delete swap files.

## Limits and safety

- `lastUsedAt` and keyboard/mouse idle time are heuristics, not proof that a
  simulator or remote workflow is inactive. Headless CI and unattended work may
  evade process detection. Exclude devices used by those workflows.
- DeviceHub being open alone does not block cleanup. Xcode and Simulator UI do.
- Known symlinks are skipped at cleanup targets; DerivedData deletion is limited
  to direct children. This utility is not a security boundary against another
  process changing those directories concurrently.
- Each step logs errors independently. Preview writes logs and a lock but does
  not shut down devices, delete caches, or update cleanup state.
- macOS manages memory compression and swap; freeing applications' memory does
  not guarantee an immediate reduction in swap-file size.
- Cache files are deleted, not moved to Trash. Review a preview before enabling
  cleanup. There is no telemetry or network reporting.

## Development

```sh
python3 -m unittest discover -v
```

Tests use temporary directories and mocks, never real caches, simulators, or
launchd jobs. Contributions improving portability, detection, and safety are welcome.
