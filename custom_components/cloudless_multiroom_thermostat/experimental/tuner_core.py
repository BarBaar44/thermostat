"""
tuner_core - warm-up episodes and PID advice
--------------------------------------------
Plain Python with no Home Assistant imports, so it is unit testable. All I/O
and HA access lives in runner.py and store.py.

An episode is a list of one-minute samples (dicts) taken while the boiler PID
works towards a target, from the moment demand appears until the watch after
reaching the target ends (see watch_end).
"""

# Schema 2: the watch after reaching target ends at the first reheat (or an
# earlier disturbance) instead of always running 90 minutes, so overshoot, dip
# and flame starts are measured differently. Records of different schemas are
# not compared with each other; records from before schema 1 carry no field.
# Schema 3: top-ups (kind) are kept out of the advice, and with thermostat.yaml
# max_ch is the flow limit the thermostat really asks for (before, a recorded
# 40 could have been a 50 degC limit).
SCHEMA = 3

POST_REACH_MIN = 90        # longest watch after the room first reaches target
MIN_WATCH_MIN = 20         # a watch cut short before this is not judged
MAX_RISE_MIN = 240         # give up on reaching target after this long
MIN_EPISODE_MIN = 30       # shorter episodes are not judged
DHW_MAX_MIN = 10           # more hot-water time than this disturbs the episode
MIN_RISE = 0.5             # degC below target at the start for a warm-up; less is a top-up

OVERSHOOT_LIMIT = 0.4      # degC above target
SLOW_RISE_MIN = 60         # minutes to reach target counted as slow
UNDERSHOOT_LIMIT = 0.4     # degC below target after the first reach
FLAME_STARTS_PER_HOUR = 6  # more than this is short-cycling
CONSISTENT_EPISODES = 2    # this many valid episodes in a row must agree
STEP = 0.10                # 10 % parameter step

# Only episodes led by the followed room (the "Room to follow" option, matched
# by room id) are used for tuning. Other rooms' episodes are still recorded
# (reason "other_room") so we can judge later whether they need parameters of
# their own. The class name "living" is what the records store for the
# followed room; it is kept so older records group with new ones.

# Advice limits for an ESPHome PID whose output spans a 0 to 100 degC flow
# range. The PID output is a fraction of that range, so the same flow response
# needs gains in inverse proportion to it: at a 40 degC limit every limit is
# 100 / 40 = 2.5 times higher (see limits()).
LIMITS = {"kp": (0.3, 1.5), "ki": (0.0005, 0.01), "kd": (0.0, 2000.0)}
LIMITS_RANGE = 100.0


def limits(name, max_ch=None):
    """(low, high) for a parameter at flow limit max_ch (None: LIMITS_RANGE)."""
    low, high = LIMITS[name]
    if not max_ch or max_ch <= 0:
        return low, high
    factor = LIMITS_RANGE / max_ch
    return round(low * factor, 6), round(high * factor, 6)

OUTDOOR_BANDS = ((0.0, "below_0"), (8.0, "0_8"), (15.0, "8_15"))


def outdoor_band(temp):
    if temp is None:
        return "unknown"
    for upper, name in OUTDOOR_BANDS:
        if temp < upper:
            return name
    return "above_15"


def schema_of(record):
    return record.get("schema", 1)


def watch_end(samples, reach_idx):
    """
    Where the watch after the room first reached target ends, and why:
    (index of the first sample left out, reason), or (None, None) while it
    goes on.

    - "reheat": the burner starts for heating again after it went off. What
      follows is the next heat, not this warm-up (a hot-water draw doesn't count).
    - "target_changed", "winner_changed", "garden_doors": a disturbance. The
      episode is judged on what came before it if the watch lasted at least
      MIN_WATCH_MIN, otherwise the disturbance makes it invalid.
    - "cap": POST_REACH_MIN minutes without any of these.
    """
    first = samples[0]
    cap = reach_idx + POST_REACH_MIN
    burner_off = False
    for i in range(reach_idx + 1, min(len(samples), cap)):
        s = samples[i]
        if s["target"] != first["target"]:
            return i, "target_changed"
        if s["area"] not in (first["area"], "Off"):
            return i, "winner_changed"
        if s["garden_paused"]:
            return i, "garden_doors"
        if s["dhw"]:
            continue
        if not s["flame"]:
            burner_off = True
        elif burner_off:
            return i, "reheat"
    if len(samples) >= cap:
        return cap, "cap"
    return None, None


def room_class(room_id, followed_room_id):
    """Stored with each record for older readers; advice and progress use is_room."""
    return "living" if room_id is not None and room_id == followed_room_id else "other"


def is_room(record, room_id, room_name):
    """True for a record of the room with room_id (the room followed now).

    Newer records carry room_id, which survives a rename. Older ones (and the
    copied pyscript history) only have the room name in area. Without a room
    to compare (room_id and room_name None) the stamped room_class decides.
    """
    if room_id is None and room_name is None:
        return record.get("room_class") == "living"
    if record.get("room_id") is not None:
        return record["room_id"] == room_id
    if record.get("area"):
        return record["area"].casefold() == (room_name or "").casefold()
    return record.get("room_class") == "living"


def episode_kind(record):
    """warm_up, or top_up when the room started less than MIN_RISE below target.

    A top-up is the burner reheating a room that sits just below target. On a
    boiler whose minimum output is well above the room's load one burn
    overshoots whatever the PID values are, so top-ups say little about them.
    Derived from target and start_temp, so older records are sorted too; a
    record without them counts as a warm-up.
    """
    target, start = record.get("target"), record.get("start_temp")
    if target is None or start is None:
        return "warm_up"
    return "warm_up" if round(target - start, 2) >= MIN_RISE else "top_up"


def for_advice(record, room_id=None, room_name=None):
    """A valid warm-up (not a top-up) of the room followed now: what the PID advice learns from."""
    return judged_valid(record, room_id, room_name) and episode_kind(record) == "warm_up"


def judged_valid(record, room_id=None, room_name=None):
    """A valid episode of the room followed now.

    "other_room" is left out: it is stamped when the episode is recorded, so
    an episode of a room that became the followed room later still counts.
    """
    reasons = record.get("invalid_reasons")
    ok = record.get("valid", False) if reasons is None else all(r == "other_room" for r in reasons)
    return ok and is_room(record, room_id, room_name)


def analyse(samples, params, start_reason, extra_reasons=(), *, room_id, followed_room_id):
    """Turn one episode's samples into a record for episodes.jsonl.

    room_id is the room that led the episode, followed_room_id the room the
    advice learns from.
    """
    first = samples[0]
    target = first["target"]
    reasons = list(extra_reasons)

    reach_idx = next(
        (i for i, s in enumerate(samples) if s["room"] is not None and s["room"] >= target), None
    )
    watch_min = watch_reason = None
    if reach_idx is not None:
        end, watch_reason = watch_end(samples, reach_idx)
        if end is not None:
            samples = samples[:end]
        else:
            watch_reason = "heating_stopped"  # the runner ended it: heating went off
        watch_min = len(samples) - reach_idx
        if watch_reason in ("target_changed", "winner_changed", "garden_doors") and watch_min < MIN_WATCH_MIN:
            reasons.append(watch_reason)
    after = samples[reach_idx:] if reach_idx is not None else []
    rooms_after = [s["room"] for s in after if s["room"] is not None]
    peak = max(rooms_after) if rooms_after else None
    peak_idx = rooms_after.index(peak) if rooms_after else None
    trough = min(rooms_after[peak_idx:]) if rooms_after else None

    flame_starts = len(
        [1 for a, b in zip(samples, samples[1:], strict=False) if b["flame"] and not a["flame"]]
    )
    hours = max(len(samples) / 60.0, 1 / 60.0)
    outdoors = [s["outdoor"] for s in samples if s["outdoor"] is not None]
    outdoor_avg = round(sum(outdoors) / len(outdoors), 1) if outdoors else None
    integrals = [s["integral"] for s in samples if s["integral"] is not None]
    gas = [s.get("gas") for s in samples if s.get("gas") is not None]
    # Return water while the burner runs for heating (not hot water): below
    # ~55 degC the condensing boiler recovers extra heat, the lower the better
    returns = [s.get("ret") for s in samples
               if s["flame"] and not s["dhw"] and s.get("ret") is not None]
    # Flow water for heating only: during a hot-water draw a combi boiler runs
    # its water much hotter (60+ degC), which says nothing about the warm-up
    waters = [s["water"] for s in samples if not s["dhw"] and s["water"] is not None]
    # The boiler's own flow limit (maximum CH setpoint): it caps what the PID
    # can ask for, so warm-ups with a different limit are not comparable
    caps = [s.get("max_ch") for s in samples if s.get("max_ch") is not None]

    if len(samples) < MIN_EPISODE_MIN:
        reasons.append("too_short")
    if room_id is None or room_id != followed_room_id:
        reasons.append("other_room")
    # "Off" is not another room: the winner turns Off as soon as the leading
    # room reaches its target, which every successful warm-up does
    if any([s["area"] not in (first["area"], "Off") for s in samples]):
        reasons.append("winner_changed")
    if any([s["target"] != target for s in samples]):
        reasons.append("target_changed")
    if any([s["garden_paused"] for s in samples]):
        reasons.append("garden_doors")
    if len([1 for s in samples if s["dhw"]]) > DHW_MAX_MIN:
        reasons.append("hot_water")
    if reach_idx is None:
        reasons.append("target_not_reached")
    if len(set(caps)) > 1:
        reasons.append("max_ch_changed")

    record = {
        "schema": SCHEMA,
        "watch_min": watch_min,
        "watch_end": watch_reason,
        "start": first["t"],
        "end": samples[-1]["t"],
        "start_reason": start_reason,
        "room_id": room_id,
        "area": first["area"],
        "room_class": room_class(room_id, followed_room_id),
        "target": target,
        "start_temp": first["room"],
        "outdoor_avg": outdoor_avg,
        "outdoor_band": outdoor_band(outdoor_avg),
        "rise_min": reach_idx,
        "overshoot": round(peak - target, 2) if peak is not None else None,
        "minutes_above_target": len([1 for r in rooms_after if r > target]),
        "undershoot": round(max(target - trough, 0.0), 2) if trough is not None else None,
        "flame_starts": flame_starts,
        "flame_starts_per_hour": round(flame_starts / hours, 1),
        "flame_on_min": len([1 for s in samples if s["flame"]]),
        "water_peak": max(waters, default=None),
        "max_ch": caps[0] if caps else None,
        "gas_used": round(gas[-1] - gas[0], 3) if gas else None,
        "return_avg": round(sum(returns) / len(returns), 1) if returns else None,
        "return_max": max(returns) if returns else None,
        "people_avg": _avg([s.get("people") for s in samples]),
        "integral_min": min(integrals, default=None),
        "integral_max": max(integrals, default=None),
        "params": params,
        "valid": not reasons,
        "invalid_reasons": reasons,
    }
    record["kind"] = episode_kind(record)
    return record


def _avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else None


def verdict(episode):
    """What a single valid episode (see judged_valid) asks for, or None when it is within limits."""
    if episode["overshoot"] is not None and episode["overshoot"] > OVERSHOOT_LIMIT:
        return "overshoot"
    if episode["flame_starts_per_hour"] > FLAME_STARTS_PER_HOUR or (
        episode["undershoot"] is not None and episode["undershoot"] > UNDERSHOOT_LIMIT
    ):
        return "oscillating"
    if episode["rise_min"] is not None and episode["rise_min"] > SLOW_RISE_MIN:
        return "slow"
    return None


ACTIONS = {
    "overshoot": ("ki", -STEP, f"overshoot above {OVERSHOOT_LIMIT:.1f} degC"),
    "oscillating": ("kp", -STEP, "swinging around target or short-cycling"),
    "slow": ("kp", +STEP, "slow warm-up without overshoot"),
}


def recommend(episodes, params, *, room_id=None, room_name=None):
    """
    Look at the most recent valid episodes of the followed room (room_id,
    room_name) in the latest one's group (outdoor band + boiler flow limit +
    schema).
    Recommend one change only when the last CONSISTENT_EPISODES of them
    agree. Returns None or a dict.
    """
    valid = [e for e in episodes if for_advice(e, room_id, room_name)]
    if not valid:
        return None
    last = valid[-1]
    group = [
        e for e in valid
        if e["outdoor_band"] == last["outdoor_band"]
        and e.get("max_ch") == last.get("max_ch")
        and schema_of(e) == schema_of(last)
    ][-CONSISTENT_EPISODES:]
    if len(group) < CONSISTENT_EPISODES:
        return None
    verdicts = {verdict(e) for e in group}
    if len(verdicts) != 1:
        return None
    v = verdicts.pop()
    if v is None:
        return None

    name, step, why = ACTIONS[v]
    low, high = limits(name, last.get("max_ch"))
    old = params[name]
    rec = {
        "verdict": v, "param": name, "old": old, "new": old, "why": why, "at_limit": False,
        # What the advice was based on, for autotune: the group and the PID
        # values its episodes ran on
        "band": last["outdoor_band"], "max_ch": last.get("max_ch"), "schema": schema_of(last),
        "group_params": [e.get("params") for e in group],
    }
    if not low <= old <= high:
        # Another PID scale than limits() knows: clamping into the range
        # could move the value against the verdict (overshoot -> raise Ki)
        return {**rec, "outside_range": True, "low": low, "high": high}
    new = round(min(max(old * (1 + step), low), high), 5)
    if new == old:
        return {**rec, "at_limit": True}
    return {**rec, "new": new}


def _or_dash(value):
    """A value for a report line, "-" when it is missing (sensor not set or no data)."""
    return "-" if value is None else value


def summary_line(e):
    """One human-readable line for the daily notification."""
    start = e["start"][11:16]
    if not e["valid"]:
        return "- {} {}: skipped ({})".format(start, e["area"], ", ".join(e["invalid_reasons"]))
    line = (
        "- {} {} -> {:.1f} degC (outside {} degC): target after {} min, "
        "overshoot {:+.1f}, dip {:.1f}, {} flame starts, water max {}, return avg {}".format(
            start, e["area"], e["target"], _or_dash(e["outdoor_avg"]), e["rise_min"],
            e["overshoot"], e["undershoot"], e["flame_starts"], _or_dash(e.get("water_peak")),
            _or_dash(e.get("return_avg")),
        )
    )
    if e.get("max_ch") is not None:
        line += f", flow limit {e['max_ch']:g}"
    if episode_kind(e) == "top_up":
        line += ", top-up (not used for advice)"
    return line


BOILER_SETTINGS = (("max_ch", "max flow"), ("dhw_setpoint", "hot water"))


def boiler_change_line(before, now):
    """A report line when a boiler setting changed since the last report, else None.

    before and now map "max_ch" / "dhw_setpoint" to a value or None. A setting
    without a value on either side (not configured, unavailable, first report)
    is not reported as a change.
    """
    before = before or {}
    parts = [
        f"{label} {before[key]:g} -> {now[key]:g} degC"
        for key, label in BOILER_SETTINGS
        if before.get(key) is not None and now.get(key) is not None and before[key] != now[key]
    ]
    return "Boiler settings changed since the last report: " + ", ".join(parts) if parts else None
