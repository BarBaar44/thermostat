"""
progress - how far each experimental feature is from stable
------------------------------------------------------------
Plain Python, no Home Assistant. Measures the stored records against the
thresholds in docs/experimental.md ("When a feature stops being
experimental") and returns lines for the evening report. Keep the numbers
here and in the docs in step.
"""

from . import tuner_core

# Gas per degree-day
GAS_DAYS = 365             # a full year of data holds a whole heating season

# Night cooldown model
NIGHTS = 30                # predicted nights judged
NIGHT_BANDS = 3            # spread over this many outdoor bands
NIGHT_MEAN_ERROR = 0.3     # degC, average absolute error
NIGHT_MAX_ERROR = 0.6      # degC, worst night

# Warm-up episodes and PID advice
PID_BANDS = 2              # outdoor bands with a change applied by hand
PID_EPISODES_AFTER = 5     # valid episodes in that band after the change
PID_COMPARE_BEFORE = 5     # episodes before the change to compare with


def gas_progress(summary):
    """Days of gas and outdoor statistics against a full year."""
    if summary is None:
        return {"done": False, "text": "Gas per degree-day: not set up"}
    days = summary.get("days", 0)
    text = f"Gas per degree-day: {min(days, GAS_DAYS)} of {GAS_DAYS} days of data"
    if summary.get("first"):
        text += " (since {})".format(summary["first"])
    text += "; hot-water rate is still a constant"
    return {"done": False, "text": text}


def night_progress(nights):
    """The last NIGHTS valid nights with a prediction: count, bands and errors."""
    judged = [n for n in nights if n.get("valid") and n.get("prediction_error") is not None][-NIGHTS:]
    if not judged:
        return {"done": False, "text": f"Night model: 0 of {NIGHTS} predicted nights"}
    errors = [abs(n["prediction_error"]) for n in judged]
    bands = {tuner_core.outdoor_band(n.get("outdoor_avg")) for n in judged} - {"unknown"}
    mean, worst = sum(errors) / len(errors), max(errors)
    done = (
        len(judged) >= NIGHTS
        and len(bands) >= NIGHT_BANDS
        and mean <= NIGHT_MEAN_ERROR
        and worst <= NIGHT_MAX_ERROR
    )
    text = (
        f"Night model: {len(judged)} of {NIGHTS} predicted nights, {min(len(bands), NIGHT_BANDS)} of {NIGHT_BANDS} outdoor bands, "
        f"average error {mean:.2f} (max {NIGHT_MEAN_ERROR}), worst {worst:.1f} (max {NIGHT_MAX_ERROR})"
    )
    return {"done": done, "text": text}


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _better(before, after):
    """Less overshoot or a faster warm-up on average."""
    for key in ("overshoot", "rise_min"):
        b, a = _mean([e.get(key) for e in before]), _mean([e.get(key) for e in after])
        if b is not None and a is not None and a < b:
            return True
    return False


def pid_changes(episodes):
    """
    PID values changed by hand between valid episodes of the followed room.
    Returns per outdoor band the last change: the episodes before it and after it.
    """
    followed = [
        e for e in episodes
        if e.get("valid") and e.get("room_class") == "living"
        and e.get("params") and None not in e["params"].values()
    ]
    by_band = {}
    for i in range(1, len(followed)):
        if followed[i]["params"] == followed[i - 1]["params"]:
            continue
        band = followed[i]["outdoor_band"]
        params = followed[i]["params"]
        same = [e for e in followed if e["outdoor_band"] == band]
        before = [e for e in followed[:i] if e["outdoor_band"] == band][-PID_COMPARE_BEFORE:]
        after = [e for e in same if e["start"] >= followed[i]["start"] and e["params"] == params]
        by_band[band] = {"before": before, "after": after}
    return by_band


def pid_progress(episodes):
    changes = pid_changes(episodes)
    good, parts = 0, []
    for band, c in sorted(changes.items()):
        n = len(c["after"])
        better = n > 0 and c["before"] and _better(c["before"], c["after"])
        if n >= PID_EPISODES_AFTER and better:
            good += 1
        parts.append("{} {}/{}{}".format(
            band, min(n, PID_EPISODES_AFTER), PID_EPISODES_AFTER,
            " better" if better else (" not better" if n and c["before"] else ""),
        ))
    text = f"PID advice: {min(good, PID_BANDS)} of {PID_BANDS} outdoor bands confirmed"
    if parts:
        text += " (episodes after a change by hand: {})".format(", ".join(parts))
    else:
        text += " (no change applied by hand yet)"
    text += "; check gas per degree-day did not get worse"
    return {"done": good >= PID_BANDS, "text": text}


def report_lines(gas_summary, nights, episodes):
    """Lines for the evening report, one per feature."""
    lines = []
    for p in (gas_progress(gas_summary), night_progress(nights), pid_progress(episodes)):
        lines.append("- " + p["text"] + ("; ready to leave experimental" if p["done"] else ""))
    return lines
