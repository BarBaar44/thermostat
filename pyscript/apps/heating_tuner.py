"""
heating_tuner - pyscript app (phase 2: observe only)
-----------------------------------------------------
Samples the boiler PID loop once a minute, cuts the samples into warm-up
episodes, stores each episode in episodes.jsonl and works out what it WOULD
change. It never writes the Kp/Ki/Kd numbers in this phase.

It also reports heating gas per degree-day (from Home Assistant's long-term
statistics of the gas meter and the outdoor temperature) as the yardstick for
whether a change saved gas. Each complete day's gas is split into hot water
and heating while the boiler signals are still in the recorder's history
(7 days) and stored in gas_days.jsonl, with "away" when nobody was home the
whole day (zone.home); away days don't count towards the figures.

It also records each night's cooldown (heating off in the evening until it
comes on in the morning) in nights.jsonl, and at the summary time predicts
the coming night from the weather forecast: how cold the living room will
be at the comfort time and when the heating WOULD have to start. Nothing is
controlled; the next morning the prediction is compared with reality.

All analysis lives in modules/heating_tuner_core.py,
modules/heating_night_core.py and modules/heating_gas_core.py (plain Python), file I/O in
modules/heating_tuner_store.py. This file is only the HA glue.

Config (pyscript/config.yaml -> apps: heating_tuner:), all optional:
    data_dir: /config/heating_tuner
    summary_time: "21:00"   # keep at 21:00: the night prediction runs then and
                            # the evening drop is learned from 21:00
"""

import json
from datetime import timedelta
import homeassistant.util.dt as dt_util
from homeassistant.components.recorder import get_instance, history
from homeassistant.core import async_get_hass
import heating_tuner_core as core
import heating_night_core as night_core
import heating_gas_core as gas_core
import heating_tuner_store as store

CFG = pyscript.app_config or {}
DATA_DIR = CFG.get("data_dir", "/config/heating_tuner")
EPISODES = DATA_DIR + "/episodes.jsonl"
STATE = DATA_DIR + "/state.json"
NIGHTS = DATA_DIR + "/nights.jsonl"
NIGHT_STATE = DATA_DIR + "/night_state.json"
GAS_DAYS = DATA_DIR + "/gas_days.jsonl"
SUMMARY_TIME = CFG.get("summary_time", "21:00")

E_CLIMATE = "climate.thermostat_central_heating"
E_ROOM = "sensor.virtual_heating_temperature"
E_DELTA = "sensor.delta_temperature"
E_CH_ENABLED = "switch.main_thermostat_central_heating_enabled"
E_FLAME = "binary_sensor.diyless_thermostat_flame_on"
E_DHW = "binary_sensor.main_thermostat_domestic_hot_water_active"
E_CH_ACTIVE = "binary_sensor.main_thermostat_central_heating_active"
E_WATER = "sensor.thermostat_water_temperature"
E_RETURN = "sensor.diyless_thermostat_return_water_temperature"
# Number of people home (persons in zone.home). People add heat - the evening
# after 20:00 can even warm up when everyone is home. Not everyone is tracked,
# so treat it as "more or fewer than usual", not an exact count.
E_PEOPLE = "zone.home"
E_OUTDOOR = "sensor.outdoor_temperature"
E_INTEGRAL = "sensor.attic_main_thermostat_central_heating_pid_integral"
E_GARDEN = "input_boolean.garden_heating_paused"
E_KP = "number.attic_main_thermostat_central_heating_kp"
E_KI = "number.attic_main_thermostat_central_heating_ki"
E_KD = "number.attic_main_thermostat_central_heating_kd"
E_GAS = "sensor.p1_meter_kit_gas_consumed"
E_LIVING = "sensor.temperature_living_room_temperature"
E_GARDEN_DOOR = "binary_sensor.backdoors_garden"
E_SUN = "sun.sun"
E_WEATHER = "weather.forecast_home"
E_GAS_BEFORE6 = "sensor.frank_energie_gasprices_gas_price_tomorrow_before_6am_all_in"
E_GAS_AFTER6 = "sensor.frank_energie_gasprices_gas_price_tomorrow_after_6am_all_in"
MIN_NIGHT_HOURS = 4    # shorter heating pauses are not nights
SPLIT_DAYS_BACK = 6    # complete days still fully in the recorder's 7-day history

# In-memory only: an episode in progress is lost on an HA restart, which is
# fine - a restart mid-episode would make it unreliable anyway.
episode = None
last_target = None
# The night in progress is saved to night_state.json every sample, because a
# night is long and a pyscript reload or HA restart during it is likely.
night = None


def _float(entity_id, scale=1.0):
    try:
        return float(state.get(entity_id)) * scale
    except (TypeError, ValueError, NameError):
        return None


def _is_on(entity_id):
    try:
        return state.get(entity_id) == "on"
    except NameError:
        return False


def _area():
    try:
        return json.loads(state.get(E_DELTA)).get("area", "Off")
    except (TypeError, ValueError, NameError):
        return "Off"


def _target():
    try:
        return float(state.getattr(E_CLIMATE).get("temperature"))
    except (TypeError, ValueError, NameError, AttributeError):
        return None


def _params():
    return {"kp": _float(E_KP), "ki": _float(E_KI), "kd": _float(E_KD)}


def _sample():
    return {
        "t": dt_util.now().isoformat(timespec="seconds"),
        "target": _target(),
        "room": _float(E_ROOM),
        "area": _area(),
        "flame": _is_on(E_FLAME),
        "dhw": _is_on(E_DHW),
        "water": _float(E_WATER),
        "ret": _float(E_RETURN),
        "people": _float(E_PEOPLE),
        "outdoor": _float(E_OUTDOOR),
        # ESPHome reports the PID terms x100
        "integral": _float(E_INTEGRAL, 0.01),
        "garden_paused": _is_on(E_GARDEN),
        "gas": _float(E_GAS),
    }


@time_trigger("period(now, 60s)")
def heating_tuner_tick():
    global episode, last_target
    s = _sample()
    ch_enabled = _is_on(E_CH_ENABLED)
    raised = last_target is not None and s["target"] is not None and s["target"] > last_target
    last_target = s["target"]

    if episode is None:
        if (
            ch_enabled
            and s["target"] is not None
            and s["room"] is not None
            and s["area"] != "Off"
            and s["room"] < s["target"] - 0.1
        ):
            episode = {
                "samples": [s],
                "reason": "target_raised" if raised else "cooled_down",
                "reached": None,
            }
        return

    episode["samples"].append(s)
    n = len(episode["samples"])
    if episode["reached"] is None and s["room"] is not None and s["target"] is not None \
            and s["room"] >= s["target"]:
        episode["reached"] = n - 1

    if not ch_enabled:
        _finish(["heating_stopped"] if (
            episode["reached"] is None or n - episode["reached"] < core.POST_REACH_MIN
        ) else [])
    elif episode["reached"] is not None and n - episode["reached"] >= core.POST_REACH_MIN:
        _finish([])
    elif episode["reached"] is None and n >= core.MAX_RISE_MIN:
        _finish([])


def _finish(extra_reasons):
    global episode
    ep, episode = episode, None
    record = core.analyse(ep["samples"], _params(), ep["reason"], extra_reasons)
    store.append_episode(EPISODES, record)

    history = store.read_episodes(EPISODES)
    rec = core.recommend(history, _params())
    tuner_state = store.read_state(STATE)
    tuner_state["last_episode"] = record["start"]
    tuner_state["recommendation"] = rec
    store.write_state(STATE, tuner_state)

    _publish(record, rec)
    log.info(f"heating_tuner: episode {record['start']} valid={record['valid']} "
             f"overshoot={record['overshoot']} rec={rec}")


def _rec_text(rec):
    if rec is None:
        return "no change"
    if rec["at_limit"]:
        return f"{rec['param']} at its limit ({rec['old']}) - {rec['why']}"
    return f"{rec['param']} {rec['old']} -> {rec['new']} ({rec['why']})"


def _publish(record, rec):
    state.set(
        "sensor.heating_tuner_last_overshoot",
        record["overshoot"] if record["overshoot"] is not None else "unknown",
        {
            "friendly_name": "Heating tuner last overshoot",
            "unit_of_measurement": "°C",
            "icon": "mdi:thermometer-chevron-up",
            "area": record["area"],
            "start": record["start"],
            "rise_min": record["rise_min"],
            "undershoot": record["undershoot"],
            "flame_starts": record["flame_starts"],
            "water_peak": record["water_peak"],
            "outdoor_avg": record["outdoor_avg"],
            "valid": record["valid"],
            "invalid_reasons": record["invalid_reasons"],
        },
    )
    state.set(
        "sensor.heating_tuner_recommendation",
        _rec_text(rec)[:255],
        {
            "friendly_name": "Heating tuner recommendation",
            "icon": "mdi:tune-variant",
            "mode": "observe",
        },
    )


# ---------------------------------------------------------------- nights


def _sun_up():
    try:
        return state.get(E_SUN) == "above_horizon"
    except NameError:
        return False


def _attr_float(entity_id, attr):
    try:
        return float(state.getattr(entity_id).get(attr))
    except (TypeError, ValueError, NameError, AttributeError):
        return None


def _night_sample():
    return {
        "t": dt_util.now().isoformat(timespec="seconds"),
        "room": _float(E_LIVING),
        "outdoor": _float(E_OUTDOOR),
        "wind": _attr_float(E_WEATHER, "wind_speed"),
        "garden_open": _is_on(E_GARDEN_DOOR),
        "sun_up": _sun_up(),
        "people": _float(E_PEOPLE),
    }


def _save_night():
    store.update_state(NIGHT_STATE, {"night": night})


@time_trigger("startup")
def heating_night_resume():
    """Pick up a night that was in progress before a reload or restart."""
    global night
    saved = store.read_state(NIGHT_STATE).get("night")
    if not saved:
        return
    night = saved
    if _is_on(E_CH_ENABLED):
        _finish_night("heating_on")
    else:
        log.info(f"heating_tuner: resumed night started {night['samples'][0]['t']}")


@state_trigger(f"{E_CH_ENABLED} == 'off' and {E_CH_ENABLED}.old == 'on'")
def heating_night_start():
    global night
    if night is not None or not night_core.starts_night(dt_util.now().hour):
        return
    night = {"samples": [_night_sample()], "gas_start": _float(E_GAS)}
    _save_night()


# Any change to "on" (also unavailable -> on after a restart) ends the night
@state_trigger(f"{E_CH_ENABLED} == 'on' and {E_CH_ENABLED}.old != 'on'")
def heating_night_end():
    if night is not None:
        _finish_night("heating_on")


@time_trigger(f"period(now, {night_core.SAMPLE_MIN * 60}s)")
def heating_night_sample():
    if night is None:
        return
    night["samples"].append(_night_sample())
    hour = dt_util.now().hour
    if night_core.NIGHT_MAX_END_HOUR <= hour < night_core.NIGHT_START_HOURS[0]:
        _finish_night("timeout")
    else:
        _save_night()


def _finish_night(end_reason):
    global night
    n, night = night, None
    n["samples"].append(_night_sample())
    prediction = store.update_state(NIGHT_STATE, {"night": None}).get("prediction")

    samples = n["samples"]
    hours = (dt_util.parse_datetime(samples[-1]["t"]) - dt_util.parse_datetime(samples[0]["t"])).total_seconds() / 3600
    if hours < MIN_NIGHT_HOURS:
        log.info(f"heating_tuner: heating pause of {hours:.1f} h from {samples[0]['t']} is not a night")
        return
    # Only compare with the prediction made for this night
    if prediction and not (prediction["made"] <= samples[-1]["t"] and prediction["made"] >= samples[0]["t"][:10]):
        prediction = None
    record = night_core.analyse(samples, end_reason, prediction, n.get("gas_start"), _float(E_GAS))
    store.append_episode(NIGHTS, record)
    log.info(f"heating_tuner: night {record['start']} valid={record['valid']} "
             f"tau_h={record['tau_h']} error={record['prediction_error']}")


def _comfort_at(now):
    day = now + timedelta(days=1) if now.hour >= 12 else now
    hh, mm = night_core.COMFORT_TIME[day.weekday()].split(":")
    return day.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)


def _predict():
    now = dt_util.now()
    try:
        response = weather.get_forecasts(entity_id=E_WEATHER, type="hourly", return_response=True)
        forecast = [(f["datetime"], f.get("temperature")) for f in response[E_WEATHER]["forecast"]]
    except Exception as err:
        log.warning(f"heating_tuner: no hourly forecast: {err}")
        forecast = []
    prediction = night_core.predict(
        _float(E_LIVING),
        now.isoformat(timespec="seconds"),
        forecast,
        store.read_episodes(NIGHTS),
        store.read_episodes(EPISODES),
        _comfort_at(now).isoformat(timespec="seconds"),
        _float(E_GAS_BEFORE6),
        _float(E_GAS_AFTER6),
    )
    store.update_state(NIGHT_STATE, {"prediction": prediction})
    state.set(
        "sensor.heating_tuner_night_prediction",
        prediction["suggested_start"][11:16] if prediction["suggested_start"] else "learning",
        {
            "friendly_name": "Heating tuner night prediction",
            "icon": "mdi:weather-night",
            "mode": "observe",
            "comfort_at": prediction["comfort_at"],
            "room_at_comfort": prediction["room_at_comfort"],
            "warmup_min": prediction["warmup_min"],
            "tau_h": prediction["tau_h"],
            "evening_drop": prediction["evening_drop"],
            "outdoor_min": prediction["outdoor_min"],
            "nights_used": prediction["nights_used"],
            "warmups_used": prediction["warmups_used"],
            "gas_before6": prediction["gas_before6"],
            "gas_after6": prediction["gas_after6"],
        },
    )
    return prediction


# ---------------------------------------------------------------- gas per degree-day


def _stat_date(start):
    """Local ISO date of a daily statistics row (rows start at local midnight)."""
    if not isinstance(start, str):
        start = start.isoformat()
    return dt_util.as_local(dt_util.parse_datetime(start)).date().isoformat()


def _day_start(date):
    return dt_util.start_of_local_day(dt_util.parse_datetime(date + "T12:00:00"))


def _people(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _away(people, date):
    """True if nobody was home the whole of `date` (zone.home history)."""
    d0 = _day_start(date)
    return gas_core.nobody_home(people, d0, d0 + timedelta(days=1))


def _read_gas_days():
    """
    Stored gas days by date. A later line for the same date updates the
    earlier one: days stored before the away flag existed get it added as a
    separate {"date", "away"} line, keeping the file append-only.
    """
    days = {}
    for d in store.read_episodes(GAS_DAYS, last_n=800):
        days.setdefault(d["date"], {}).update(d)
    return days


def _store_gas_splits(now):
    """
    Split each complete day of the last SPLIT_DAYS_BACK that is not stored
    yet, and add the away flag to stored days that don't have it.
    """
    stored = _read_gas_days()
    midnight = dt_util.start_of_local_day(now)
    window = [(midnight - timedelta(days=n)).date().isoformat()
              for n in range(SPLIT_DAYS_BACK, 0, -1)]
    dates = [d for d in window if "dhw_gas" not in stored.get(d, {})]
    unflagged = [d for d in window if d in stored and "away" not in stored[d]]
    if not dates and not unflagged:
        return
    start = _day_start(min(dates + unflagged))
    response = recorder.get_statistics(
        statistic_ids=[E_GAS],
        start_time=start.isoformat(),
        end_time=midnight.isoformat(),
        period="hour",
        types=["change"],
        units={},
        return_response=True,
    )
    # hass_is_global is off for pyscript, so fetch the instance directly
    hass_ = async_get_hass()
    states = await get_instance(hass_).async_add_executor_job(
        history.get_significant_states, hass_, dt_util.as_utc(start), dt_util.as_utc(midnight),
        [E_CH_ACTIVE, E_DHW, E_FLAME, E_PEOPLE], None, True, False, False, True,
    )
    changes = {}
    for eid in (E_CH_ACTIVE, E_DHW, E_FLAME):
        changes[eid] = [(st.last_changed, st.state == "on") for st in states.get(eid, [])]
    people = [(st.last_changed, _people(st.state)) for st in states.get(E_PEOPLE, [])]
    for date in unflagged:
        flag = {"date": date, "away": _away(people, date)}
        store.append_episode(GAS_DAYS, flag)
        log.info(f"heating_tuner: gas day flag {flag}")

    for date in dates:
        hours = []
        for r in response["statistics"].get(E_GAS, []):
            if _stat_date(r["start"]) != date:
                continue
            h0 = r["start"] if not isinstance(r["start"], str) else dt_util.parse_datetime(r["start"])
            h1 = h0 + timedelta(hours=1)
            hours.append({
                "gas": r.get("change"),
                "ch": gas_core.on_minutes(changes[E_CH_ACTIVE], h0, h1),
                "dhw": gas_core.on_minutes(changes[E_DHW], h0, h1),
                "flame": gas_core.on_minutes(changes[E_FLAME], h0, h1),
            })
        if len(hours) < 23:   # 23 on the spring DST day
            log.warning(f"heating_tuner: {len(hours)} gas hours for {date}, not split")
            continue
        split = gas_core.split_day(hours)
        gas = sum([max(h["gas"] or 0.0, 0.0) for h in hours])
        if split["flame_min"] == 0 and gas > 0.05:
            log.warning(f"heating_tuner: no burner history for {date}, not split")
            continue
        split["date"] = date
        split["gas"] = round(gas, 3)
        split["away"] = _away(people, date)
        store.append_episode(GAS_DAYS, split)
        log.info(f"heating_tuner: gas split {split}")


def _gas_summary():
    """Heating gas per degree-day from up to a year of daily statistics."""
    now = dt_util.now()
    try:
        _store_gas_splits(now)
    except Exception as err:
        log.warning(f"heating_tuner: gas split failed: {err}")
    try:
        response = recorder.get_statistics(
            statistic_ids=[E_GAS, E_OUTDOOR],
            start_time=(now - timedelta(days=365)).isoformat(),
            period="day",
            types=["change", "mean"],
            units={},
            return_response=True,
        )
        stats = response["statistics"]
    except Exception as err:
        log.warning(f"heating_tuner: no statistics for gas per degree-day: {err}")
        return None
    gas = {_stat_date(r["start"]): r.get("change") for r in stats.get(E_GAS, [])}
    outdoor = {_stat_date(r["start"]): r.get("mean") for r in stats.get(E_OUTDOOR, [])}
    splits = _read_gas_days()
    s = gas_core.summary(gas_core.build_days(gas, outdoor, now.date().isoformat(), splits))
    w7, w28 = s["w7"] or {}, s["w28"] or {}
    state.set(
        "sensor.heating_gas_per_degree_day",
        w7.get("per_dd") if w7.get("per_dd") is not None else "unknown",
        {
            "friendly_name": "Heating gas per degree-day (7 days)",
            "unit_of_measurement": "m³/°Cd",
            "icon": "mdi:fire",
            "per_dd_28": w28.get("per_dd"),
            "degree_days_7": w7.get("dd"),
            "degree_days_28": w28.get("dd"),
            "gas_7": w7.get("gas"),
            "heating_gas_7": w7.get("heating_gas"),
            "base_per_day": s["base"],
            "base_days": s["base_days"],
            "base_source": s["base_source"],
            "away_days_28": s["away_28"],
            "last_day": s["last"],
        },
    )
    return s


@time_trigger("startup")
def heating_gas_startup():
    _gas_summary()


# ---------------------------------------------------------------- report


@time_trigger(f"once({SUMMARY_TIME})")
@service
def heating_tuner_report(trigger_type=None):
    """Post today's heating episodes and the night prediction as a persistent notification."""
    today = dt_util.now().date().isoformat()
    todays = [e for e in store.read_episodes(EPISODES) if e["start"][:10] == today]
    rec = store.read_state(STATE).get("recommendation")
    p = _params()
    lines = [core.summary_line(e) for e in todays] or ["- no complete warm-up episodes today"]
    # The prediction is made once a day at the summary time; a manual report
    # shows the stored one.
    if trigger_type == "time":
        prediction = _predict()
    else:
        prediction = store.read_state(NIGHT_STATE).get("prediction")
    last_nights = [n for n in store.read_episodes(NIGHTS) if n["end"][:10] == today]
    night_lines = [night_core.night_line(n) for n in last_nights] or ["- no night recorded"]
    if prediction:
        night_lines.append(night_core.prediction_line(prediction))
    gas = _gas_summary()
    message = (
        "\n".join(lines)
        + f"\n\nCurrent: Kp {p['kp']}, Ki {p['ki']}, Kd {p['kd']}"
        + f"\nWould change (observe mode, nothing applied): {_rec_text(rec)}"
        + "\n\nNight (observe mode):\n" + "\n".join(night_lines)
        + ("\n\n" + gas_core.summary_line(gas) if gas else "")
    )
    persistent_notification.create(
        notification_id="heating_tuner_daily",
        title=f"Heating tuner {today}",
        message=message,
    )
