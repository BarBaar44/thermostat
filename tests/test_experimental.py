"""Experimental features: options, warm-up episodes, nights, gas split, report."""

from __future__ import annotations

from datetime import timedelta
import json
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.cloudless_multiroom_thermostat.const import DOCS_EXPERIMENTAL_URL, DOMAIN
from custom_components.cloudless_multiroom_thermostat.experimental import runner as runner_mod

from .test_integration import LIVING, OFFICE, SYSTEM, _states

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


async def _calls_noop(self, domain, service, data):
    return None


async def _setup(hass: HomeAssistant, options: dict) -> MockConfigEntry:
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
        hass.states.async_set("sensor.living_temp", f"{temp:.2f}", {"device_class": "temperature"})
        hass.states.async_set("binary_sensor.flame", "on" if minute % 20 < 15 else "off")
        await hass.async_block_till_done()
        await runner._async_tick()
    assert runner._episode is None, "episode should finish 90 minutes after reaching target"

    records = [json.loads(line) for line in open(runner.f_episodes, encoding="utf-8")]
    assert len(records) == 1
    record = records[0]
    assert record["area"] == "Living" and record["room_class"] == "living"
    assert record["rise_min"] is not None and record["flame_starts"] >= 1
    assert "other_room" not in record["invalid_reasons"]
    await hass.async_block_till_done()
    state = hass.states.get("sensor.heating_last_warm_up_overshoot")
    assert state.attributes["room"] == "Living" and state.attributes["experimental"] is True
    assert hass.states.get("sensor.heating_pid_advice").state != "unknown"


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
