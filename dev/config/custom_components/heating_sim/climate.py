"""Fake TRVs and a fake OpenTherm boiler thermostat."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature

from . import DOMAIN, HeatingSim, SimTrv
from .entity import SimEntity

FEATURES = (
    ClimateEntityFeature.TARGET_TEMPERATURE
    | ClimateEntityFeature.TURN_ON
    | ClimateEntityFeature.TURN_OFF
)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    if discovery_info is None:
        return
    sim: HeatingSim = hass.data[DOMAIN]
    entities: list[ClimateEntity] = [SimBoiler(sim)]
    entities += [SimTrvClimate(trv) for room in sim.rooms for trv in room.trvs]
    async_add_entities(entities)


class _SimClimate(SimEntity, ClimateEntity):
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = FEATURES
    _attr_target_temperature_step = 0.5
    _attr_min_temp = 5
    _attr_max_temp = 30


class SimTrvClimate(_SimClimate):
    """Behaves like a TRV: regulates its own valve against its last target."""

    def __init__(self, trv: SimTrv) -> None:
        self._trv = trv
        self._attr_name = f"Sim {trv.room.name} TRV {trv.index}"
        self._attr_unique_id = f"heating_sim_{trv.key}"

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self._trv.hvac_on else HVACMode.OFF

    @property
    def target_temperature(self) -> float:
        return self._trv.target

    @property
    def current_temperature(self) -> float:
        return round(self._trv.measured, 1)

    @property
    def hvac_action(self) -> HVACAction:
        if not self._trv.hvac_on:
            return HVACAction.OFF
        return HVACAction.HEATING if self._trv.valve_open else HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"valve_open": self._trv.valve_open}

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (mode := kwargs.get("hvac_mode")) is not None:
            self._trv.hvac_on = mode == HVACMode.HEAT
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            self._trv.target = min(max(float(temp), self.min_temp), self.max_temp)
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._trv.hvac_on = hvac_mode == HVACMode.HEAT
        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)


class SimBoiler(_SimClimate):
    """Stands in for the ESPHome / DIYLess / OTGW thermostat: receives the winner target."""

    _attr_name = "Sim boiler"
    _attr_unique_id = "heating_sim_boiler"

    def __init__(self, sim: HeatingSim) -> None:
        self._sim = sim

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self._sim.boiler_on else HVACMode.OFF

    @property
    def target_temperature(self) -> float:
        return self._sim.boiler_target

    @property
    def hvac_action(self) -> HVACAction:
        return HVACAction.HEATING if self._sim.firing else HVACAction.IDLE

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (mode := kwargs.get("hvac_mode")) is not None:
            self._sim.boiler_on = mode == HVACMode.HEAT
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            self._sim.boiler_target = min(max(float(temp), self.min_temp), self.max_temp)
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._sim.boiler_on = hvac_mode == HVACMode.HEAT
        self.async_write_ha_state()

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)
