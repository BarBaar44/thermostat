"""Boiler signals as OpenTherm reports them: flame, central heating active, hot water active."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity

from . import DOMAIN, HeatingSim
from .entity import SimEntity


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    if discovery_info is None:
        return
    sim: HeatingSim = hass.data[DOMAIN]
    async_add_entities(
        [
            SimBoilerSignal(sim, "flame", "Sim boiler flame", lambda s: s.flame),
            SimBoilerSignal(sim, "ch_active", "Sim central heating active", lambda s: s.firing),
            SimBoilerSignal(sim, "hot_water", "Sim hot water active", lambda s: s.hot_water),
        ]
    )


class SimBoilerSignal(SimEntity, BinarySensorEntity):
    def __init__(self, sim: HeatingSim, key: str, name: str, value) -> None:
        self._sim = sim
        self._value = value
        self._attr_name = name
        self._attr_unique_id = f"heating_sim_{key}"

    @property
    def is_on(self) -> bool:
        return self._value(self._sim)
