"""File storage for the experimental features (blocking: call from an executor).

*.jsonl files are append-only: one JSON object per line, never rewritten, so
an interrupted write can at most lose the line being written. State files
are small and written atomically (temp file + rename).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from typing import Any

_LOGGER = logging.getLogger(__name__)


def append_episode(path: str, episode: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(episode, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_episodes(path: str, last_n: int | None = None) -> list[dict[str, Any]]:
    """All records in path, or the last last_n.

    The whole file is parsed either way, so there is no saving in a cap: a
    year of warm-ups is a few thousand lines (about 700 bytes each), parsed
    in tens of milliseconds, and the progress check needs that history.
    """
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
    return episodes if last_n is None else episodes[-last_n:]


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
    """The state in path, or {} when it is missing or unreadable.

    An unreadable file (not JSON, or not an object) is moved aside to
    <path>.corrupt-<time> so it can be looked at, and the feature starts over
    instead of failing on every read.
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
        if isinstance(state, dict):
            return state
        reason = f"holds a {type(state).__name__}, not an object"
    except (OSError, ValueError) as err:
        reason = str(err)
    aside = f"{path}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        os.replace(path, aside)
    except OSError:
        aside = "(could not be moved aside)"
    _LOGGER.warning("Unreadable %s (%s), starting over; old file: %s", path, reason, aside)
    return {}
