"""Experimental features: options, warm-up episodes, nights, gas split, report."""

from __future__ import annotations

from datetime import timedelta
import json
import os
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.cloudless_multiroom_thermostat.const import DOCS_EXPERIMENTAL_URL, DOMAIN
from custom_components.cloudless_multiroom_thermostat.experimental import night_core, tuner_core
from custom_components.cloudless_multiroom_thermostat.experimental import runner as runner_mod
from custom_components.cloudless_multiroom_thermostat.experimental import store

from .test_integration import HALLWAY, LIVING, OFFICE, SYSTEM, _states

EXP = {
    "experimental": True,
    "exp_comfort_weekday": "07:30:00",
    "exp_comfort_weekend": "08:00:00",
    "exp_outdoor": "sensor.outdoor",
    "exp_gas": "sensor.gas",
    "exp_flame": "binary_sensor.flame",
    "exp_dhw": "binary_sensor.dhw",
    "exp_ch_active": "binary_sensor.ch_active",
    "exp_people": "zone.home",
    "exp_kp": "number.kp",
    "exp_ki": "number.ki",
    "exp_kd": "number.kd",
}


async def _calls_noop(self, domain, service, data, key=None):
    return None


async def _setup(hass: HomeAssistant, options: dict, extra_rooms: tuple[dict, ...] = ()) -> MockConfigEntry:
    _states(hass)
    hass.states.async_set("sensor.outdoor", "6.0")
    hass.states.async_set("sensor.gas", "1000.0")
    hass.states.async_set("binary_sensor.flame", "off")
    hass.states.async_set("binary_sensor.dhw", "off")
    hass.states.async_set("binary_sensor.ch_active", "off")
    hass.states.async_set("zone.home", "2")
    hass.states.async_set("number.kp", "0.7")
    hass.states.async_set("number.ki", "0.003")
    hass.states.async_set("number.kd", "0")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Heating",
        data=SYSTEM,
        options=options,
        subentries_data=[
            ConfigSubentryData(data=LIVING, subentry_type="room", title="Living", unique_id=None),
            ConfigSubentryData(data=OFFICE, subentry_type="room", title="Office", unique_id=None),
            *(
                ConfigSubentryData(data=room, subentry_type="room", title=room["name"], unique_id=None)
                for room in extra_rooms
            ),
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture(autouse=True)
def _tmp_config_dir(hass: HomeAssistant, tmp_path):
    """Keep the experimental files out of the shared testing_config folder."""
    hass.config.config_dir = str(tmp_path)


@pytest.fixture(autouse=True)
def _no_device_writes():
    with patch(
        "custom_components.cloudless_multiroom_thermostat.controller.HeatingController._call",
        _calls_noop,
    ):
        yield


def _living_id(entry) -> str:
    return next(s.subentry_id for s in entry.subentries.values() if s.title == "Living")


async def _minute(hass: HomeAssistant, runner, temp: float, flame: bool = True) -> None:
    """One minute of a warm-up: new room temperature, the controller's debounced update, a tick."""
    hass.states.async_set("sensor.living_temp", f"{temp:.2f}", {"device_class": "temperature"})
    hass.states.async_set("binary_sensor.flame", "on" if flame else "off")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()
    await runner._async_tick()


async def _start_episode(hass: HomeAssistant, entry, minutes: int):
    runner = entry.runtime_data.experimental
    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": "climate.living", "hvac_mode": "heat"}, blocking=True
    )
    for minute in range(minutes):
        await _minute(hass, runner, 18.0 + 0.01 * minute)
    assert runner._episode is not None
    assert len(runner._episode["samples"]) == minutes
    return runner


async def test_off_by_default_no_sensors(hass: HomeAssistant) -> None:
    entry = await _setup(hass, {})
    assert entry.runtime_data.experimental is None
    assert hass.states.get("sensor.heating_gas_per_degree_day") is None


async def test_options_enable_experimental(hass: HomeAssistant) -> None:
    entry = await _setup(hass, {})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert DOCS_EXPERIMENTAL_URL == result["description_placeholders"]["docs_url"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "heat_source_entity": "climate.boiler",
            "off_temperature": 10,
            "away_mode": "off",
            "skip_unchanged_writes": True,
            "external_temp_heartbeat": 60,
            "experimental": True,
        },
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "experimental"
    fields = {str(k): v for k, v in result["data_schema"].schema.items()}
    labels = [o["label"] for o in fields["exp_room"].config["options"]]
    assert labels == ["Living", "Office"]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "exp_room": _living_id(entry),
            "exp_comfort_weekday": "07:00:00",
            "exp_comfort_weekend": "08:30:00",
            "exp_gas": "sensor.gas",
            "exp_outdoor": "sensor.outdoor",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["exp_gas"] == "sensor.gas" and entry.options["exp_flame"] is None
    assert entry.options["exp_dhw_rate"] is None  # empty: measured automatically
    assert entry.runtime_data.experimental is not None
    for name in ("gas_per_degree_day", "night_prediction", "last_warm_up_overshoot", "pid_advice"):
        assert hass.states.get(f"sensor.heating_{name}") is not None, name

    # Switching off keeps the experimental settings for next time
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "heat_source_entity": "climate.boiler",
            "off_temperature": 10,
            "away_mode": "off",
            "skip_unchanged_writes": True,
            "external_temp_heartbeat": 60,
            "experimental": False,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data.experimental is None
    assert entry.options["exp_gas"] == "sensor.gas" and entry.options["exp_comfort_weekday"] == "07:00:00"


async def test_warm_up_episode_recorded(hass: HomeAssistant) -> None:
    entry = await _setup(hass, {**EXP, "exp_room": None})
    runner = entry.runtime_data.experimental
    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": "climate.living", "hvac_mode": "heat"}, blocking=True
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()
    assert entry.runtime_data.winner.name == "Living"

    temp = 18.0
    for minute in range(140):
        if minute < 40:
            temp += 0.03  # reaches 19.0 after about 34 minutes
        await _minute(hass, runner, temp, flame=minute % 20 < 15)
    assert entry.runtime_data.winner.name == "Off", "the winner turns Off once the room reaches target"
    assert runner._episode is None, "episode should finish 90 minutes after reaching target"

    records = [json.loads(line) for line in open(runner.f_episodes, encoding="utf-8")]
    assert len(records) == 1
    record = records[0]
    assert record["area"] == "Living" and record["room_class"] == "living"
    assert record["room_id"] == _living_id(entry)
    assert record["rise_min"] is not None and record["flame_starts"] >= 1
    # The episode follows the room, not the winner that turned Off at target
    assert record["overshoot"] is not None and record["overshoot"] > 0
    for reason in ("other_room", "target_changed", "target_not_reached"):
        assert reason not in record["invalid_reasons"]
    await hass.async_block_till_done()
    state = hass.states.get("sensor.heating_last_warm_up_overshoot")
    assert state.attributes["room"] == "Living" and state.attributes["experimental"] is True
    assert hass.states.get("sensor.heating_pid_advice").state != "unknown"


async def test_episode_saved_while_running(hass: HomeAssistant) -> None:
    """Saved every few samples, so even a crash loses at most a few minutes."""
    entry = await _setup(hass, {**EXP, "exp_room": None})
    runner = await _start_episode(hass, entry, 10)
    saved = json.load(open(runner.f_episode_state, encoding="utf-8"))
    assert len(saved["episode"]["samples"]) == 10
    assert saved["episode"]["room_id"] == _living_id(entry)


async def test_episode_survives_restart(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    entry = await _setup(hass, {**EXP, "exp_room": None})
    runner = await _start_episode(hass, entry, 12)
    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.tick(timedelta(minutes=3))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    resumed = entry.runtime_data.experimental
    assert resumed is not runner
    # 12 saved samples plus 2 filled minutes; the next tick adds the third
    assert len(resumed._episode["samples"]) == 14
    assert resumed._episode["room_id"] == _living_id(entry)
    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": "climate.living", "hvac_mode": "heat"}, blocking=True
    )
    await _minute(hass, resumed, 18.2)
    assert len(resumed._episode["samples"]) == 15


async def test_episode_dropped_after_long_gap(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    entry = await _setup(hass, {**EXP, "exp_room": None})
    runner = await _start_episode(hass, entry, 6)
    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.tick(timedelta(minutes=runner_mod.EPISODE_MAX_GAP_MIN + 5))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.experimental._episode is None
    assert json.load(open(runner.f_episode_state, encoding="utf-8")) == {}


async def test_night_recorded(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.now().replace(hour=19, minute=0, second=0, microsecond=0)))
    entry = await _setup(hass, EXP)
    runner = entry.runtime_data.experimental
    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": "climate.living", "hvac_mode": "heat"}, blocking=True
    )
    await hass.async_block_till_done()
    runner._heating_on = True

    freezer.move_to(dt_util.as_utc(dt_util.now().replace(hour=20, minute=0)))
    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": "climate.living", "hvac_mode": "off"}, blocking=True
    )
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert runner._night is not None, "heating off in the evening starts a night"
    saved = json.load(open(runner.f_night_state, encoding="utf-8"))
    assert saved["night"]["samples"]

    temp = 19.0
    for _ in range(64):  # 10-minute samples until about 06:40
        freezer.tick(timedelta(minutes=10))
        temp -= 0.05
        hass.states.async_set("sensor.living_temp", f"{temp:.2f}", {"device_class": "temperature"})
        await hass.async_block_till_done()
        await runner._async_night_sample()
    assert runner._night is not None

    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": "climate.living", "hvac_mode": "heat"}, blocking=True
    )
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert runner._night is None
    nights = [json.loads(line) for line in open(runner.f_nights, encoding="utf-8")]
    assert len(nights) == 1 and nights[0]["end_reason"] == "heating_on"


async def test_night_starts_while_a_non_driving_room_heats(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A room with "Drives the heat source" off (a hallway on eco all night) doesn't block the night."""
    freezer.move_to(dt_util.as_utc(dt_util.now().replace(hour=19, minute=0, second=0, microsecond=0)))
    entry = await _setup(hass, EXP, extra_rooms=(HALLWAY,))
    runner = entry.runtime_data.experimental
    for entity_id in ("climate.living", "climate.hallway"):
        await hass.services.async_call(
            "climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": "heat"}, blocking=True
        )
    await hass.async_block_till_done()
    assert runner._any_heating()
    runner._heating_on = True

    freezer.move_to(dt_util.as_utc(dt_util.now().replace(hour=20, minute=0)))
    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": "climate.living", "hvac_mode": "off"}, blocking=True
    )
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("climate.hallway").state == "heat"
    assert not runner._any_heating(), "the hallway doesn't drive the heat source"
    assert runner._night is not None, "the living room going off starts a night"


async def test_gas_split_from_recorder_data(hass: HomeAssistant) -> None:
    entry = await _setup(hass, EXP)
    runner = entry.runtime_data.experimental
    now = dt_util.now()
    midnight = dt_util.start_of_local_day(now)
    day0 = midnight - timedelta(days=1)

    stats = {
        "sensor.gas": [
            # recorder rows start as UTC timestamps
            {"start": (day0 + timedelta(hours=h)).timestamp(),
             "change": 0.2 if 6 <= h < 9 else 0.05 if h == 20 else 0.0}
            for h in range(24)
        ]
    }

    class St:
        def __init__(self, when, state):
            self.last_changed = when
            self.state = state

    history = {
        "binary_sensor.ch_active": [St(day0, "off"), St(day0 + timedelta(hours=6), "on"), St(day0 + timedelta(hours=9), "off")],
        "binary_sensor.dhw": [St(day0, "off"), St(day0 + timedelta(hours=20), "on"), St(day0 + timedelta(hours=20, minutes=10), "off")],
        "binary_sensor.flame": [St(day0, "off"), St(day0 + timedelta(hours=6), "on"), St(day0 + timedelta(hours=9), "off"),
                                St(day0 + timedelta(hours=20), "on"), St(day0 + timedelta(hours=20, minutes=10), "off")],
        "zone.home": [St(day0, "2")],
    }

    async def fake_stats(ids, start, end, period, types):
        return stats

    async def fake_history(ids, start, end):
        return history

    with patch.object(runner, "_async_statistics", side_effect=fake_stats), patch.object(
        runner, "_async_history", side_effect=fake_history
    ):
        await runner._async_store_gas_splits(now)

    days = await runner._async_read_gas_days()
    day = days[day0.date().isoformat()]
    assert day["away"] is False
    assert day["heating_gas"] == pytest.approx(0.6, abs=0.001)
    assert day["dhw_gas"] == pytest.approx(0.05, abs=0.001)
    assert day["ch_min"] == 180 and day["flame_min"] == 190
    assert day["dhw_rate"] == pytest.approx(0.019) and day["dhw_rate_source"] == "default"
    # the 10-minute shower at 20:00 sits between quiet hours, so it is a rate sample
    assert day["rate_min"] == pytest.approx(10) and day["rate_gas"] == pytest.approx(0.05, abs=0.001)


async def test_report_notification(hass: HomeAssistant) -> None:
    await _setup(hass, {**EXP, "exp_gas": None})
    created = []
    with patch.object(
        runner_mod.persistent_notification, "async_create", side_effect=lambda h, m, **kw: created.append((m, kw))
    ):
        await hass.services.async_call(DOMAIN, "experimental_report", {}, blocking=True)
    assert len(created) == 1
    message, kw = created[0]
    assert "no complete warm-up episodes today" in message
    assert "Night, Living" in message
    assert "set a gas meter and an outdoor sensor" in message
    assert "Progress to stable:" in message
    assert "Night model: 0 of 30 predicted nights" in message
    assert DOCS_EXPERIMENTAL_URL in message
    assert kw["notification_id"].endswith("_report")


async def test_evening_prediction_uses_forecast(hass: HomeAssistant) -> None:
    from homeassistant.core import SupportsResponse

    entry = await _setup(hass, {**EXP, "exp_weather": "weather.home"})
    runner = entry.runtime_data.experimental
    asked = []

    async def get_forecasts(call):
        asked.append(call.data)
        start = dt_util.utcnow()
        return {"weather.home": {"forecast": [
            {"datetime": (start + timedelta(hours=h)).isoformat(), "temperature": 5.0} for h in range(24)
        ]}}

    hass.services.async_register("weather", "get_forecasts", get_forecasts, supports_response=SupportsResponse.ONLY)
    created = []
    with patch.object(
        runner_mod.persistent_notification, "async_create", side_effect=lambda h, m, **kw: created.append(m)
    ):
        await runner._async_scheduled_report()
    await hass.async_block_till_done()
    assert asked and asked[0]["type"] == "hourly"
    state = hass.states.get("sensor.heating_night_prediction")
    assert state.state == "learning"  # no nights recorded yet
    assert state.attributes["room"] == "Living" and state.attributes["outdoor_min"] == 5.0
    saved = json.load(open(runner.f_night_state, encoding="utf-8"))
    assert saved["prediction"]["morning_target"] == 19.0  # the room's warm temperature
    assert "Tonight" in created[0]


async def _forecast_retry_setup(hass: HomeAssistant, fail_times: int, extra: dict | None = None):
    """A runner whose forecast fails the first fail_times calls, with retries captured."""
    from homeassistant.core import SupportsResponse
    from homeassistant.exceptions import ServiceValidationError

    entry = await _setup(hass, {**EXP, "exp_weather": "weather.home", **(extra or {})})
    asked = []

    async def get_forecasts(call):
        asked.append(call.data)
        if len(asked) <= fail_times:
            raise ServiceValidationError("no entity")
        start = dt_util.utcnow()
        return {"weather.home": {"forecast": [
            {"datetime": (start + timedelta(hours=h)).isoformat(), "temperature": 5.0} for h in range(24)
        ]}}

    hass.services.async_register("weather", "get_forecasts", get_forecasts, supports_response=SupportsResponse.ONLY)
    return entry.runtime_data.experimental, asked


async def test_forecast_retry_replaces_report_when_it_works(hass: HomeAssistant) -> None:
    runner, asked = await _forecast_retry_setup(hass, fail_times=1)
    created, retries = [], []

    def call_later(_hass, delay, action):
        retries.append((delay, action))
        return lambda: None

    with (
        patch.object(runner_mod.persistent_notification, "async_create", side_effect=lambda h, m, **kw: created.append(m)),
        patch.object(runner_mod, "async_call_later", side_effect=call_later),
    ):
        await runner._async_scheduled_report()
        saved = json.load(open(runner.f_night_state, encoding="utf-8"))
        assert saved["prediction"]["outdoor_min"] is None
        first = saved["prediction"]
        assert first["room_now"] is not None
        assert [d for d, _ in retries] == [timedelta(hours=1)]
        # The room cools before the retry; the retry still starts from 21:00
        for sensor in ("sensor.living_temp", "sensor.office_temp", "sensor.hallway_temp"):
            hass.states.async_set(sensor, "12.0", {"device_class": "temperature"})
        await hass.async_block_till_done()
        await retries[0][1](dt_util.utcnow())
    assert len(asked) == 2 and len(retries) == 1  # worked: no further retry
    assert len(created) == 2  # the report is replaced
    saved = json.load(open(runner.f_night_state, encoding="utf-8"))
    assert saved["prediction"]["outdoor_min"] == 5.0
    # The retry predicts as of the first attempt: the evening drop runs from 21:00
    assert saved["prediction"]["made"] == first["made"]
    assert saved["prediction"]["room_now"] == first["room_now"]


async def test_forecast_retry_stops_after_two_repeats(hass: HomeAssistant) -> None:
    runner, asked = await _forecast_retry_setup(hass, fail_times=99)
    created, retries = [], []

    def call_later(_hass, delay, action):
        retries.append(action)
        return lambda: None

    with (
        patch.object(runner_mod.persistent_notification, "async_create", side_effect=lambda h, m, **kw: created.append(m)),
        patch.object(runner_mod, "async_call_later", side_effect=call_later),
    ):
        await runner._async_scheduled_report()
        await retries[0](dt_util.utcnow())
        await retries[1](dt_util.utcnow())
    assert len(asked) == 3 and len(retries) == 2  # 21:00 plus two repeats, then it gives up
    assert len(created) == 1  # a failed retry doesn't post the report again


async def test_no_forecast_retry_when_forecast_works(hass: HomeAssistant) -> None:
    runner, _asked = await _forecast_retry_setup(hass, fail_times=0)
    with (
        patch.object(runner_mod.persistent_notification, "async_create"),
        patch.object(runner_mod, "async_call_later") as call_later,
    ):
        await runner._async_scheduled_report()
    call_later.assert_not_called()


async def test_forecast_retry_keeps_boiler_line(hass: HomeAssistant) -> None:
    """The replaced report still says the boiler settings changed."""
    from custom_components.cloudless_multiroom_thermostat.experimental import store

    runner, _asked = await _forecast_retry_setup(hass, fail_times=1, extra={"exp_max_ch": "sensor.max_ch"})
    hass.states.async_set("sensor.max_ch", "40")
    store.write_state(runner.f_state, {"boiler": {"max_ch": 47.5}})
    created, retries = [], []

    def call_later(_hass, delay, action):
        retries.append(action)
        return lambda: None

    with (
        patch.object(runner_mod.persistent_notification, "async_create", side_effect=lambda h, m, **kw: created.append(m)),
        patch.object(runner_mod, "async_call_later", side_effect=call_later),
    ):
        await runner._async_scheduled_report()
        await retries[0](dt_util.utcnow())
    assert len(created) == 2
    line = "Boiler settings changed since the last report: max flow 47.5 -> 40 degC"
    assert all(line in m for m in created)


async def test_manual_report_keeps_boiler_line_for_evening(hass: HomeAssistant) -> None:
    """A report on demand shows a boiler change but leaves it for the 21:00 report."""
    from custom_components.cloudless_multiroom_thermostat.experimental import store

    runner, _asked = await _forecast_retry_setup(hass, fail_times=0, extra={"exp_max_ch": "sensor.max_ch"})
    hass.states.async_set("sensor.max_ch", "40")
    store.write_state(runner.f_state, {"boiler": {"max_ch": 47.5}})
    created = []
    line = "Boiler settings changed since the last report: max flow 47.5 -> 40 degC"
    with patch.object(runner_mod.persistent_notification, "async_create", side_effect=lambda h, m, **kw: created.append(m)):
        await runner.async_report()
        assert store.read_state(runner.f_state)["boiler"] == {"max_ch": 47.5}
        await runner._async_scheduled_report()
        assert store.read_state(runner.f_state)["boiler"] == {"max_ch": 40.0}
        await runner.async_report()
    assert line in created[0] and line in created[1]
    assert line not in created[2]


def test_split_day_uses_dhw_rate() -> None:
    """In a heating hour the hot-water part is its hot-water minutes at the given rate."""
    from custom_components.cloudless_multiroom_thermostat.experimental import gas_core

    hours = [{"gas": 1.0, "ch": 60, "dhw": 10, "flame": 60}]
    default = gas_core.split_day(hours)
    assert default["dhw_gas"] == pytest.approx(0.19) and default["dhw_rate"] == 0.019
    custom = gas_core.split_day(hours, 0.025)
    assert custom["dhw_gas"] == pytest.approx(0.25) and custom["heating_gas"] == pytest.approx(0.75)
    assert custom["dhw_rate"] == 0.025
    # never more hot water than the hour burned
    assert gas_core.split_day(hours, 0.5)["dhw_gas"] == pytest.approx(1.0)


def _h(gas=0.0, ch=0, dhw=0, flame=None):
    return {"gas": gas, "ch": ch, "dhw": dhw, "flame": dhw if flame is None else flame}


def test_rate_sample_needs_quiet_hours_around_a_draw() -> None:
    from custom_components.cloudless_multiroom_thermostat.experimental import gas_core

    # shower, meter reports part of it an hour late: both hours count
    hours = [_h(), _h(0.15, dhw=8), _h(0.04), _h()]
    assert gas_core.rate_sample(hours) == (pytest.approx(0.19), pytest.approx(8))
    # right after heating: heating gas may leak in, not a sample
    assert gas_core.rate_sample([_h(0.5, ch=60, flame=60), _h(0.15, dhw=8), _h(), _h()]) == (0, 0)
    # right before heating: the late gas would land in a heating hour
    assert gas_core.rate_sample([_h(), _h(0.15, dhw=8), _h(0.5, ch=60, flame=60)]) == (0, 0)
    # washing hands: too short to count
    assert gas_core.rate_sample([_h(), _h(0.03, dhw=2), _h(), _h()]) == (0, 0)


def test_measured_rate_needs_enough_minutes() -> None:
    from custom_components.cloudless_multiroom_thermostat.experimental import gas_core

    days = {f"2026-10-{d:02d}": {"rate_gas": 0.21, "rate_min": 10} for d in range(1, 6)}
    assert gas_core.measured_rate(days) == (None, 50)
    days["2026-10-06"] = {"rate_gas": 0.21, "rate_min": 10}
    assert gas_core.measured_rate(days) == (pytest.approx(0.021), 60)
    # implausible (meter glitch) is ignored
    assert gas_core.measured_rate({"d": {"rate_gas": 9.0, "rate_min": 60}})[0] is None


async def test_dhw_rate_setting_over_measured_over_default(hass: HomeAssistant) -> None:
    entry = await _setup(hass, EXP)
    runner = entry.runtime_data.experimental
    assert runner._dhw_rate({})["source"] == "default"
    stored = {"2026-10-01": {"rate_gas": 1.5, "rate_min": 60}}
    assert runner._dhw_rate(stored) == {"rate": 0.025, "source": "measured", "minutes": 60}
    hass.config_entries.async_update_entry(entry, options={**entry.options, "exp_dhw_rate": 0.02})
    await hass.async_block_till_done()
    runner = entry.runtime_data.experimental
    assert runner._dhw_rate(stored)["source"] == "setting" and runner._dhw_rate(stored)["rate"] == 0.02


def _samples(area: str = "Living", minutes: int = 40) -> list[dict]:
    return [
        {
            "t": f"2026-11-01T07:{m:02d}:00+01:00",
            "target": 20.0,
            "room": 19.0 + m * 0.05,
            "area": area,
            "flame": True,
            "dhw": False,
            "water": 50.0,
            "outdoor": 5.0,
            "integral": None,
            "garden_paused": False,
        }
        for m in range(minutes)
    ]


def test_episode_room_matched_by_id() -> None:
    """other_room follows the room id of the "Room to follow" option, not a room name."""
    followed = tuner_core.analyse(_samples(), {}, "cooled_down", room_id="a", followed_room_id="a")
    assert followed["room_id"] == "a" and followed["room_class"] == "living"
    assert "other_room" not in followed["invalid_reasons"]

    other = tuner_core.analyse(_samples("Bedroom"), {}, "cooled_down", room_id="b", followed_room_id="a")
    assert other["room_id"] == "b" and other["room_class"] == "other"
    assert "other_room" in other["invalid_reasons"]

    # No leading room recorded: never counts
    unknown = tuner_core.analyse(_samples(), {}, "cooled_down", room_id=None, followed_room_id=None)
    assert "other_room" in unknown["invalid_reasons"]


def _warmup(day: int, room_id: str | None, area: str) -> dict:
    record = {
        "start": f"2026-10-{day:02d}T06:00:00+01:00",
        "area": area,
        "target": 21.0,
        "start_temp": 18.0,
        "rise_min": 60,
        "outdoor_band": "0_8",
        "invalid_reasons": [],
    }
    if room_id is not None:
        record["room_id"] = room_id
    return record


def test_warmup_rate_matches_room_id_and_legacy_name() -> None:
    episodes = [
        _warmup(1, None, "Living Room"),  # copied pyscript history: name only
        _warmup(2, None, "living room"),  # name match ignores case
        _warmup(3, "a", "Lounge"),  # same room, renamed since: id wins
        _warmup(4, "b", "Living Room"),  # another room that had the same name
        _warmup(5, None, "Bedroom"),
    ]
    rate, n, _ = night_core.warmup_rate(episodes, room_id="a", room_name="Living Room")
    assert n == 3 and rate == pytest.approx(0.05)


def test_episode_water_peak_skips_hot_water_and_stores_flow_limit() -> None:
    samples = _samples()
    for s in samples:
        s["max_ch"] = 40.0
    samples[5].update(dhw=True, water=64.0)  # a short hot-water draw
    e = tuner_core.analyse(samples, {}, "cooled_down", room_id="a", followed_room_id="a")
    assert e["valid"]
    assert e["water_peak"] == 50.0 and e["max_ch"] == 40.0
    assert tuner_core.summary_line(e).endswith(", flow limit 40")

    samples[20]["max_ch"] = 47.5  # limit changed on the boiler during the warm-up
    e = tuner_core.analyse(samples, {}, "cooled_down", room_id="a", followed_room_id="a")
    assert "max_ch_changed" in e["invalid_reasons"] and e["max_ch"] == 40.0

    # Without the field nothing changes
    e = tuner_core.analyse(_samples(), {}, "cooled_down", room_id="a", followed_room_id="a")
    assert e["max_ch"] is None and e["valid"]
    assert "flow limit" not in tuner_core.summary_line(e)


def _judged(max_ch: float | None) -> dict:
    return {
        "valid": True, "room_class": "living", "outdoor_band": "0_8", "overshoot": 0.8,
        "flame_starts_per_hour": 1.0, "undershoot": 0.0, "rise_min": 30, "max_ch": max_ch,
    }


def test_recommend_keeps_flow_limits_apart() -> None:
    params = {"kp": 0.7, "ki": 0.003, "kd": 0.0}
    assert tuner_core.recommend([_judged(47.5), _judged(40.0)], params) is None
    rec = tuner_core.recommend([_judged(47.5), _judged(40.0), _judged(40.0)], params)
    assert rec["verdict"] == "overshoot" and rec["param"] == "ki"
    # Older records without the field still group with each other
    assert tuner_core.recommend([_judged(None), _judged(None)], params)["param"] == "ki"


def test_boiler_change_line() -> None:
    now = {"max_ch": 40.0, "dhw_setpoint": 50.0}
    assert tuner_core.boiler_change_line(None, now) is None  # first report: nothing to compare
    assert tuner_core.boiler_change_line(now, now) is None
    assert tuner_core.boiler_change_line({"max_ch": 47.5, "dhw_setpoint": 65.0}, now) == (
        "Boiler settings changed since the last report: max flow 47.5 -> 40 degC, hot water 65 -> 50 degC"
    )
    assert tuner_core.boiler_change_line({"max_ch": 47.5, "dhw_setpoint": 50.0}, now) == (
        "Boiler settings changed since the last report: max flow 47.5 -> 40 degC"
    )
    # Unavailable right now is not a change
    assert tuner_core.boiler_change_line(now, {"max_ch": None, "dhw_setpoint": 50.0}) is None


BOILER = {"exp_max_ch": "sensor.max_ch", "exp_dhw_setpoint": "sensor.dhw_set"}


async def test_gas_split_stores_boiler_settings(hass: HomeAssistant) -> None:
    """Each split day keeps the boiler settings in effect at its end."""
    entry = await _setup(hass, {**EXP, **BOILER})
    runner = entry.runtime_data.experimental
    now = dt_util.now()
    day0 = dt_util.start_of_local_day(now) - timedelta(days=1)
    stats = {
        "sensor.gas": [
            {"start": (day0 + timedelta(hours=h)).timestamp(), "change": 0.2 if 6 <= h < 9 else 0.0}
            for h in range(24)
        ]
    }

    class St:
        def __init__(self, when, state):
            self.last_changed = when
            self.state = state

    history = {
        "binary_sensor.ch_active": [St(day0, "off"), St(day0 + timedelta(hours=6), "on"), St(day0 + timedelta(hours=9), "off")],
        "binary_sensor.dhw": [St(day0, "off")],
        "binary_sensor.flame": [St(day0, "off"), St(day0 + timedelta(hours=6), "on"), St(day0 + timedelta(hours=9), "off")],
        "zone.home": [St(day0, "2")],
        # changed on the boiler at 13:00; the setpoint sensor dropped out in the evening
        "sensor.max_ch": [St(day0, "47.5"), St(day0 + timedelta(hours=13), "40")],
        "sensor.dhw_set": [St(day0, "65"), St(day0 + timedelta(hours=20), "unavailable")],
    }
    asked = []

    async def fake_stats(ids, start, end, period, types):
        return stats

    async def fake_history(ids, start, end):
        asked.extend(ids)
        return history

    with patch.object(runner, "_async_statistics", side_effect=fake_stats), patch.object(
        runner, "_async_history", side_effect=fake_history
    ):
        await runner._async_store_gas_splits(now)

    assert "sensor.max_ch" in asked and "sensor.dhw_set" in asked
    day = (await runner._async_read_gas_days())[day0.date().isoformat()]
    assert day["max_ch"] == 40.0 and day["dhw_setpoint"] == 65.0


async def test_gas_split_without_boiler_settings(hass: HomeAssistant) -> None:
    """Not configured: no boiler fields in the record (the gas split test covers the rest)."""
    entry = await _setup(hass, EXP)
    runner = entry.runtime_data.experimental
    now = dt_util.now()
    day0 = dt_util.start_of_local_day(now) - timedelta(days=1)

    class St:
        def __init__(self, when, state):
            self.last_changed = when
            self.state = state

    async def fake_stats(ids, start, end, period, types):
        return {"sensor.gas": [{"start": (day0 + timedelta(hours=h)).timestamp(), "change": 0.0} for h in range(24)]}

    async def fake_history(ids, start, end):
        return {eid: [St(day0, "off")] for eid in ids}

    with patch.object(runner, "_async_statistics", side_effect=fake_stats), patch.object(
        runner, "_async_history", side_effect=fake_history
    ):
        await runner._async_store_gas_splits(now)
    day = (await runner._async_read_gas_days())[day0.date().isoformat()]
    assert "max_ch" not in day and "dhw_setpoint" not in day


async def test_report_shows_boiler_setting_change(hass: HomeAssistant) -> None:
    entry = await _setup(hass, {**EXP, **BOILER, "exp_gas": None})
    runner = entry.runtime_data.experimental
    hass.states.async_set("sensor.max_ch", "47.5")
    hass.states.async_set("sensor.dhw_set", "65")
    created = []

    async def report():
        # The 21:00 report: only that one stores the boiler settings
        await runner._async_scheduled_report()

    with patch.object(
        runner_mod.persistent_notification, "async_create", side_effect=lambda h, m, **kw: created.append(m)
    ):
        await report()  # first report: only remembers the values
        hass.states.async_set("sensor.max_ch", "40")
        hass.states.async_set("sensor.dhw_set", "unavailable")
        await report()
        hass.states.async_set("sensor.dhw_set", "50")
        await report()
        await report()  # nothing changed

    assert "Boiler settings changed" not in created[0]
    assert "Boiler settings changed since the last report: max flow 47.5 -> 40 degC" in created[1]
    assert "hot water 65" not in created[1]  # unavailable is not a change
    assert "Boiler settings changed since the last report: hot water 65 -> 50 degC" in created[2]
    assert "Boiler settings changed" not in created[3]


def test_recommend_never_moves_against_the_verdict() -> None:
    overshoot = [_judged(None), _judged(None)]
    # Ki below the known range: clamping would have raised it on overshoot
    rec = tuner_core.recommend(overshoot, {"kp": 0.7, "ki": 0.0002, "kd": 0.0})
    assert rec["outside_range"] and rec["new"] == rec["old"] == 0.0002
    slow = [{**e, "overshoot": 0.1, "rise_min": 90} for e in overshoot]
    # Kp on another scale: clamping would have cut it to 1.5 on a slow warm-up
    rec = tuner_core.recommend(slow, {"kp": 12.0, "ki": 0.003, "kd": 0.0})
    assert rec["param"] == "kp" and rec["outside_range"] and rec["new"] == 12.0
    # Inside the range the 10% step and the limits are unchanged
    rec = tuner_core.recommend(overshoot, {"kp": 0.7, "ki": 0.003, "kd": 0.0})
    assert rec["new"] == 0.0027 and not rec["at_limit"] and "outside_range" not in rec
    rec = tuner_core.recommend(overshoot, {"kp": 0.7, "ki": 0.0005, "kd": 0.0})
    assert rec["at_limit"] and rec["new"] == 0.0005
async def test_stored_values_shown_after_reload(hass: HomeAssistant) -> None:
    from custom_components.cloudless_multiroom_thermostat.experimental import store

    entry = await _setup(hass, EXP)
    runner = entry.runtime_data.experimental
    store.append_episode(runner.f_episodes, {"start": "2026-11-04T06:30:00+01:00", "area": "Living", "overshoot": 0.3})
    store.write_state(
        runner.f_state,
        {"recommendation": {"param": "ki", "old": 0.003, "new": 0.0027, "why": "overshoot", "at_limit": False}},
    )
    store.write_state(
        runner.f_night_state,
        {"prediction": {
            "suggested_start": "2026-11-05T06:41:00+01:00", "comfort_at": "2026-11-05T07:30:00+01:00",
            "room_at_comfort": 17.1, "warmup_min": 49, "tau_h": 40.0, "evening_drop": -0.8, "outdoor_min": 3.0,
            "nights_used": 7, "warmups_used": 4, "gas_before6": None, "gas_after6": None,
        }},
    )
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.heating_last_warm_up_overshoot").state == "0.3"
    assert hass.states.get("sensor.heating_pid_advice").state == "ki 0.003 -> 0.0027 (overshoot)"
    night = hass.states.get("sensor.heating_night_prediction")
    assert night.state == "06:41" and night.attributes["warmup_min"] == 49
async def test_unreadable_learning_file_does_not_block_heating(hass: HomeAssistant) -> None:
    entry = await _setup(hass, {})
    folder = os.path.join(hass.config.config_dir, DOMAIN, entry.entry_id)
    os.makedirs(folder)
    path = os.path.join(folder, "episode_state.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not json")
    hass.config_entries.async_update_entry(entry, options=EXP)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.experimental is not None
    assert not os.path.exists(path)
    assert any(name.startswith("episode_state.json.corrupt-") for name in os.listdir(folder))
    assert hass.states.get("climate.living") is not None


def test_read_state_sets_a_non_object_aside(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text("[1, 2]", encoding="utf-8")
    assert store.read_state(str(path)) == {}
    assert not path.exists()
    assert len(list(tmp_path.glob("state.json.corrupt-*"))) == 1


async def test_learning_start_failure_keeps_heating(hass: HomeAssistant) -> None:
    with patch.object(runner_mod.ExperimentalRunner, "_async_resume_episode", side_effect=RuntimeError("boom")):
        entry = await _setup(hass, EXP)
    assert entry.state is ConfigEntryState.LOADED
    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": "climate.living", "hvac_mode": "heat"}, blocking=True
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.heating_winning_room").state == "Living"
