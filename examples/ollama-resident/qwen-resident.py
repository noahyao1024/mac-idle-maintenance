#!/usr/bin/env python3
"""Preload M4's Qwen after login or an Ollama restart; exit after each check."""
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import urllib.request

MODEL = "qwen3.5:9b"
BASE = Path.home() / "Library/Application Support/M4Ollama"
LOG = logging.getLogger("qwen-resident")


def local_api(path, body=None):
    request = urllib.request.Request(
        "http://127.0.0.1:11434" + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    # Loopback checks must not use the user's HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=120) as response:
        return json.load(response)


def ensure_resident():
    models = local_api("/api/ps").get("models", [])
    if any(model.get("name") == MODEL for model in models):
        return
    if models:
        LOG.info("Preload deferred: another model is loaded; no model was evicted")
        return
    local_api("/api/generate", {
        "model": MODEL, "stream": False, "keep_alive": -1,
        "options": {"num_ctx": 32768},
    })
    models = local_api("/api/ps").get("models", [])
    if not any(model.get("name") == MODEL for model in models):
        raise RuntimeError("Qwen was not loaded after preloading")
    LOG.info("Preloaded %s with keep_alive=-1 and context=32768", MODEL)


def main():
    BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_dir = Path.home() / "Library/Logs/M4Ollama"
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[RotatingFileHandler(log_dir / "resident.log",
                                                      maxBytes=262144, backupCount=2)])
    with (BASE / "resident.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            ensure_resident()
        except Exception as error:
            LOG.warning("Resident check failed; next scheduled check will retry: %s", error)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
