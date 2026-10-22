"""TRV external temperature inputs (like Sonoff TRVZB / Shelly BLU TRV)."""

from __future__ import annotations

import time

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature

from . import DOMAIN, HeatingSim, SimTrv
from .entity import SimEntity


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    if discovery_info is None:
        return
    sim: HeatingSim = hass.data[DOMAIN]
    async_add_entities([ExternalInput(trv) for room in sim.rooms for trv in room.trvs])


class ExternalInput(SimEntity, NumberEntity):
    _attr_native_min_value = -10
    _attr_native_max_value = 50
    _attr_native_step = 0.1
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, trv: SimTrv) -> None:
        self._trv = trv
        self._attr_name = f"Sim {trv.room.name} TRV {trv.index} external temperature"
        self._attr_unique_id = f"heating_sim_{trv.key}_external"

    @property
    def native_value(self) -> float | None:
        return self._trv.external

    async def async_set_native_value(self, value: float) -> None:
        self._trv.external = value
        self._trv.external_at = time.monotonic()
        self.async_write_ha_state()
