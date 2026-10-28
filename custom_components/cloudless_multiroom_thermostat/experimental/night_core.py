"""
heating_night_core - night cooldown model for the heating tuner
----------------------------------------------------------------
Plain Python with no pyscript or Home Assistant dependencies, like
heating_tuner_core. Observe only: nothing here controls the heating.

A night runs from the moment central heating is switched off in the evening
until it is switched on again in the morning. It is sampled every 10 minutes.

Cooling model (Newton): the room loses heat in proportion to how much warmer
it is than outside,

    dT_room/dt = -k * (T_room - T_outdoor)       k in 1/h, tau = 1/k hours

k is fitted per night on the "quiet" part only: from QUIET_FROM_HOUR until
the end of the night, before sunrise, with the garden doors closed. The
evening does not follow the model: people, cooking and appliances keep the
living room warm for a few hours after the heating stops, then it drops
quickly around bedtime. That part is learned as a plain number instead:
the evening drop from EVENING_FROM_HOUR to QUIET_FROM_HOUR.

The prediction made at EVENING_FROM_HOUR therefore is: room now + median
evening drop at 01:00, then the cooling model with the forecast until the
comfort time.
"""

import math
from datetime import datetime, timedelta

from . import tuner_core

SCHEMA = 1

SAMPLE_MIN = 10            # night sample interval
NIGHT_START_HOURS = (18, 2)  # a CH switch-off between 18:00 and 02:00 starts a night
NIGHT_MAX_END_HOUR = 11    # close a night at 11:00 even if heating never came on
QUIET_FROM_HOUR = 1        # fit k from 01:00 onwards
EVENING_FROM_HOUR = 21     # evening drop is measured from 21:00 (the summary time) to 01:00
MIN_QUIET_SAMPLES = 18     # 3 hours of 10-minute samples
MIN_DELTA = 3.0            # degC indoor-outdoor needed for a meaningful fit
HOLD_DROP = 0.2            # "evening hold" ends when the room is this far below its peak
K_NIGHTS = 7               # median k over this many recent valid nights
MIN_NIGHTS = 2             # predictions start after this many valid nights
MIN_WARMUPS = 2            # warm-up estimate needs this many morning episodes

# Mirrors the Heating Schedule automation: when the living room should be at
# MORNING_TARGET. Weekday 0 = Monday.
COMFORT_TIME = {0: "07:30", 1: "07:30", 2: "07:30", 3: "07:30", 4: "07:30",
                5: "08:00", 6: "08:00"}
MORNING_TARGET = 18.0


def starts_night(hour):
    """True when a CH switch-off at this local hour starts a night."""
    first, last = NIGHT_START_HOURS
    return hour >= first or hour < last


def _hour(iso):
    return int(iso[11:13])


def _quiet(sample, start_iso):
    """Sample belongs to the quiet part of the night used for fitting."""
    if sample["room"] is None or sample["outdoor"] is None:
        return False
    if sample["sun_up"] or sample["garden_open"]:
        return False
    # The night starts in the evening; "after QUIET_FROM_HOUR" means the
    # next calendar day, before NIGHT_MAX_END_HOUR.
    return _quiet_hours(sample, start_iso)


def _quiet_hours(sample, start_iso):
    """Sample lies in the quiet hours (next day, QUIET_FROM_HOUR..NIGHT_MAX_END_HOUR)."""
    return sample["t"][:10] != start_iso[:10] and QUIET_FROM_HOUR <= _hour(sample["t"]) < NIGHT_MAX_END_HOUR


def fit_k(samples, start_iso):
    """
    Least-squares k (1/h) through the origin on consecutive quiet samples:
    rate = -dT/dt against delta = T_room - T_outdoor. Returns (k, n, mean_delta)
    or (None, n, mean_delta).
    """
    quiet = [s for s in samples if _quiet(s, start_iso)]
    num = den = 0.0
    deltas = []
    for a, b in zip(quiet, quiet[1:], strict=False):
        dt_h = (_minutes(b["t"]) - _minutes(a["t"])) / 60.0
        if dt_h <= 0 or dt_h > 0.5:      # skip gaps (restart, missing data)
            continue
        delta = (a["room"] + b["room"]) / 2 - (a["outdoor"] + b["outdoor"]) / 2
        rate = -(b["room"] - a["room"]) / dt_h
        num += rate * delta
        den += delta * delta
        deltas.append(delta)
    mean_delta = round(sum(deltas) / len(deltas), 2) if deltas else None
    if den == 0:
        return None, len(quiet), mean_delta
    return num / den, len(quiet), mean_delta


def _minutes(iso):
    """Minutes since the epoch for an ISO timestamp with UTC offset."""
    return datetime.fromisoformat(iso).timestamp() / 60.0


def _room_at_hour(samples, start_iso, hour):
    """Room temperature of the first sample at or after hour (evening hours on
    the start day, early hours on the next day)."""
    next_day = hour < 12
    for s in samples:
        if (s["t"][:10] != start_iso[:10]) == next_day and _hour(s["t"]) >= hour and s["room"] is not None:
            return s["room"]
    return None


def analyse(samples, end_reason, prediction=None, gas_start=None, gas_end=None):
    """Turn one night's samples into a record for nights.jsonl."""
    first, last = samples[0], samples[-1]
    reasons = []
    k, n_quiet, mean_delta = fit_k(samples, first["t"])
    room_evening = _room_at_hour(samples, first["t"], EVENING_FROM_HOUR)
    room_quiet = _room_at_hour(samples, first["t"], QUIET_FROM_HOUR)

    rooms = [s["room"] for s in samples if s["room"] is not None]
    outdoors = [s["outdoor"] for s in samples if s["outdoor"] is not None]
    peak = max(rooms) if rooms else None
    hold_min = None
    if peak is not None:
        # pyscript has no generator expressions: list comprehensions only
        peak_i = [i for i, s in enumerate(samples) if s["room"] == peak][0]
        drop_i = ([i for i, s in enumerate(samples) if i >= peak_i
                   and s["room"] is not None and s["room"] <= peak - HOLD_DROP] or [None])[0]
        if drop_i is not None:
            hold_min = int(round(_minutes(samples[drop_i]["t"]) - _minutes(first["t"])))

    if any([s["garden_open"] for s in samples]):
        reasons.append("garden_doors")
    if n_quiet < MIN_QUIET_SAMPLES:
        reasons.append("too_short")
    if mean_delta is not None and mean_delta < MIN_DELTA:
        reasons.append("small_delta")
    if k is None or k <= 0:
        reasons.append("no_fit")

    record = {
        "schema": SCHEMA,
        "start": first["t"],
        "end": last["t"],
        "end_reason": end_reason,
        "hours": round((_minutes(last["t"]) - _minutes(first["t"])) / 60.0, 1),
        "room_start": first["room"],
        "room_peak": peak,
        "room_end": last["room"],
        "drop": round(first["room"] - last["room"], 2) if first["room"] is not None and last["room"] is not None else None,
        "hold_min": hold_min,
        "evening_drop": round(room_quiet - room_evening, 2) if room_evening is not None and room_quiet is not None else None,
        "outdoor_avg": round(sum(outdoors) / len(outdoors), 1) if outdoors else None,
        "outdoor_min": min(outdoors) if outdoors else None,
        "wind_avg": _avg([s["wind"] for s in samples]),
        # People home in the evening part (until QUIET_FROM_HOUR) drive the
        # evening hold/drop; overnight they are asleep and matter little
        "people_evening": _avg([s.get("people") for s in samples if not _quiet_hours(s, first["t"])]),
        "quiet_samples": n_quiet,
        "mean_delta": mean_delta,
        "k": round(k, 5) if k is not None else None,
        "tau_h": round(1 / k, 1) if k else None,
        "gas_used": round(gas_end - gas_start, 3) if gas_start is not None and gas_end is not None else None,
        "valid": not reasons,
        "invalid_reasons": reasons,
        "predicted_end_room": None,
        "prediction_error": None,
    }
    if prediction:
        pred = predicted_at(prediction, last["t"])
        if pred is not None and last["room"] is not None:
            record["predicted_end_room"] = round(pred, 2)
            record["prediction_error"] = round(last["room"] - pred, 2)
    return record


def _avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else None


def _median(values):
    values = sorted(values)
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def model(nights):
    """
    Median k and evening drop over the last K_NIGHTS valid nights.
    Returns (k, evening_drop, n); k is None while learning.
    """
    used = [n for n in nights if n["valid"] and n["k"] and n.get("evening_drop") is not None][-K_NIGHTS:]
    if len(used) < MIN_NIGHTS:
        return None, None, len(used)
    return _median([n["k"] for n in used]), _median([n["evening_drop"] for n in used]), len(used)


def simulate(room_now, now_iso, forecast, k, until_iso):
    """
    Step the cooling model hour by hour from now until until_iso.
    forecast: list of (iso, outdoor_temp) sorted by time. Returns a list of
    (iso, predicted_room) points including the start and the end.
    """
    points = [(now_iso, round(room_now, 2))]
    t, room = _minutes(now_iso), room_now
    end = _minutes(until_iso)
    fc = [(_minutes(f_iso), temp) for f_iso, temp in forecast if temp is not None]
    if not fc:
        return points
    while t < end:
        step = min(60, end - t)
        outdoor = _outdoor_at(fc, t + step / 2)
        room = outdoor + (room - outdoor) * math.exp(-k * step / 60.0)
        t += step
        points.append((_iso_from(points[0][0], t - _minutes(now_iso)), round(room, 2)))
    return points


def _outdoor_at(fc, t):
    if t <= fc[0][0]:
        return fc[0][1]
    for (t1, v1), (t2, v2) in zip(fc, fc[1:], strict=False):
        if t1 <= t <= t2:
            return v1 + (v2 - v1) * (t - t1) / (t2 - t1)
    return fc[-1][1]


def _iso_from(base_iso, add_min):
    """base_iso + add_min minutes, keeping base_iso's UTC offset."""
    return (datetime.fromisoformat(base_iso) + timedelta(minutes=add_min)).isoformat(timespec="seconds")


def predicted_at(prediction, iso):
    """Interpolate a stored prediction's points at iso; None outside its range."""
    pts = [(_minutes(p_iso), v) for p_iso, v in prediction.get("points", [])]
    t = _minutes(iso)
    if not pts or t < pts[0][0]:
        return None
    for (t1, v1), (t2, v2) in zip(pts, pts[1:], strict=False):
        if t1 <= t <= t2:
            return v1 + (v2 - v1) * (t - t1) / (t2 - t1) if t2 > t1 else v2
    # A little past the last point (heating came on late): hold the last value
    return pts[-1][1] if t - pts[-1][0] <= 120 else None


def warmup_rate(episodes, outdoor=None, room="Living Room"):
    """
    Median warm-up speed (degC per minute) from living room morning episodes
    that reached their target. Warm-ups get slower as it gets colder, so
    mornings in the same outdoor band as `outdoor` are used once there are
    MIN_WARMUPS of them; until then all mornings. Returns (rate, n, band),
    band being None when all mornings were used.
    """
    band = tuner_core.outdoor_band(outdoor) if outdoor is not None else None
    in_band = _warmup_rates([e for e in episodes if band and e.get("outdoor_band") == band], room)
    if len(in_band) >= MIN_WARMUPS:
        return _median(in_band), len(in_band), band
    rates = _warmup_rates(episodes, room)
    if len(rates) < MIN_WARMUPS:
        return None, len(rates), None
    return _median(rates), len(rates), None


def _warmup_rates(episodes, room="Living Room"):
    rates = []
    for e in episodes:
        if e.get("area") != room or not e.get("rise_min"):
            continue
        if "garden_doors" in e.get("invalid_reasons", []) or e.get("start_temp") is None:
            continue
        if not 5 <= _hour(e["start"]) < 11:
            continue
        deficit = e["target"] - e["start_temp"]
        if deficit > 0.2:
            rates.append(deficit / e["rise_min"])
    return rates[-K_NIGHTS:]


def predict(room_now, now_iso, forecast, nights, episodes, comfort_at, room="Living Room", morning_target=MORNING_TARGET):
    """
    Evening prediction for the coming night. Returns a dict that is stored
    and compared with reality the next morning.
    """
    k, evening_drop, n_nights = model(nights)
    out = {
        "made": now_iso,
        "comfort_at": comfort_at,
        "room_now": room_now,
        "k": round(k, 5) if k else None,
        "tau_h": round(1 / k, 1) if k else None,
        "evening_drop": round(evening_drop, 2) if evening_drop is not None else None,
        "nights_used": n_nights,
        "points": [],
        "room_at_comfort": None,
        "warmup_min": None,
        "warmups_used": 0,
        "warmup_band": None,
        "suggested_start": None,
        "outdoor_min": min([t for _, t in forecast if t is not None], default=None),
        "morning_target": morning_target,
    }
    if k is None or room_now is None or out["outdoor_min"] is None:
        return out
    # Evening: room now + learned drop at 01:00 (straight line), then the model
    quiet_at = datetime.fromisoformat(now_iso)
    if quiet_at.hour >= 12:
        quiet_at += timedelta(days=1)
    quiet_at = quiet_at.replace(hour=QUIET_FROM_HOUR, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    if _minutes(now_iso) < _minutes(quiet_at) < _minutes(comfort_at):
        points = [(now_iso, round(room_now, 2))] + simulate(room_now + evening_drop, quiet_at, forecast, k, comfort_at)
    else:
        points = simulate(room_now, now_iso, forecast, k, comfort_at)
    out["points"] = points
    out["room_at_comfort"] = points[-1][1]
    fc = [(_minutes(f_iso), temp) for f_iso, temp in forecast if temp is not None]
    rate, n_warm, band = warmup_rate(episodes, _outdoor_at(fc, _minutes(comfort_at)), room)
    out["warmups_used"] = n_warm
    out["warmup_band"] = band
    if rate:
        deficit = max(morning_target - out["room_at_comfort"], 0.0)
        out["warmup_min"] = int(round(deficit / rate))
        out["suggested_start"] = _iso_from(comfort_at, -out["warmup_min"])
    return out


def night_line(n):
    """One human-readable line about last night for the daily notification."""
    start = n["start"][11:16]
    end = n["end"][11:16]
    base = "- Night {}-{}: {:.1f} -> {:.1f} degC (outside avg {}, people home in the evening {}), evening hold {} min, evening drop {}".format(
        start, end, n["room_start"] or 0, n["room_end"] or 0, n["outdoor_avg"],
        n["people_evening"] if n.get("people_evening") is not None else "-",
        n["hold_min"] if n["hold_min"] is not None else "-",
        "{:+.1f}".format(n["evening_drop"]) if n.get("evening_drop") is not None else "-")
    if n["valid"]:
        base += ", cooling tau {} h".format(n["tau_h"])
    else:
        base += ", not used ({})".format(", ".join(n["invalid_reasons"]))
    if n["prediction_error"] is not None:
        base += ", predicted {:.1f} (error {:+.1f})".format(n["predicted_end_room"], n["prediction_error"])
    return base


def prediction_line(p):
    """One human-readable line about tonight's prediction."""
    if p["k"] is None:
        return "- Tonight: still learning the cooling rate ({}/{} usable nights)".format(
            p["nights_used"], MIN_NIGHTS)
    if p["room_at_comfort"] is None:
        return "- Tonight: no prediction (room temperature or forecast missing)"
    line = "- Tonight: room {:.1f} at {} (outside min {}, tau {} h, evening drop {:+.1f})".format(
        p["room_at_comfort"], p["comfort_at"][11:16], p["outdoor_min"], p["tau_h"], p["evening_drop"])
    if p["suggested_start"]:
        line += "; to be {:.0f} degC by then I would start at {} ({} min warm-up, {})".format(
            p.get("morning_target", MORNING_TARGET), p["suggested_start"][11:16], p["warmup_min"],
            "outside band " + p["warmup_band"] if p.get("warmup_band") else "all mornings")
    else:
        line += "; warm-up time still learning ({}/{} mornings)".format(p["warmups_used"], MIN_WARMUPS)
    return line
