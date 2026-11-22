# Optional Qwen resident setup

This example records the Qwen 9B setup used alongside idle maintenance on a
16 GiB Mac. It is installed separately from `install.py`. The maintenance
command itself never calls Ollama or unloads models.

## Ollama service defaults

Install `qwen3.5:9b` in your existing Ollama installation first. Merge the values
from [ollama.env.example](ollama.env.example) into
`~/.homebrew/services/ollama.env`, retaining any other settings. With a Homebrew
version supporting per-service environment overrides (`brew services --help`),
these values are included whenever it generates the service definition.

```sh
chmod 600 "$HOME/.homebrew/services/ollama.env"
brew services restart ollama
```

Restarting interrupts current Ollama requests. The defaults keep one loaded
model with one parallel request, a 32768-token context, Flash Attention and
q8_0 KV cache. Request-level `keep_alive` and `num_ctx` options still follow
Ollama's API semantics. These defaults do not preload a model by themselves.

## Preload at login and after service restarts

[qwen-resident.py](qwen-resident.py) checks the loopback Ollama API and exits.
If Qwen is already loaded it leaves it running; if another model is loaded it
defers. With no loaded models it preloads Qwen using `keep_alive=-1` and 32768
context. The script never downloads a model. A lock prevents overlapping checks.

From this example directory, install the script and render the launchd template:

```sh
python3 - <<'PY'
from pathlib import Path
import plistlib
import shutil

user_root = Path.home()
destination = user_root / "Library/Application Support/M4Ollama"
destination.mkdir(parents=True, exist_ok=True, mode=0o700)
script = destination / "qwen-resident.py"
shutil.copy2("qwen-resident.py", script)
script.chmod(0o700)

job = plistlib.loads(Path("qwen-resident.plist.in").read_bytes())
job["ProgramArguments"][1] = str(script)
target = user_root / "Library/LaunchAgents/me.noah.qwen-resident.plist"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_bytes(plistlib.dumps(job))
target.chmod(0o600)
PY

# Run once in the foreground and verify the loaded model.
python3 "$HOME/Library/Application Support/M4Ollama/qwen-resident.py"
ollama ps

# Load when this job is not already registered.
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/me.noah.qwen-resident.plist"
```

The job runs at login and every five minutes, while logged in. If Ollama is
unavailable or preloading fails, it records an error and retries at the next
scheduled check. It has no continuously running helper process. Logs rotate at
`~/Library/Logs/M4Ollama/resident.log`, retaining roughly 768 KiB.

Temporarily stop the checks with:

```sh
launchctl bootout "gui/$(id -u)/me.noah.qwen-resident"
```

Move its plist out of `~/Library/LaunchAgents/` to disable it across future
logins. Ollama is managed separately by Homebrew.

## Optional llmhub node restriction

Merge [node-overrides.example.json](node-overrides.example.json) into your
existing llmhub node configuration, retaining the existing endpoint and token
reference. This fragment is not a complete node configuration. Restart the node
to advertise only `qwen3.5:9b`, so remote clients cannot evict it by selecting a
different installed model. `OLLAMA_MAX_LOADED_MODELS=1` also limits the backend
to one loaded model. Local native clients can still select another model.

## Memory maintenance settings

For hourly maintenance, run the repository's installer with
`--enable-cleanup --interval-hours 1`. In its installed `config.json`, set
`memory_pressure_cleanup` to `true` and `memory_pressure_simulator_age_hours`
to `24`. Put simulators used by unattended workflows in
`excluded_simulator_ids`. The regular idle/activity protections continue to
apply, and the resident model is retained. See the repository README for the
full maintenance rules.
