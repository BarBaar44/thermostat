"""
progress - how far each learning feature is from stable
-------------------------------------------------------
Plain Python, no Home Assistant. Measures the stored records against the
thresholds in docs/experimental.md ("When a feature becomes
stable") and returns lines for the evening report. Keep the numbers
here and in the docs in step.
"""

from . import gas_core, night_core, tuner_core

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
    rate = summary.get("rate")
    if rate is None:  # no boiler signals: no split, so no rate needed
        rate_ok = True
    elif rate["source"] == "default":
        rate_ok = False
        text += "; hot-water rate still the default ({} of {} minutes measured)".format(
            rate["minutes"], gas_core.RATE_MIN_MINUTES
        )
    else:
        rate_ok = True
        text += "; hot-water rate {}".format(rate["source"])
    return {"done": days >= GAS_DAYS and rate_ok, "text": text}


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


def pid_changes(episodes, room_id=None, room_name=None):
    """
    PID values changed by hand between valid episodes of the followed room
    (judged against the room followed now: tuner_core.judged_valid).
    Returns per outdoor band the last change: the episodes before it and after it.

    There is one set of PID values for all weather, so a change counts in
    every band with an episode on the new values after it, not only in the
    band of the first episode after it.

    Before and after only hold episodes with the boiler flow limit (max_ch) of
    the band's first episode after the change: a lower limit alone slows a
    warm-up and cuts its overshoot, which would otherwise be credited to the
    PID. Records without max_ch (older ones, or the field not set) only
    compare with each other. The same goes for the schema: schema 2 ends the
    watch after reaching target at the first reheat, so it measures overshoot
    and dip differently, whatever the PID values are.
    """
    followed = [
        e for e in episodes
        if tuner_core.judged_valid(e, room_id, room_name)
        and e.get("params") and None not in e["params"].values()
    ]
    by_band = {}
    for i in range(1, len(followed)):
        if followed[i]["params"] == followed[i - 1]["params"]:
            continue
        params = followed[i]["params"]
        # Episodes on the new values, until the next change
        run = []
        for e in followed[i:]:
            if e["params"] != params:
                break
            run.append(e)
        for band in dict.fromkeys(e["outdoor_band"] for e in run):
            first = next(e for e in run if e["outdoor_band"] == band)
            cap, schema = first.get("max_ch"), tuner_core.schema_of(first)

            def comparable(e, band=band, cap=cap, schema=schema):
                return (
                    e["outdoor_band"] == band and e.get("max_ch") == cap
                    and tuner_core.schema_of(e) == schema
                )

            before = [e for e in followed[:i] if comparable(e)][-PID_COMPARE_BEFORE:]
            after = [e for e in run if comparable(e)]
            by_band[band] = {"before": before, "after": after}
    return by_band


def pid_progress(episodes, room_id=None, room_name=None):
    changes = pid_changes(episodes, room_id, room_name)
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


def report_lines(gas_summary, nights, episodes, room_id=None, room_name=None):
    """Lines for the evening report, one per feature; nights and episodes of the followed room."""
    lines = []
    nights = night_core.for_room(nights, room_id) if room_id else nights
    for p in (gas_progress(gas_summary), night_progress(nights), pid_progress(episodes, room_id, room_name)):
        lines.append("- " + p["text"] + ("; ready to become stable" if p["done"] else ""))
    return lines
