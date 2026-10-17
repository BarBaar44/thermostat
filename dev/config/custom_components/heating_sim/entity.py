"""Base entity: refresh on every simulation tick."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from . import SIGNAL


class SimEntity(Entity):
    _attr_should_poll = False

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL, self._tick))

    @callback
    def _tick(self) -> None:
        self.async_write_ha_state()
