"""
autotune - apply the PID advice automatically, with guardrails
--------------------------------------------------------------
Plain Python, no Home Assistant. Only used when the option "Apply PID advice
automatically" is on; without it the advice stays a suggestion. runner.py
reads the PID numbers, calls decide() after each finished warm-up and writes
the value it returns.

Guardrails (see docs/experimental.md, "Applying the advice automatically"):
- one parameter per change, the advice's own 10% step, inside limits();
- a change is only made when the episodes the advice is based on all ran on
  the current values, so the last change has had its own warm-ups;
- after a change, EPISODES_AFTER valid warm-ups of the followed room in the
  same outdoor band, flow limit and schema decide: the problem the advice
  acted on must be smaller on average, or the change is reverted;
- a reverted change is never made again;
- values changed by hand (or by anything else) end the check without a revert:
  the user took over.
"""

import math

from . import tuner_core

EPISODES_AFTER = 2  # valid warm-ups on the new values before a change is judged
LOG_KEEP = 50       # entries kept in the change log

# What must get smaller for a change to stay, per verdict
METRICS = {
    "overshoot": ("overshoot",),
    "oscillating": ("flame_starts_per_hour", "undershoot"),
    "slow": ("rise_min",),
}


def same(a, b):
    """Two PID values equal within float noise (ESPHome numbers are float32)."""
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=1e-4, abs_tol=1e-9)


def same_params(a, b):
    if not a or not b:
        return False
    return all(same(a.get(k), b.get(k)) for k in ("kp", "ki", "kd"))


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def means(episodes, verdict):
    return {key: _mean([e.get(key) for e in episodes]) for key in METRICS[verdict]}


def improved(verdict, before, after):
    """True when one of the verdict's metrics went down and none went up."""
    down = up = False
    for key in METRICS[verdict]:
        b, a = before.get(key), after.get(key)
        if b is None or a is None:
            continue
        down = down or a < b
        up = up or a > b
    return down and not up


def usable(rec):
    """Advice that actually moves a value."""
    return bool(rec) and not rec.get("outside_range") and not rec.get("at_limit") and not same(rec["old"], rec["new"])


def _after(history, pending, room_id, room_name):
    return [
        e for e in history
        if e.get("start", "") > pending["at"]
        and tuner_core.judged_valid(e, room_id, room_name)
        and same_params(e.get("params"), pending["params_after"])
        and e["outdoor_band"] == pending["band"]
        and e.get("max_ch") == pending["max_ch"]
        and tuner_core.schema_of(e) == pending["schema"]
    ]


def _entry(kind, at, param, old, new, why):
    return {"at": at, "action": kind, "param": param, "old": old, "new": new, "why": why}


def decide(history, params, rec, state, now, *, room_id=None, room_name=None):
    """
    What to do after a finished warm-up.

    history: all episode records, params: the PID values now, rec: the advice
    from tuner_core.recommend (rounded to the number's step by the caller),
    state: the stored autotune state ({} at first), now: ISO time.
    Returns (action, new_state). action is None or
    {"action": "apply" | "revert", "param", "value", "old", "why"}; new_state
    assumes the write succeeds (the caller keeps the old state if it fails).
    """
    state = {
        "pending": state.get("pending"),
        "reverted": list(state.get("reverted", [])),
        "log": list(state.get("log", [])),
    }

    def done(action):
        state["log"] = state["log"][-LOG_KEEP:]
        return action, state

    pending = state["pending"]
    if pending:
        if not same_params(params, pending["params_after"]):
            state["pending"] = None
            state["log"].append(_entry(
                "dropped", now, pending["param"], pending["old"], pending["new"], "values changed outside the integration"
            ))
            return done(None)
        after = _after(history, pending, room_id, room_name)
        if len(after) < EPISODES_AFTER:
            return done(None)
        state["pending"] = None
        if improved(pending["verdict"], pending["before"], means(after, pending["verdict"])):
            state["log"].append(_entry("kept", now, pending["param"], pending["old"], pending["new"], "better after the change"))
        else:
            state["reverted"].append({"param": pending["param"], "old": pending["old"], "new": pending["new"]})
            state["log"].append(_entry("reverted", now, pending["param"], pending["new"], pending["old"], "not better after the change"))
            return done({
                "action": "revert", "param": pending["param"], "value": pending["old"],
                "old": pending["new"], "why": "not better after the change",
            })

    if not usable(rec):
        return done(None)
    if not all(same_params(p, params) for p in rec.get("group_params", [])):
        return done(None)  # the advice is (partly) based on older values
    for r in state["reverted"]:
        if r["param"] == rec["param"] and same(r["old"], rec["old"]) and same(r["new"], rec["new"]):
            return done(None)  # tried before and made it worse
    group = [e for e in history if tuner_core.judged_valid(e, room_id, room_name)]
    group = [
        e for e in group
        if e["outdoor_band"] == rec["band"] and e.get("max_ch") == rec["max_ch"]
        and tuner_core.schema_of(e) == rec["schema"]
    ][-tuner_core.CONSISTENT_EPISODES:]
    state["pending"] = {
        "param": rec["param"], "old": rec["old"], "new": rec["new"], "verdict": rec["verdict"],
        "params_after": {**params, rec["param"]: rec["new"]},
        "band": rec["band"], "max_ch": rec["max_ch"], "schema": rec["schema"],
        "before": means(group, rec["verdict"]), "at": now,
    }
    state["log"].append(_entry("applied", now, rec["param"], rec["old"], rec["new"], rec["why"]))
    return done({"action": "apply", "param": rec["param"], "value": rec["new"], "old": rec["old"], "why": rec["why"]})


def is_reverted(state, rec):
    return usable(rec) and any(
        r["param"] == rec["param"] and same(r["old"], rec["old"]) and same(r["new"], rec["new"])
        for r in (state or {}).get("reverted", [])
    )


def status_line(state, history, room_id=None, room_name=None):
    """The check in progress, for the report; None when nothing is pending."""
    pending = (state or {}).get("pending")
    if not pending:
        return None
    n = len(_after(history, pending, room_id, room_name))
    return "- Checking {} {} -> {} (since {}): {} of {} warm-ups in band {}".format(
        pending["param"], pending["old"], pending["new"], pending["at"][:16].replace("T", " "),
        min(n, EPISODES_AFTER), EPISODES_AFTER, pending["band"],
    )


def log_lines(state, day):
    """Changes made, kept, reverted or dropped on day (YYYY-MM-DD)."""
    return [
        "- {} {}: {} {} -> {} ({})".format(
            e["at"][11:16], e["action"], e["param"], e["old"], e["new"], e["why"]
        )
        for e in (state or {}).get("log", []) if e["at"][:10] == day
    ]
