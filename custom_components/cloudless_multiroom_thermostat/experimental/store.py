"""File storage for the experimental features (blocking: call from an executor).

*.jsonl files are append-only: one JSON object per line, never rewritten, so
an interrupted write can at most lose the line being written. State files
are small and written atomically (temp file + rename).
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any


def append_episode(path: str, episode: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(episode, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_episodes(path: str, last_n: int = 200) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []
    episodes = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                episodes.append(json.loads(line))
            except ValueError:
                continue  # a torn last line after a crash
    return episodes[-last_n:]


def write_state(path: str, state: dict[str, Any]) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    # Unique temp name: two writers can never share (and rename away) one file
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=os.path.basename(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def read_state(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)
