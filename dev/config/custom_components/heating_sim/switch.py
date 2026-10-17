"""Boiler relay (switch heat source) and central heating enable switch."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity

from . import DOMAIN, HeatingSim
from .entity import SimEntity


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    if discovery_info is None:
        return
    sim: HeatingSim = hass.data[DOMAIN]
    async_add_entities([SimSwitch(sim, "relay_on", "Sim boiler relay"), SimSwitch(sim, "ch_enabled", "Sim CH enabled")])


class SimSwitch(SimEntity, SwitchEntity):
    def __init__(self, sim: HeatingSim, attr: str, name: str) -> None:
        self._sim = sim
        self._key = attr
        self._attr_name = name
        self._attr_unique_id = f"heating_sim_{attr}"

    @property
    def is_on(self) -> bool:
        return getattr(self._sim, self._key)

    async def async_turn_on(self, **kwargs: Any) -> None:
        setattr(self._sim, self._key, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        setattr(self._sim, self._key, False)
        self.async_write_ha_state()
