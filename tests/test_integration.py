"""End to end tests on a real Home Assistant core with fake devices."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest

from homeassistant import config_entries
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.cloudless_multiroom_thermostat.const import DOMAIN

LIVING = {
    "name": "Living",
    "temperature_sensor": "sensor.living_temp",
    "trvs": ["climate.trv_living"],
    "external_temperature_inputs": ["number.trv_living_ext"],
    "window_sensors": ["binary_sensor.garden_door"],
    "window_open_delay": 120,
    "window_close_delay": 60,
    "eco_temperature": 15.0,
    "warm_temperature": 19.0,
    "overshoot_buffer": 1.0,
    "min_temp": 5.0,
    "max_temp": 25.0,
    "target_temp_step": 0.5,
}
OFFICE = {
    **LIVING,
    "name": "Office",
    "temperature_sensor": "sensor.office_temp",
    "trvs": ["climate.trv_office"],
    "external_temperature_inputs": [],
    "window_sensors": [],
    "warm_temperature": 18.0,
}
HALLWAY = {
    **OFFICE,
    "name": "Hallway",
    "temperature_sensor": "sensor.hallway_temp",
    "trvs": ["climate.trv_hallway"],
    "drives_heat_source": False,
}
SYSTEM = {
    "heat_source_type": "climate",
    "heat_source_entity": "climate.boiler",
    "ch_switch": "switch.ch_enabled",
    "off_temperature": 10.0,
    "presence_entity": "input_boolean.somebody_home",
    "away_mode": "off",
    "skip_unchanged_writes": True,
    "external_temp_heartbeat": 60,
}
TRV_ATTRS = {"temperature": 15, "min_temp": 5, "max_temp": 30, "target_temp_step": 0.5}


class Calls:
    """Records every write the controller makes instead of calling services."""

    def __init__(self) -> None:
        self.items: list[tuple[str, str, dict[str, Any]]] = []

    def last(self, entity_id: str) -> tuple[str, dict[str, Any]] | None:
        for domain_service in reversed(self.items):
            if domain_service[2]["entity_id"] == entity_id:
                return domain_service[1], domain_service[2]
        return None

    def clear(self) -> None:
        self.items.clear()


def _states(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.living_temp", "18.0", {"device_class": "temperature"})
    hass.states.async_set("sensor.office_temp", "17.0", {"device_class": "temperature"})
    hass.states.async_set("climate.trv_living", "heat", TRV_ATTRS)
    hass.states.async_set("climate.trv_office", "heat", TRV_ATTRS)
    hass.states.async_set("sensor.hallway_temp", "14.0", {"device_class": "temperature"})
    hass.states.async_set("climate.trv_hallway", "heat", TRV_ATTRS)
    hass.states.async_set("number.trv_living_ext", "16.0", {"min": -10, "max": 50, "step": 0.1})
    hass.states.async_set("binary_sensor.garden_door", "off")
    hass.states.async_set("climate.boiler", "heat", {"temperature": 10})
    hass.states.async_set("switch.ch_enabled", "off")
    hass.states.async_set("input_boolean.somebody_home", "on")


async def _tick(hass: HomeAssistant, seconds: float = 2) -> None:
    """Advance time, then once more so a recompute queued by a fired timer runs too."""
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds + 2))
    await hass.async_block_till_done()


@pytest.fixture
async def setup(hass: HomeAssistant):
    _states(hass)
    calls = Calls()

    async def _fake_call(self, domain, service, data):
        calls.items.append((domain, service, dict(data)))

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Heating",
        data=SYSTEM,
        subentries_data=[
            ConfigSubentryData(data=LIVING, subentry_type="room", title="Living", unique_id=None),
            ConfigSubentryData(data=OFFICE, subentry_type="room", title="Office", unique_id=None),
            ConfigSubentryData(data=HALLWAY, subentry_type="room", title="Hallway", unique_id=None),
        ],
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.cloudless_multiroom_thermostat.controller.HeatingController._call",
        _fake_call,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await _tick(hass)
        yield entry, calls


async def _heat(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": "heat"}, blocking=True
    )
    await _tick(hass)


async def test_entities_created(hass: HomeAssistant, setup) -> None:
    state = hass.states.get("climate.living")
    assert state is not None
    assert state.state == "off"
    assert state.attributes["preset_mode"] == "warm"
    assert state.attributes["temperature"] == 19.0
    assert hass.states.get("climate.office") is not None
    assert hass.states.get("binary_sensor.living_window") is not None
    assert hass.states.get("binary_sensor.office_window") is None
    assert hass.states.get("sensor.heating_winning_room").state == "Off"


async def test_startup_pushes_external_temperature(hass: HomeAssistant, setup) -> None:
    _, calls = setup
    assert calls.last("number.trv_living_ext") == ("set_value", {"entity_id": "number.trv_living_ext", "value": 18.0})


async def test_winner_gets_buffer_passenger_does_not(hass: HomeAssistant, setup) -> None:
    _, calls = setup
    calls.clear()
    await _heat(hass, "climate.living")  # 19 vs 18: delta 1
    await _heat(hass, "climate.office")  # 18 vs 17: delta 1, Living came first and ties lose
    assert hass.states.get("sensor.heating_winning_room").state == "Living"
    assert calls.last("climate.trv_living")[1]["temperature"] == 20.0
    assert calls.last("climate.trv_office")[1]["temperature"] == 18.0
    assert calls.last("climate.boiler")[1]["temperature"] == 19.0
    assert calls.last("switch.ch_enabled")[0] == "turn_on"
    assert hass.states.get("sensor.heating_winning_room_temperature").state == "18.0"
    assert hass.states.get("climate.living").attributes["hvac_action"] == "heating"
    assert hass.states.get("climate.office").attributes["role"] == "passenger"

    # Office gets colder: it takes over, buffer moves with it
    hass.states.async_set("climate.trv_living", "heat", {**TRV_ATTRS, "temperature": 20})
    hass.states.async_set("climate.trv_office", "heat", {**TRV_ATTRS, "temperature": 18})
    hass.states.async_set("sensor.office_temp", "15.0", {"device_class": "temperature"})
    await _tick(hass)
    assert hass.states.get("sensor.heating_winning_room").state == "Office"
    assert calls.last("climate.trv_office")[1]["temperature"] == 19.0
    assert calls.last("climate.trv_living")[1]["temperature"] == 19.0
    assert calls.last("climate.boiler")[1]["temperature"] == 18.0


async def test_room_that_does_not_drive_only_heats_along(hass: HomeAssistant, setup) -> None:
    _, calls = setup
    calls.clear()
    await _heat(hass, "climate.hallway")  # 18 vs 14: the biggest gap, but it may not drive
    assert hass.states.get("sensor.heating_winning_room").state == "Off"
    assert calls.last("climate.trv_hallway")[1]["temperature"] == 18.0
    assert calls.last("climate.boiler") is None
    assert calls.last("switch.ch_enabled") is None
    hallway = hass.states.get("climate.hallway")
    assert hallway.attributes["role"] == "waiting"
    assert hallway.attributes["hvac_action"] == "idle"

    # Living drives the boiler; the hallway heats along without the buffer
    hass.states.async_set("climate.trv_hallway", "heat", {**TRV_ATTRS, "temperature": 18})
    await _heat(hass, "climate.living")
    assert hass.states.get("sensor.heating_winning_room").state == "Living"
    assert calls.last("climate.boiler")[1]["temperature"] == 19.0
    assert calls.last("climate.trv_hallway")[1]["temperature"] == 18.0
    hallway = hass.states.get("climate.hallway")
    assert hallway.attributes["role"] == "passenger"
    assert hallway.attributes["hvac_action"] == "heating"


async def test_window_pauses_and_resumes(hass: HomeAssistant, setup) -> None:
    _, calls = setup
    await _heat(hass, "climate.living")
    hass.states.async_set("climate.boiler", "heat", {"temperature": 19})
    calls.clear()
    hass.states.async_set("binary_sensor.garden_door", "on")
    await _tick(hass, 30)
    assert hass.states.get("binary_sensor.living_paused").state == "off"  # still within delay
    await _tick(hass, 125)
    assert hass.states.get("binary_sensor.living_window").state == "on"
    paused = hass.states.get("binary_sensor.living_paused")
    assert paused.state == "on" and paused.attributes["reason"] == "window"
    assert calls.last("climate.trv_living")[1]["temperature"] == 10.0
    assert calls.last("climate.boiler")[1]["temperature"] == 10.0
    # The room keeps its own mode and target
    living = hass.states.get("climate.living")
    assert living.state == "heat" and living.attributes["temperature"] == 19.0

    hass.states.async_set("climate.trv_living", "heat", {**TRV_ATTRS, "temperature": 10})
    hass.states.async_set("climate.boiler", "heat", {"temperature": 10})
    hass.states.async_set("binary_sensor.garden_door", "off")
    await _tick(hass, 65)
    assert hass.states.get("binary_sensor.living_paused").state == "off"
    assert calls.last("climate.trv_living")[1]["temperature"] == 20.0
    assert calls.last("climate.boiler")[1]["temperature"] == 19.0


async def test_away_off_and_return(hass: HomeAssistant, setup) -> None:
    _, calls = setup
    await _heat(hass, "climate.living")
    hass.states.async_set("climate.boiler", "heat", {"temperature": 19})
    calls.clear()
    hass.states.async_set("input_boolean.somebody_home", "off")
    await _tick(hass)
    assert hass.states.get("binary_sensor.living_paused").attributes["reason"] == "away"
    assert calls.last("switch.ch_enabled") is None or calls.last("switch.ch_enabled")[0] == "turn_off"
    assert calls.last("climate.boiler")[1]["temperature"] == 10.0
    hass.states.async_set("input_boolean.somebody_home", "on")
    hass.states.async_set("climate.boiler", "heat", {"temperature": 10})
    await _tick(hass)
    assert hass.states.get("binary_sensor.living_paused").state == "off"
    assert calls.last("climate.boiler")[1]["temperature"] == 19.0


async def test_presets_and_manual_target(hass: HomeAssistant, setup) -> None:
    await hass.services.async_call(
        "climate", "set_preset_mode", {"entity_id": "climate.living", "preset_mode": "eco"}, blocking=True
    )
    state = hass.states.get("climate.living")
    assert state.attributes["preset_mode"] == "eco" and state.attributes["temperature"] == 15.0
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": "climate.living", "temperature": 20.5}, blocking=True
    )
    state = hass.states.get("climate.living")
    assert state.attributes["preset_mode"] == "none" and state.attributes["temperature"] == 20.5


async def test_pause_resume_and_all_off_services(hass: HomeAssistant, setup) -> None:
    await _heat(hass, "climate.living")
    await hass.services.async_call(DOMAIN, "pause_room", {"entity_id": "climate.living"}, blocking=True)
    await _tick(hass)
    assert hass.states.get("binary_sensor.living_paused").attributes["reason"] == "manual"
    await hass.services.async_call(DOMAIN, "resume_room", {"entity_id": "climate.living"}, blocking=True)
    await _tick(hass)
    assert hass.states.get("binary_sensor.living_paused").state == "off"
    await hass.services.async_call(DOMAIN, "set_all_off", {}, blocking=True)
    await _tick(hass)
    assert hass.states.get("climate.living").state == "off"


async def test_config_flow_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "House", "heat_source_type": "switch"}
    )
    assert result["step_id"] == "heat_source"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"heat_source_entity": "switch.boiler", "hysteresis": 0.3}
    )
    assert result["step_id"] == "settings"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"off_temperature": 10, "away_mode": "eco", "skip_unchanged_writes": True, "external_temp_heartbeat": 60},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "House"
    assert result["data"]["heat_source_type"] == "switch"


async def test_switch_heat_source_hysteresis(hass: HomeAssistant) -> None:
    _states(hass)
    hass.states.async_set("switch.boiler", "off")
    calls = Calls()

    async def _fake_call(self, domain, service, data):
        calls.items.append((domain, service, dict(data)))

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Relay",
        data={**SYSTEM, "heat_source_type": "switch", "heat_source_entity": "switch.boiler", "hysteresis": 0.3, "ch_switch": None},
        subentries_data=[ConfigSubentryData(data=LIVING, subentry_type="room", title="Living", unique_id=None)],
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.cloudless_multiroom_thermostat.controller.HeatingController._call",
        _fake_call,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await _tick(hass)
        await _heat(hass, "climate.living")  # 19 vs 18
        assert calls.last("switch.boiler")[0] == "turn_on"
        hass.states.async_set("switch.boiler", "on")
        hass.states.async_set("sensor.living_temp", "19.0", {"device_class": "temperature"})
        await _tick(hass)
        assert calls.last("switch.boiler")[0] == "turn_off"


async def test_options_flow_clears_optional_entities(hass: HomeAssistant, setup) -> None:
    entry, _ = setup
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "heat_source_entity": "climate.boiler",
            "off_temperature": 8,
            "away_mode": "eco",
            "skip_unchanged_writes": True,
            "external_temp_heartbeat": 30,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["ch_switch"] is None and entry.options["presence_entity"] is None
    assert entry.runtime_data.off_temp == 8


async def test_state_restored_after_reload(hass: HomeAssistant, setup) -> None:
    entry, _ = setup
    await _heat(hass, "climate.living")
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": "climate.living", "temperature": 21}, blocking=True
    )
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get("climate.living")
    assert state.state == "heat" and state.attributes["temperature"] == 21


async def test_fresh_external_input_without_value_gets_written(hass: HomeAssistant) -> None:
    """A new TRV input reports unknown until its first write; it must still get one."""
    _states(hass)
    hass.states.async_set("number.trv_living_ext", "unknown", {"min": -10, "max": 50, "step": 0.1})
    calls = Calls()

    async def _fake_call(self, domain, service, data):
        calls.items.append((domain, service, dict(data)))

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Heating",
        data=SYSTEM,
        subentries_data=[ConfigSubentryData(data=LIVING, subentry_type="room", title="Living", unique_id=None)],
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.cloudless_multiroom_thermostat.controller.HeatingController._call",
        _fake_call,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await _tick(hass)
        assert calls.last("number.trv_living_ext") == ("set_value", {"entity_id": "number.trv_living_ext", "value": 18.0})


ROOM_STEP = {k: v for k, v in OFFICE.items() if k in (
    "name", "eco_temperature", "warm_temperature", "overshoot_buffer", "min_temp", "max_temp")}
SENSOR_STEP = {k: v for k, v in OFFICE.items() if k in (
    "temperature_sensor", "trvs", "external_temperature_inputs", "window_sensors",
    "window_open_delay", "window_close_delay")}


def _room_step(name: str, **extra) -> dict:
    return {**ROOM_STEP, "name": name, "target_temp_step": "0.5", **extra}


async def test_add_room_two_steps(hass: HomeAssistant, setup) -> None:
    entry, _ = setup
    flows = hass.config_entries.subentries
    result = await flows.async_init((entry.entry_id, "room"), context={"source": config_entries.SOURCE_USER})
    assert result["step_id"] == "user"
    fields = {str(k) for k in result["data_schema"].schema}
    assert "temperature_sensor" not in fields and not {"area", "create_area"} & fields

    result = await flows.async_configure(result["flow_id"], _room_step("office"))
    assert result["errors"] == {"name": "name_exists"}
    result = await flows.async_configure(result["flow_id"], _room_step("Attic", min_temp=20.0, max_temp=18.0))
    assert result["errors"]["max_temp"] == "max_below_min"
    result = await flows.async_configure(result["flow_id"], _room_step("Attic"))
    assert result["step_id"] == "sensors"

    result = await flows.async_configure(result["flow_id"], {**SENSOR_STEP, "trvs": []})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    sub = next(s for s in entry.subentries.values() if s.title == "Attic")
    assert sub.data["target_temp_step"] == 0.5 and "area" not in sub.data
    assert hass.states.get("climate.attic") is not None


async def test_edit_room_keeps_device_area_set_in_ha(hass: HomeAssistant, setup) -> None:
    """The user puts the room device in an area from HA; editing the room must not undo it."""
    from homeassistant.helpers import area_registry as ar, device_registry as dr

    entry, _ = setup
    office = next(s for s in entry.subentries.values() if s.title == "Office")
    study = ar.async_get(hass).async_create("Study")
    devices = dr.async_get(hass)
    device = devices.async_get_device_by_identifier((DOMAIN, office.subentry_id), entry.entry_id)
    devices.async_update_device(device.id, area_id=study.id)

    flows = hass.config_entries.subentries
    result = await entry.start_subentry_reconfigure_flow(hass, office.subentry_id)
    assert result["step_id"] == "reconfigure"
    result = await flows.async_configure(result["flow_id"], _room_step("Office", warm_temperature=21.0))
    assert result["step_id"] == "sensors"
    result = await flows.async_configure(result["flow_id"], SENSOR_STEP)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.subentries[office.subentry_id].data["warm_temperature"] == 21.0
    assert devices.async_get_device_by_identifier((DOMAIN, office.subentry_id), entry.entry_id).area_id == study.id


async def test_rename_room_climate_keeps_entity(hass: HomeAssistant, setup) -> None:
    """Renaming a room climate's entity ID re-adds it under the new ID (the room device links to the system device by id)."""
    from homeassistant.helpers import device_registry as dr, entity_registry as er

    entry, _ = setup
    living = next(s for s in entry.subentries.values() if s.title == "Living")
    devices = dr.async_get(hass)
    system = devices.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    room = devices.async_get_device_by_identifier((DOMAIN, living.subentry_id), entry.entry_id)
    assert room.via_device_id == system.id

    er.async_get(hass).async_update_entity("climate.living", new_entity_id="climate.virtual_thermostat_living")
    await hass.async_block_till_done()
    assert hass.states.get("climate.living") is None
    assert hass.states.get("climate.virtual_thermostat_living") is not None


async def test_winner_area_comes_from_device_area(hass: HomeAssistant, setup) -> None:
    from homeassistant.helpers import area_registry as ar, device_registry as dr

    entry, _ = setup
    living = next(s for s in entry.subentries.values() if s.title == "Living")
    lounge = ar.async_get(hass).async_create("Lounge")
    devices = dr.async_get(hass)
    device = devices.async_get_device_by_identifier((DOMAIN, living.subentry_id), entry.entry_id)
    devices.async_update_device(device.id, area_id=lounge.id)
    await _heat(hass, "climate.living")
    assert hass.states.get("sensor.heating_winning_room").attributes["area"] == "Lounge"
