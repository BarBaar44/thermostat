"""
heating_tuner_core - pure analysis logic for the heating tuner
---------------------------------------------------------------
Plain Python with no pyscript or Home Assistant dependencies, so it can be
moved to AppDaemon or a custom integration later without changes. All I/O
and HA access lives in apps/heating_tuner.py and modules/heating_tuner_store.py.

An episode is a list of one-minute samples (dicts) taken while the boiler PID
works towards a target, from the moment demand appears until POST_REACH_MIN
minutes after the room first reaches the target.
"""

SCHEMA = 1

POST_REACH_MIN = 90        # observe this long after the room first reaches target
MAX_RISE_MIN = 240         # give up on reaching target after this long
MIN_EPISODE_MIN = 30       # shorter episodes are not judged
DHW_MAX_MIN = 10           # more hot-water time than this disturbs the episode

OVERSHOOT_LIMIT = 0.4      # degC above target
SLOW_RISE_MIN = 60         # minutes to reach target counted as slow
UNDERSHOOT_LIMIT = 0.4     # degC below target after the first reach
FLAME_STARTS_PER_HOUR = 6  # more than this is short-cycling
CONSISTENT_EPISODES = 2    # this many valid episodes in a row must agree
STEP = 0.10                # 10 % parameter step

# Only episodes led by these rooms are used for tuning. Other rooms lead
# rarely; their episodes are still recorded (reason "other_room") so we can
# judge later whether they need parameters of their own.
TUNED_AREAS = ("Living Room",)

LIMITS = {"kp": (0.3, 1.5), "ki": (0.0005, 0.01), "kd": (0.0, 2000.0)}

OUTDOOR_BANDS = ((0.0, "below_0"), (8.0, "0_8"), (15.0, "8_15"))


def outdoor_band(temp):
    if temp is None:
        return "unknown"
    for upper, name in OUTDOOR_BANDS:
        if temp < upper:
            return name
    return "above_15"


def room_class(area):
    return "living" if area == "Living Room" else "other"


def analyse(samples, params, start_reason, extra_reasons=()):
    """Turn one episode's samples into a record for episodes.jsonl."""
    first = samples[0]
    target = first["target"]
    reasons = list(extra_reasons)

    # pyscript has no generator expressions: list comprehensions only
    reach_idx = (
        [i for i, s in enumerate(samples) if s["room"] is not None and s["room"] >= target]
        or [None]
    )[0]
    after = samples[reach_idx:] if reach_idx is not None else []
    rooms_after = [s["room"] for s in after if s["room"] is not None]
    peak = max(rooms_after) if rooms_after else None
    peak_idx = rooms_after.index(peak) if rooms_after else None
    trough = min(rooms_after[peak_idx:]) if rooms_after else None

    flame_starts = len(
        [1 for a, b in zip(samples, samples[1:]) if b["flame"] and not a["flame"]]
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

    if len(samples) < MIN_EPISODE_MIN:
        reasons.append("too_short")
    if first["area"] not in TUNED_AREAS:
        reasons.append("other_room")
    if any([s["area"] != first["area"] for s in samples]):
        reasons.append("winner_changed")
    if any([s["target"] != target for s in samples]):
        reasons.append("target_changed")
    if any([s["garden_paused"] for s in samples]):
        reasons.append("garden_doors")
    if len([1 for s in samples if s["dhw"]]) > DHW_MAX_MIN:
        reasons.append("hot_water")
    if reach_idx is None:
        reasons.append("target_not_reached")

    return {
        "schema": SCHEMA,
        "start": first["t"],
        "end": samples[-1]["t"],
        "start_reason": start_reason,
        "area": first["area"],
        "room_class": room_class(first["area"]),
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
        "water_peak": max([s["water"] for s in samples if s["water"] is not None], default=None),
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


def _avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else None


def verdict(episode):
    """What a single valid episode asks for, or None when it is within limits."""
    if not episode["valid"]:
        return None
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
    "overshoot": ("ki", -STEP, "overshoot above {:.1f} degC".format(OVERSHOOT_LIMIT)),
    "oscillating": ("kp", -STEP, "swinging around target or short-cycling"),
    "slow": ("kp", +STEP, "slow warm-up without overshoot"),
}


def recommend(episodes, params):
    """
    Look at the most recent valid episodes of the latest valid episode's group
    (room class + outdoor band). Recommend one change only when the last
    CONSISTENT_EPISODES of them agree. Returns None or a dict.
    """
    valid = [e for e in episodes if e["valid"]]
    if not valid:
        return None
    last = valid[-1]
    group = [
        e for e in episodes
        if e["valid"]
        and e["room_class"] == last["room_class"]
        and e["outdoor_band"] == last["outdoor_band"]
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
    low, high = LIMITS[name]
    old = params[name]
    new = round(min(max(old * (1 + step), low), high), 5)
    if new == old:
        return {"verdict": v, "param": name, "old": old, "new": old, "why": why, "at_limit": True}
    return {"verdict": v, "param": name, "old": old, "new": new, "why": why, "at_limit": False}


def summary_line(e):
    """One human-readable line for the daily notification."""
    start = e["start"][11:16]
    if not e["valid"]:
        return "- {} {}: skipped ({})".format(start, e["area"], ", ".join(e["invalid_reasons"]))
    return (
        "- {} {} -> {:.1f} degC (outside {} degC): target after {} min, "
        "overshoot {:+.1f}, dip {:.1f}, {} flame starts, water max {}, return avg {}".format(
            start, e["area"], e["target"], e["outdoor_avg"], e["rise_min"],
            e["overshoot"], e["undershoot"], e["flame_starts"], e["water_peak"],
            e.get("return_avg", "-"),
        )
    )
