"""heating_sim: a fake house for testing Cloudless Multiroom Thermostat.

Creates per room a temperature sensor, N fake TRVs (climate) with an
external temperature input (number), plus a fake boiler (climate), a boiler
relay and a central heating enable switch. Room temperatures follow a simple
thermal model, sped up so an hour passes in minutes.

Test only. Not for a real installation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import logging
import time
from typing import Any

import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import slugify

_LOGGER = logging.getLogger(__name__)

DOMAIN = "heating_sim"
SIGNAL = f"{DOMAIN}_tick"
PLATFORMS = [
    Platform.SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
    Platform.WEATHER,
]
# Gas use in m³ per real hour (not sim time: recorder statistics run on real time)
GAS_CH_PER_HOUR = 1.1
GAS_DHW_PER_HOUR = 1.15  # 0.019 m³ per burner minute, the rate the gas split assumes

ROOM_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Optional("start_temperature", default=17.0): vol.Coerce(float),
        vol.Optional("trvs", default=1): vol.All(int, vol.Range(min=0, max=4)),
        # Degrees per sim hour with every valve fully open and the boiler firing
        vol.Optional("heat_rate", default=2.0): vol.Coerce(float),
        # Fraction of the indoor/outdoor gap lost per sim hour
        vol.Optional("loss_rate", default=0.06): vol.Coerce(float),
        vol.Optional("window", default=None): vol.Any(None, cv.entity_id),
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional("outdoor_temperature", default=8.0): vol.Coerce(float),
                # Sim seconds per real second (60 = one hour per minute)
                vol.Optional("speed", default=30.0): vol.All(vol.Coerce(float), vol.Range(min=1, max=600)),
                vol.Optional("tick_seconds", default=5): vol.All(int, vol.Range(min=1, max=60)),
                # Boiler climate counts as firing when its target is above this
                vol.Optional("boiler_on_above", default=12.0): vol.Coerce(float),
                # Extra loss per sim hour while a window is open
                vol.Optional("window_loss_rate", default=0.6): vol.Coerce(float),
                # Toggle this to simulate a shower: the burner runs for hot water
                vol.Optional("hot_water", default=None): vol.Any(None, cv.entity_id),
                vol.Required("rooms"): vol.All(cv.ensure_list, [ROOM_SCHEMA]),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


@dataclass
class SimTrv:
    room: SimRoom
    index: int
    hvac_on: bool = True
    target: float = 15.0
    external: float | None = None
    external_at: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.room.key}_trv_{self.index}"

    @property
    def measured(self) -> float:
        """What the TRV thinks the room is: external input if recent, else its own warm sensor."""
        if self.external is not None and time.monotonic() - self.external_at < 2 * 3600:
            return self.external
        return self.room.temperature + 1.5  # sits on the radiator, reads warm

    @property
    def valve_open(self) -> bool:
        return self.hvac_on and self.target > self.measured


@dataclass
class SimRoom:
    name: str
    temperature: float
    heat_rate: float
    loss_rate: float
    window: str | None
    trvs: list[SimTrv] = field(default_factory=list)

    @property
    def key(self) -> str:
        return slugify(self.name)

    def opening(self) -> float:
        """Share of this room's radiator capacity that is open (rooms without TRVs heat freely)."""
        if not self.trvs:
            return 1.0
        return sum(1 for t in self.trvs if t.valve_open) / len(self.trvs)


class HeatingSim:
    def __init__(self, hass: HomeAssistant, conf: dict[str, Any]) -> None:
        self.hass = hass
        self.conf = conf
        self.outdoor = conf["outdoor_temperature"]
        self.boiler_on = True
        self.boiler_target = 10.0
        self.relay_on = False
        self.ch_enabled = True
        self.gas_m3 = 1000.0
        self.rooms: list[SimRoom] = []
        for rc in conf["rooms"]:
            room = SimRoom(rc["name"], rc["start_temperature"], rc["heat_rate"], rc["loss_rate"], rc["window"])
            room.trvs = [SimTrv(room, i + 1) for i in range(rc["trvs"])]
            self.rooms.append(room)
        self._last = time.monotonic()

    @property
    def firing(self) -> bool:
        if self.relay_on:
            return True
        return self.ch_enabled and self.boiler_on and self.boiler_target > self.conf["boiler_on_above"]

    @property
    def hot_water(self) -> bool:
        entity_id = self.conf.get("hot_water")
        state = self.hass.states.get(entity_id) if entity_id else None
        return state is not None and state.state == "on"

    @property
    def flame(self) -> bool:
        return self.firing or self.hot_water

    def window_open(self, room: SimRoom) -> bool:
        if not room.window:
            return False
        state = self.hass.states.get(room.window)
        return state is not None and state.state == "on"

    @callback
    def tick(self, _now: Any = None) -> None:
        now = time.monotonic()
        real_hours = (now - self._last) / 3600
        sim_hours = real_hours * self.conf["speed"]
        self._last = now
        firing = self.firing
        if self.hot_water:  # hot water has priority on a combi boiler
            self.gas_m3 += GAS_DHW_PER_HOUR * real_hours
            firing = False
        elif firing:
            self.gas_m3 += GAS_CH_PER_HOUR * real_hours
        for room in self.rooms:
            gap = room.temperature - self.outdoor
            loss = room.loss_rate + (self.conf["window_loss_rate"] if self.window_open(room) else 0)
            gain = room.heat_rate * room.opening() if firing else 0.0
            room.temperature += (gain - loss * gap) * sim_hours
            room.temperature = round(room.temperature, 3)
        async_dispatcher_send(self.hass, SIGNAL)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    conf = config[DOMAIN]
    sim = HeatingSim(hass, conf)
    hass.data[DOMAIN] = sim
    for platform in PLATFORMS:
        hass.async_create_task(async_load_platform(hass, platform, DOMAIN, {}, config))
    async_track_time_interval(hass, sim.tick, timedelta(seconds=conf["tick_seconds"]))
    _LOGGER.warning(
        "heating_sim: %d fake rooms running at %sx speed. Test only.", len(sim.rooms), conf["speed"]
    )
    return True
