"""
heating_tuner_store - file storage for the heating tuner
---------------------------------------------------------
episodes.jsonl is append-only: one JSON object per line, never rewritten, so
an interrupted write can at most lose the line being written. state.json is
small and written atomically (temp file + rename).

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
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


@pyscript_executor
def read_state(path):
    import json
    import os
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)
