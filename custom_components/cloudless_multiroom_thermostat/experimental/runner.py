"""Experimental features: the HA glue around the analysis modules.

Port of the pyscript heating_tuner app (BarBaar44/thermostat, pyscript/apps).
Observe only: nothing here changes the heating. It

* samples the heat source once a minute and cuts warm-up episodes, judged
  by tuner_core, with Kp/Ki/Kd advice when PID numbers are configured;
* records each night's cooldown of one room and predicts the next morning
  from the hourly forecast (night_core);
* splits each day's gas into hot water and heating and reports heating gas
  per degree-day (gas_core);
* posts a summary as a persistent notification every evening at 21:00.

All files live in <config>/cloudless_multiroom_thermostat/<entry_id>/.
See docs/experimental.md for what each part does and how mature it is.
"""

from __future__ import annotations

import logging
import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from datetime import time as dt_time
from typing import TYPE_CHECKING, Any

from homeassistant.components import persistent_notification
from homeassistant.const import STATE_ON
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.event import (
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from ..const import (
    CONF_EXP_CH_ACTIVE,
    CONF_EXP_COMFORT_WEEKDAY,
    CONF_EXP_COMFORT_WEEKEND,
    CONF_EXP_DHW,
    CONF_EXP_FLAME,
    CONF_EXP_GAS,
    CONF_EXP_GAS_AFTER6,
    CONF_EXP_GAS_BEFORE6,
    CONF_EXP_INTEGRAL,
    CONF_EXP_KD,
    CONF_EXP_KI,
    CONF_EXP_KP,
    CONF_EXP_OUTDOOR,
    CONF_EXP_PEOPLE,
    CONF_EXP_RETURN,
    CONF_EXP_ROOM,
    CONF_EXP_WATER,
    CONF_EXP_WEATHER,
    DEFAULT_COMFORT_WEEKDAY,
    DEFAULT_COMFORT_WEEKEND,
    DOCS_EXPERIMENTAL_URL,
    DOMAIN,
    EXP_SUMMARY_HOUR,
    PAUSE_WINDOW,
    WINNER_OFF,
    signal_update,
)
from . import gas_core, night_core, progress, store, tuner_core

if TYPE_CHECKING:
    from ..controller import HeatingController, Room

_LOGGER = logging.getLogger(__name__)

MIN_NIGHT_HOURS = 4  # shorter heating pauses are not nights
SPLIT_DAYS_BACK = 6  # complete days still fully in the recorder's 7-day history


def signal_experimental(entry_id: str) -> str:
    """Fired when an experimental sensor value changes."""
    return f"{DOMAIN}_{entry_id}_experimental"


def _parse_time(value: str | None, default: str) -> dt_time:
    try:
        return dt_time.fromisoformat(value or default)
    except ValueError:
        return dt_time.fromisoformat(default)


def _stat_dt(start: Any) -> datetime:
    """Statistics rows start as a UTC timestamp (recorder API) or datetime/ISO string."""
    if isinstance(start, (int, float)):
        return dt_util.utc_from_timestamp(start)
    if isinstance(start, str):
        return dt_util.parse_datetime(start)
    return start


def _stat_date(start: Any) -> str:
    """Local ISO date of a daily statistics row (rows start at local midnight)."""
    return dt_util.as_local(_stat_dt(start)).date().isoformat()


def _day_start(date: str) -> datetime:
    return dt_util.start_of_local_day(dt_util.parse_datetime(date + "T12:00:00"))


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class ExperimentalRunner:
    """Observe-only diagnostics for one heating system."""

    def __init__(self, hass: HomeAssistant, controller: HeatingController) -> None:
        self.hass = hass
        self.ctl = controller
        entry_id = controller.entry.entry_id
        self.dir = hass.config.path(DOMAIN, entry_id)
        self.f_episodes = f"{self.dir}/episodes.jsonl"
        self.f_state = f"{self.dir}/state.json"
        self.f_nights = f"{self.dir}/nights.jsonl"
        self.f_night_state = f"{self.dir}/night_state.json"
        self.f_gas_days = f"{self.dir}/gas_days.jsonl"
        self.signal = signal_experimental(entry_id)
        # Values for the experimental sensors: key -> (state, attributes)
        self.values: dict[str, tuple[Any, dict[str, Any]]] = {}
        self._unsubs: list[CALLBACK_TYPE] = []
        # In memory only: an episode in progress is lost on a restart, which
        # is fine, a restart mid-episode would make it unreliable anyway.
        self._episode: dict[str, Any] | None = None
        self._last_target: float | None = None
        # The night in progress is saved every sample (long, restarts likely).
        self._night: dict[str, Any] | None = None
        self._heating_on: bool | None = None
        # Serialises read-modify-write of the night state file
        self._night_lock = asyncio.Lock()

    # ------------------------------------------------------------- settings
    @property
    def _s(self) -> dict[str, Any]:
        return self.ctl.settings

    def _entity(self, key: str) -> str | None:
        return self._s.get(key) or None

    @property
    def room(self) -> Room | None:
        """The room the night model and the tuning follow."""
        room_id = self._s.get(CONF_EXP_ROOM)
        if room_id in self.ctl.rooms:
            return self.ctl.rooms[room_id]
        return next(iter(self.ctl.rooms.values()), None)

    @property
    def tuned_areas(self) -> tuple[str, ...]:
        return (self.room.name,) if self.room else ()

    # ------------------------------------------------------------- readers
    def _float(self, key: str, scale: float = 1.0) -> float | None:
        entity_id = self._entity(key)
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        value = _num(state.state) if state else None
        return value * scale if value is not None else None

    def _is_on(self, key: str) -> bool:
        entity_id = self._entity(key)
        state = self.hass.states.get(entity_id) if entity_id else None
        return state is not None and state.state == STATE_ON

    def _any_heating(self) -> bool:
        """What the CH enable switch follows: some room is set to heat and not held off."""
        return any(self.ctl.is_heating(room) for room in self.ctl.rooms.values())

    def _params(self) -> dict[str, float | None]:
        return {"kp": self._float(CONF_EXP_KP), "ki": self._float(CONF_EXP_KI), "kd": self._float(CONF_EXP_KD)}

    def _sample(self) -> dict[str, Any]:
        winner = self.ctl.winner
        room = self.room
        leader = self.ctl.rooms.get(winner.room_id) if winner.room_id else None
        return {
            "t": dt_util.now().isoformat(timespec="seconds"),
            "target": winner.target,
            # Live temperature of the winning room (the PID process value)
            "room": leader.current if leader else None,
            "area": winner.name if winner.room_id else WINNER_OFF,
            "flame": self._is_on(CONF_EXP_FLAME),
            "dhw": self._is_on(CONF_EXP_DHW),
            "water": self._float(CONF_EXP_WATER),
            "ret": self._float(CONF_EXP_RETURN),
            "people": self._float(CONF_EXP_PEOPLE),
            "outdoor": self._float(CONF_EXP_OUTDOOR),
            # ESPHome reports the PID terms x100
            "integral": self._float(CONF_EXP_INTEGRAL, 0.01),
            # Kept as "garden_paused" so records match the pyscript version
            "garden_paused": bool(room and self.ctl.pause_reason(room) == PAUSE_WINDOW),
            "gas": self._float(CONF_EXP_GAS),
        }

    def _night_sample(self) -> dict[str, Any]:
        room = self.room
        weather = self._entity(CONF_EXP_WEATHER)
        wstate = self.hass.states.get(weather) if weather else None
        sun = self.hass.states.get("sun.sun")
        return {
            "t": dt_util.now().isoformat(timespec="seconds"),
            "room": room.current if room else None,
            "outdoor": self._float(CONF_EXP_OUTDOOR),
            "wind": _num(wstate.attributes.get("wind_speed")) if wstate else None,
            "garden_open": bool(room and room.window_open),
            "sun_up": sun is not None and sun.state == "above_horizon",
            "people": self._float(CONF_EXP_PEOPLE),
        }

    # ------------------------------------------------------------ lifecycle
    async def async_start(self) -> None:
        self._unsubs.append(
            async_track_time_interval(self.hass, self._async_tick, timedelta(seconds=60))
        )
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._async_night_sample, timedelta(minutes=night_core.SAMPLE_MIN)
            )
        )
        self._unsubs.append(
            async_track_time_change(
                self.hass, self._async_scheduled_report, hour=EXP_SUMMARY_HOUR, minute=0, second=0
            )
        )
        self._unsubs.append(
            async_dispatcher_connect(
                self.hass, signal_update(self.ctl.entry.entry_id), self._on_controller_update
            )
        )
        self._unsubs.append(async_at_started(self.hass, self._async_on_started))

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    async def _async_on_started(self, _hass: HomeAssistant) -> None:
        await self._async_resume_night()
        self._heating_on = self._any_heating()
        self.hass.async_create_background_task(self._async_gas_summary(), f"{DOMAIN} gas summary")

    async def _io(self, func: Callable[..., Any], *args: Any) -> Any:
        return await self.hass.async_add_executor_job(func, *args)

    def _set(self, key: str, value: Any, attrs: dict[str, Any]) -> None:
        self.values[key] = (value, attrs)
        async_dispatcher_send(self.hass, self.signal)

    # ------------------------------------------------------------- episodes
    async def _async_tick(self, _now: Any = None) -> None:
        s = self._sample()
        ch_enabled = self._any_heating()
        raised = self._last_target is not None and s["target"] is not None and s["target"] > self._last_target
        self._last_target = s["target"]

        if self._episode is None:
            if (
                ch_enabled
                and s["target"] is not None
                and s["room"] is not None
                and s["area"] != WINNER_OFF
                and s["room"] < s["target"] - 0.1
            ):
                self._episode = {
                    "samples": [s],
                    "reason": "target_raised" if raised else "cooled_down",
                    "reached": None,
                }
            return

        episode = self._episode
        episode["samples"].append(s)
        n = len(episode["samples"])
        if (
            episode["reached"] is None
            and s["room"] is not None
            and s["target"] is not None
            and s["room"] >= s["target"]
        ):
            episode["reached"] = n - 1

        if not ch_enabled:
            await self._async_finish(
                ["heating_stopped"]
                if episode["reached"] is None or n - episode["reached"] < tuner_core.POST_REACH_MIN
                else []
            )
        elif episode["reached"] is not None and n - episode["reached"] >= tuner_core.POST_REACH_MIN:
            await self._async_finish([])
        elif episode["reached"] is None and n >= tuner_core.MAX_RISE_MIN:
            await self._async_finish([])

    async def _async_finish(self, extra_reasons: list[str]) -> None:
        ep, self._episode = self._episode, None
        if ep is None:
            return
        params = self._params()
        record = tuner_core.analyse(ep["samples"], params, ep["reason"], extra_reasons, self.tuned_areas)
        await self._io(store.append_episode, self.f_episodes, record)
        history = await self._io(store.read_episodes, self.f_episodes)
        rec = tuner_core.recommend(history, params) if all(v is not None for v in params.values()) else None
        tuner_state = await self._io(store.read_state, self.f_state)
        tuner_state["last_episode"] = record["start"]
        tuner_state["recommendation"] = rec
        await self._io(store.write_state, self.f_state, tuner_state)
        self._publish_episode(record, rec)
        _LOGGER.info(
            "Episode %s valid=%s overshoot=%s rec=%s",
            record["start"], record["valid"], record["overshoot"], rec,
        )

    def _rec_text(self, rec: dict[str, Any] | None) -> str:
        if not all(v is not None for v in self._params().values()):
            return "no PID numbers configured"
        if rec is None:
            return "no change"
        if rec["at_limit"]:
            return f"{rec['param']} at its limit ({rec['old']}) - {rec['why']}"
        return f"{rec['param']} {rec['old']} -> {rec['new']} ({rec['why']})"

    def _publish_episode(self, record: dict[str, Any], rec: dict[str, Any] | None) -> None:
        self._set(
            "last_overshoot",
            record["overshoot"],
            {
                "room": record["area"],
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
        self._set("recommendation", self._rec_text(rec)[:255], {"mode": "observe"})

    # --------------------------------------------------------------- nights
    @callback
    def _on_controller_update(self) -> None:
        heating = self._any_heating()
        previous, self._heating_on = self._heating_on, heating
        if previous is None or heating == previous:
            return
        if heating:
            if self._night is not None:
                self.hass.async_create_task(self._async_finish_night("heating_on"))
        elif self._night is None and night_core.starts_night(dt_util.now().hour):
            self._night = {"samples": [self._night_sample()], "gas_start": self._float(CONF_EXP_GAS)}
            self.hass.async_create_task(self._async_save_night())

    async def _async_update_night_state(self, **changes: Any) -> dict[str, Any]:
        """Read, change and write night_state.json; returns the state before the change."""
        async with self._night_lock:
            night_state = await self._io(store.read_state, self.f_night_state)
            before = dict(night_state)
            night_state.update(changes)
            await self._io(store.write_state, self.f_night_state, night_state)
            return before

    async def _async_save_night(self) -> None:
        await self._async_update_night_state(night=self._night)

    async def _async_resume_night(self) -> None:
        """Pick up a night that was in progress before a restart."""
        saved = (await self._io(store.read_state, self.f_night_state)).get("night")
        if not saved:
            return
        self._night = saved
        if self._any_heating():
            await self._async_finish_night("heating_on")
        else:
            _LOGGER.info("Resumed night started %s", saved["samples"][0]["t"])

    async def _async_night_sample(self, _now: Any = None) -> None:
        if self._night is None:
            return
        self._night["samples"].append(self._night_sample())
        hour = dt_util.now().hour
        if night_core.NIGHT_MAX_END_HOUR <= hour < night_core.NIGHT_START_HOURS[0]:
            await self._async_finish_night("timeout")
        else:
            await self._async_save_night()

    async def _async_finish_night(self, end_reason: str) -> None:
        n, self._night = self._night, None
        if n is None:
            return
        n["samples"].append(self._night_sample())
        prediction = (await self._async_update_night_state(night=None)).get("prediction")

        samples = n["samples"]
        hours = (
            dt_util.parse_datetime(samples[-1]["t"]) - dt_util.parse_datetime(samples[0]["t"])
        ).total_seconds() / 3600
        if hours < MIN_NIGHT_HOURS:
            _LOGGER.info("Heating pause of %.1f h from %s is not a night", hours, samples[0]["t"])
            return
        # Only compare with the prediction made for this night
        if prediction and not (samples[0]["t"][:10] <= prediction["made"] <= samples[-1]["t"]):
            prediction = None
        record = night_core.analyse(
            samples, end_reason, prediction, n.get("gas_start"), self._float(CONF_EXP_GAS)
        )
        await self._io(store.append_episode, self.f_nights, record)
        _LOGGER.info(
            "Night %s valid=%s tau_h=%s error=%s",
            record["start"], record["valid"], record["tau_h"], record["prediction_error"],
        )

    def _comfort_at(self, now: datetime) -> datetime:
        day = now + timedelta(days=1) if now.hour >= 12 else now
        weekend = day.weekday() >= 5
        comfort = _parse_time(
            self._s.get(CONF_EXP_COMFORT_WEEKEND if weekend else CONF_EXP_COMFORT_WEEKDAY),
            DEFAULT_COMFORT_WEEKEND if weekend else DEFAULT_COMFORT_WEEKDAY,
        )
        return day.replace(hour=comfort.hour, minute=comfort.minute, second=0, microsecond=0)

    async def _async_predict(self) -> dict[str, Any] | None:
        room = self.room
        if room is None:
            return None
        now = dt_util.now()
        forecast: list[tuple[str, float | None]] = []
        weather = self._entity(CONF_EXP_WEATHER)
        if weather:
            try:
                response = await self.hass.services.async_call(
                    "weather",
                    "get_forecasts",
                    {"entity_id": weather, "type": "hourly"},
                    blocking=True,
                    return_response=True,
                )
                forecast = [(f["datetime"], f.get("temperature")) for f in response[weather]["forecast"]]
            except Exception as err:  # noqa: BLE001 - no forecast only means no prediction
                _LOGGER.warning("No hourly forecast from %s: %s", weather, err)
        prediction = night_core.predict(
            room.current,
            now.isoformat(timespec="seconds"),
            forecast,
            await self._io(store.read_episodes, self.f_nights),
            await self._io(store.read_episodes, self.f_episodes),
            self._comfort_at(now).isoformat(timespec="seconds"),
            self._float(CONF_EXP_GAS_BEFORE6),
            self._float(CONF_EXP_GAS_AFTER6),
            room=room.name,
            morning_target=room.warm_temp,
        )
        await self._async_update_night_state(prediction=prediction)
        self._set(
            "night_prediction",
            prediction["suggested_start"][11:16] if prediction["suggested_start"] else "learning",
            {
                "mode": "observe",
                "room": room.name,
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

    # ---------------------------------------------------- gas per degree-day
    async def _async_read_gas_days(self) -> dict[str, dict[str, Any]]:
        """Stored gas days by date; a later line for a date updates the earlier one."""
        days: dict[str, dict[str, Any]] = {}
        for d in await self._io(store.read_episodes, self.f_gas_days, 800):
            days.setdefault(d["date"], {}).update(d)
        return days

    async def _async_statistics(
        self, ids: set[str], start: datetime, end: datetime | None, period: str, types: set[str]
    ) -> dict[str, list[dict[str, Any]]]:
        from homeassistant.components.recorder import get_instance  # noqa: PLC0415
        from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
            statistics_during_period,
        )

        return await get_instance(self.hass).async_add_executor_job(
            statistics_during_period, self.hass, start, end, ids, period, None, types
        )

    async def _async_history(self, ids: list[str], start: datetime, end: datetime) -> dict[str, list[Any]]:
        from homeassistant.components.recorder import get_instance, history  # noqa: PLC0415

        return await get_instance(self.hass).async_add_executor_job(
            lambda: history.get_significant_states(
                self.hass,
                dt_util.as_utc(start),
                dt_util.as_utc(end),
                ids,
                None,
                include_start_time_state=True,
                significant_changes_only=False,
                minimal_response=False,
                no_attributes=True,
            )
        )

    def _away(self, people: list[tuple[datetime, float | None]], date: str) -> bool:
        """True if nobody was home the whole of `date`."""
        if not self._entity(CONF_EXP_PEOPLE):
            return False
        d0 = _day_start(date)
        return gas_core.nobody_home(people, d0, d0 + timedelta(days=1))

    async def _async_store_gas_splits(self, now: datetime) -> None:
        """Split each complete recent day not stored yet; add the away flag where missing."""
        gas_id = self._entity(CONF_EXP_GAS)
        flame, dhw, ch = (self._entity(k) for k in (CONF_EXP_FLAME, CONF_EXP_DHW, CONF_EXP_CH_ACTIVE))
        if not (gas_id and flame and dhw and ch):
            return
        people_id = self._entity(CONF_EXP_PEOPLE)
        stored = await self._async_read_gas_days()
        midnight = dt_util.start_of_local_day(now)
        window = [(midnight - timedelta(days=n)).date().isoformat() for n in range(SPLIT_DAYS_BACK, 0, -1)]
        dates = [d for d in window if "dhw_gas" not in stored.get(d, {})]
        unflagged = [d for d in window if d in stored and "away" not in stored[d]]
        if not dates and not unflagged:
            return
        start = _day_start(min(dates + unflagged))
        stats = await self._async_statistics({gas_id}, start, midnight, "hour", {"change"})
        ids = [ch, dhw, flame] + ([people_id] if people_id else [])
        states = await self._async_history(ids, start, midnight)
        changes = {eid: [(st.last_changed, st.state == STATE_ON) for st in states.get(eid, [])] for eid in (ch, dhw, flame)}
        people = [(st.last_changed, _num(st.state)) for st in states.get(people_id, [])] if people_id else []

        for date in unflagged:
            flag = {"date": date, "away": self._away(people, date)}
            await self._io(store.append_episode, self.f_gas_days, flag)

        for date in dates:
            hours = []
            for r in stats.get(gas_id, []):
                if _stat_date(r["start"]) != date:
                    continue
                h0 = _stat_dt(r["start"])
                h1 = h0 + timedelta(hours=1)
                hours.append(
                    {
                        "gas": r.get("change"),
                        "ch": gas_core.on_minutes(changes[ch], h0, h1),
                        "dhw": gas_core.on_minutes(changes[dhw], h0, h1),
                        "flame": gas_core.on_minutes(changes[flame], h0, h1),
                    }
                )
            if len(hours) < 23:  # 23 on the spring DST day
                _LOGGER.debug("%d gas hours for %s, not split", len(hours), date)
                continue
            split = gas_core.split_day(hours)
            gas = sum(max(h["gas"] or 0.0, 0.0) for h in hours)
            if split["flame_min"] == 0 and gas > 0.05:
                _LOGGER.warning("No burner history for %s, gas not split", date)
                continue
            split["date"] = date
            split["gas"] = round(gas, 3)
            split["away"] = self._away(people, date)
            await self._io(store.append_episode, self.f_gas_days, split)

    async def _async_gas_summary(self) -> dict[str, Any] | None:
        """Heating gas per degree-day from up to a year of daily statistics."""
        gas_id, outdoor_id = self._entity(CONF_EXP_GAS), self._entity(CONF_EXP_OUTDOOR)
        if not (gas_id and outdoor_id):
            return None
        now = dt_util.now()
        try:
            await self._async_store_gas_splits(now)
        except Exception as err:  # noqa: BLE001 - the summary works without splits
            _LOGGER.warning("Gas split failed: %s", err)
        try:
            stats = await self._async_statistics(
                {gas_id, outdoor_id}, now - timedelta(days=365), None, "day", {"change", "mean"}
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("No statistics for gas per degree-day: %s", err)
            return None
        gas = {_stat_date(r["start"]): r.get("change") for r in stats.get(gas_id, [])}
        outdoor = {_stat_date(r["start"]): r.get("mean") for r in stats.get(outdoor_id, [])}
        splits = await self._async_read_gas_days()
        s = gas_core.summary(gas_core.build_days(gas, outdoor, now.date().isoformat(), splits))
        w7, w28 = s["w7"] or {}, s["w28"] or {}
        self._set(
            "gas_per_degree_day",
            w7.get("per_dd"),
            {
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

    # --------------------------------------------------------------- report
    async def _async_scheduled_report(self, _now: Any = None) -> None:
        await self.async_report(predict=True)

    async def async_report(self, predict: bool = False) -> None:
        """Post today's episodes, the night and gas as a persistent notification."""
        today = dt_util.now().date().isoformat()
        episodes = await self._io(store.read_episodes, self.f_episodes)
        todays = [e for e in episodes if e["start"][:10] == today]
        rec = (await self._io(store.read_state, self.f_state)).get("recommendation")
        p = self._params()
        lines = [tuner_core.summary_line(e) for e in todays] or ["- no complete warm-up episodes today"]
        # The prediction is made once a day at 21:00; a manual report shows the stored one.
        if predict:
            prediction = await self._async_predict()
        else:
            prediction = (await self._io(store.read_state, self.f_night_state)).get("prediction")
        nights = await self._io(store.read_episodes, self.f_nights)
        night_lines = [night_core.night_line(n) for n in nights if n["end"][:10] == today] or ["- no night recorded"]
        if prediction:
            night_lines.append(night_core.prediction_line(prediction))
        gas = await self._async_gas_summary()

        message = "\n".join(lines)
        if all(v is not None for v in p.values()):
            message += f"\n\nCurrent: Kp {p['kp']}, Ki {p['ki']}, Kd {p['kd']}"
        message += f"\nWould change (observe mode, nothing applied): {self._rec_text(rec)}"
        room = self.room
        message += f"\n\nNight, {room.name if room else 'no room'} (observe mode):\n" + "\n".join(night_lines)
        if gas:
            message += "\n\n" + gas_core.summary_line(gas)
        else:
            message += "\n\nGas per degree-day: set a gas meter and an outdoor sensor to enable"
        message += "\n\nProgress to stable:\n" + "\n".join(
            progress.report_lines(gas, nights, episodes)
        )
        message += f"\n\n[What these mean]({DOCS_EXPERIMENTAL_URL})"
        persistent_notification.async_create(
            self.hass,
            message,
            title=f"{self.ctl.entry.title}: experimental report {today}",
            notification_id=f"{DOMAIN}_{self.ctl.entry.entry_id}_report",
        )
