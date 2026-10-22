"""
heating_tuner_store - file storage for the heating tuner
---------------------------------------------------------
episodes.jsonl is append-only: one JSON object per line, never rewritten, so
an interrupted write can at most lose the line being written. state.json is
small and written atomically (temp file + rename).

pyscript runs triggers concurrently, so two writers can overlap. Each write
uses its own temp file, and update_state does read-change-write under a
file lock, so one trigger can never undo another trigger's change.

Wrapped in @pyscript_executor because bare open() is blocked in pyscript's
interpreted code.
"""


@pyscript_executor
def append_episode(path, episode):
    import json
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(episode, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


@pyscript_executor
def read_episodes(path, last_n=200):
    import json
    import os
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
                # A torn last line after a crash; skip it.
                continue
    return episodes[-last_n:]


@pyscript_executor
def write_state(path, state):
    import json
    import os
    import tempfile
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


@pyscript_executor
def update_state(path, changes):
    """Merge `changes` into the state file under a lock; returns the state before."""
    import fcntl
    import json
    import os
    import tempfile
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    with open(path + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                state = json.load(f)
        before = dict(state)
        state.update(changes)
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
    return before


@pyscript_executor
def read_state(path):
    import json
    import os
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)
