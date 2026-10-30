"""The heating engine: one controller per config entry (heating system)."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.components.climate import (
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_TARGET_TEMP_STEP,
    SERVICE_SET_TEMPERATURE,
)
from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_HOME,
    STATE_NOT_HOME,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started

from .const import (
    AWAY_ECO,
    AWAY_OFF,
    CONF_AWAY_MODE,
    CONF_BUFFER,
    CONF_CH_SWITCH,
    CONF_DRIVES_SOURCE,
    CONF_ECO_TEMP,
    CONF_EXT_TEMP,
    CONF_HEARTBEAT_MIN,
    CONF_HEAT_SOURCE_ENTITY,
    CONF_HEAT_SOURCE_TYPE,
    CONF_HYSTERESIS,
    CONF_OFF_TEMP,
    CONF_PRESENCE,
    CONF_ROOM_NAME,
    CONF_SKIP_UNCHANGED,
    CONF_TEMP_SENSOR,
    CONF_TRVS,
    CONF_WARM_TEMP,
    CONF_WINDOW_CLOSE_DELAY,
    CONF_WINDOW_OPEN_DELAY,
    CONF_WINDOWS,
    DEFAULT_AWAY_MODE,
    DEFAULT_BUFFER,
    DEFAULT_DRIVES_SOURCE,
    DEFAULT_ECO_TEMP,
    DEFAULT_HEARTBEAT_MIN,
    DEFAULT_HYSTERESIS,
    DEFAULT_OFF_TEMP,
    DEFAULT_SKIP_UNCHANGED,
    DEFAULT_WARM_TEMP,
    DEFAULT_WINDOW_CLOSE_DELAY,
    DEFAULT_WINDOW_OPEN_DELAY,
    DOMAIN,
    HEAT_SOURCE_SWITCH,
    PAUSE_AWAY,
    PAUSE_MANUAL,
    PAUSE_WINDOW,
    PRESET_ECO,
    PRESET_WARM,
    RECOMPUTE_COOLDOWN_S,
    SUBENTRY_ROOM,
    WINNER_OFF,
    WRITE_GRACE_S,
    signal_update,
)
from .logic import (
    RoomDemand,
    Winner,
    round_to_step,
    select_winner,
    switch_should_run,
    trv_target,
)

_LOGGER = logging.getLogger(__name__)

_BAD_STATES = (None, STATE_UNAVAILABLE, STATE_UNKNOWN, "")


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class Room:
    """Runtime state of one room. The climate entity restores and owns the base state."""

    room_id: str
    config: dict[str, Any]
    hvac_on: bool = False
    target: float = DEFAULT_WARM_TEMP
    preset: str | None = None
    manual_pause: bool = False
    window_open: bool = False
    current: float | None = None
    _window_timer: CALLBACK_TYPE | None = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return self.config[CONF_ROOM_NAME]

    @property
    def eco_temp(self) -> float:
        return float(self.config.get(CONF_ECO_TEMP, DEFAULT_ECO_TEMP))

    @property
    def warm_temp(self) -> float:
        return float(self.config.get(CONF_WARM_TEMP, DEFAULT_WARM_TEMP))

    @property
    def buffer(self) -> float:
        return float(self.config.get(CONF_BUFFER, DEFAULT_BUFFER))

    @property
    def drives(self) -> bool:
        """False for a room that heats along but must never run the heat source."""
        return bool(self.config.get(CONF_DRIVES_SOURCE, DEFAULT_DRIVES_SOURCE))


class HeatingController:
    """Owns rooms, listens to sensors and drives TRVs and the heat source."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.rooms: dict[str, Room] = {}
        self.winner = Winner(None, WINNER_OFF, self.off_temp, None, 0.0)
        self.present = True
        self.experimental: Any = None  # ExperimentalRunner when enabled in options
        self.system_device_id: str | None = None  # set in async_setup_entry
        self._unsubs: list[CALLBACK_TYPE] = []
        self._sent: dict[str, tuple[Any, float]] = {}
        self._started = False
        self._debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=RECOMPUTE_COOLDOWN_S,
            immediate=False,
            function=self._async_apply,
        )
        for sub_id, sub in entry.subentries.items():
            if sub.subentry_type == SUBENTRY_ROOM:
                self.rooms[sub_id] = Room(sub_id, dict(sub.data))

    # ---------------------------------------------------------------- config
    @property
    def settings(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options}

    @property
    def off_temp(self) -> float:
        return float(self.settings.get(CONF_OFF_TEMP, DEFAULT_OFF_TEMP))

    @property
    def away_mode(self) -> str:
        return self.settings.get(CONF_AWAY_MODE, DEFAULT_AWAY_MODE)

    # ---------------------------------------------------------- room effect
    def pause_reason(self, room: Room) -> str | None:
        """Why a room that the user set to heat is held off, if it is."""
        if not room.hvac_on:
            return None
        if room.window_open:
            return PAUSE_WINDOW
        if room.manual_pause:
            return PAUSE_MANUAL
        if not self.present and self.away_mode == AWAY_OFF:
            return PAUSE_AWAY
        return None

    def is_heating(self, room: Room) -> bool:
        return room.hvac_on and self.pause_reason(room) is None

    def effective_target(self, room: Room) -> float:
        if not self.is_heating(room):
            return self.off_temp
        if not self.present and self.away_mode == AWAY_ECO:
            return min(room.target, room.eco_temp)
        return room.target

    def area_label(self, room: Room) -> str:
        """Area the user put the room's device in, else the room name (legacy sensor, card)."""
        device = dr.async_get(self.hass).async_get_device_by_identifier(
            (DOMAIN, room.room_id), self.entry.entry_id
        )
        if device is not None and device.area_id:
            area = ar.async_get(self.hass).async_get_area(device.area_id)
            if area:
                return area.name
        return room.name

    def source_running(self) -> bool:
        if self.settings.get(CONF_HEAT_SOURCE_TYPE) == HEAT_SOURCE_SWITCH:
            state = self.hass.states.get(self.settings.get(CONF_HEAT_SOURCE_ENTITY, ""))
            return state is not None and state.state == STATE_ON
        return self.winner.room_id is not None

    def role(self, room: Room) -> str:
        """Role in the two-tier model, for the card and hvac_action."""
        if not room.hvac_on:
            return "off"
        if self.pause_reason(room):
            return "paused"
        if self.winner.room_id == room.room_id:
            return "winner"
        target = self.effective_target(room)
        # Rounded like select_winner, so a room 0.01 below target doesn't call for heat nobody gives
        if room.current is not None and round(target - room.current, 1) > 0:
            if self.source_running():
                return "passenger"
            return "calls_for_heat" if room.drives else "waiting"
        return "reached"

    def legacy_json(self) -> str:
        area = WINNER_OFF
        if self.winner.room_id and self.winner.room_id in self.rooms:
            area = self.area_label(self.rooms[self.winner.room_id])
        return json.dumps(
            {
                "area": area,
                "target": self.winner.target,
                "curr": self.winner.current,
                "delta": self.winner.delta,
            }
        )

    # ------------------------------------------------------- room mutators
    @callback
    def set_room_state(
        self,
        room_id: str,
        *,
        hvac_on: bool | None = None,
        target: float | None = None,
        preset: str | None | type[Ellipsis] = ...,
        manual_pause: bool | None = None,
    ) -> None:
        room = self.rooms[room_id]
        if hvac_on is not None:
            room.hvac_on = hvac_on
        if target is not None:
            room.target = target
        if preset is not ...:
            room.preset = preset  # type: ignore[assignment]
        if manual_pause is not None:
            room.manual_pause = manual_pause
        self.request_recompute()

    @callback
    def set_room_preset(self, room_id: str, preset: str | None) -> None:
        room = self.rooms[room_id]
        if preset == PRESET_ECO:
            self.set_room_state(room_id, target=room.eco_temp, preset=PRESET_ECO)
        elif preset == PRESET_WARM:
            self.set_room_state(room_id, target=room.warm_temp, preset=PRESET_WARM)
        else:
            self.set_room_state(room_id, preset=None)

    @callback
    def request_recompute(self) -> None:
        if self._started:
            self._debouncer.async_schedule_call()
        self._notify()

    @callback
    def _notify(self) -> None:
        async_dispatcher_send(self.hass, signal_update(self.entry.entry_id))

    # -------------------------------------------------------------- startup
    async def async_start(self) -> None:
        watched: dict[str, Callable[[Event[EventStateChangedData]], None]] = {}
        for room in self.rooms.values():
            cfg = room.config
            sensor = cfg[CONF_TEMP_SENSOR]
            self._read_temperature(room)
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass, [sensor], self._make_temp_listener(room.room_id)
                )
            )
            windows = cfg.get(CONF_WINDOWS) or []
            if windows:
                room.window_open = self._windows_open(room)
                self._unsubs.append(
                    async_track_state_change_event(
                        self.hass, windows, self._make_window_listener(room.room_id)
                    )
                )
            for trv in cfg.get(CONF_TRVS) or []:
                watched[trv] = self._on_device_change
            for number in cfg.get(CONF_EXT_TEMP) or []:
                watched[number] = self._make_ext_listener(room.room_id)

        presence = self.settings.get(CONF_PRESENCE)
        if presence:
            self.present = self._read_presence()
            watched[presence] = self._on_presence_change
        for key in (CONF_HEAT_SOURCE_ENTITY, CONF_CH_SWITCH):
            if self.settings.get(key):
                watched.setdefault(self.settings[key], self._on_device_change)

        for entity_id, listener in watched.items():
            self._unsubs.append(
                async_track_state_change_event(self.hass, [entity_id], listener)
            )

        minutes = int(self.settings.get(CONF_HEARTBEAT_MIN, DEFAULT_HEARTBEAT_MIN))
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._async_heartbeat, timedelta(minutes=max(minutes, 5))
            )
        )
        self._unsubs.append(async_at_started(self.hass, self._async_on_started))

    async def _async_on_started(self, _hass: HomeAssistant) -> None:
        self._started = True
        for room in self.rooms.values():
            self._push_external(room, force=True)
        await self._async_apply()

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for room in self.rooms.values():
            if room._window_timer:
                room._window_timer()
                room._window_timer = None
        self._debouncer.async_cancel()

    # ------------------------------------------------------------ listeners
    def _make_temp_listener(self, room_id: str):
        @callback
        def _listener(_event: Event[EventStateChangedData]) -> None:
            room = self.rooms[room_id]
            self._read_temperature(room)
            if self._started:
                self._push_external(room, force=False)
            self.request_recompute()

        return _listener

    def _make_ext_listener(self, room_id: str):
        @callback
        def _listener(event: Event[EventStateChangedData]) -> None:
            old = event.data["old_state"]
            # Only re-push when an input comes back, never fight normal updates
            if self._started and (old is None or old.state in _BAD_STATES):
                self._push_external(self.rooms[room_id], force=False)

        return _listener

    def _make_window_listener(self, room_id: str):
        @callback
        def _listener(_event: Event[EventStateChangedData]) -> None:
            room = self.rooms[room_id]
            raw_open = self._windows_open(room)
            if room._window_timer:
                room._window_timer()
                room._window_timer = None
            if raw_open == room.window_open:
                return
            cfg = room.config
            delay = (
                cfg.get(CONF_WINDOW_OPEN_DELAY, DEFAULT_WINDOW_OPEN_DELAY)
                if raw_open
                else cfg.get(CONF_WINDOW_CLOSE_DELAY, DEFAULT_WINDOW_CLOSE_DELAY)
            )

            @callback
            def _commit(_now: Any) -> None:
                room._window_timer = None
                room.window_open = raw_open
                _LOGGER.debug("%s: window %s", room.name, "open" if raw_open else "closed")
                self.request_recompute()

            room._window_timer = async_call_later(self.hass, float(delay), _commit)

        return _listener

    @callback
    def _on_presence_change(self, _event: Event[EventStateChangedData]) -> None:
        present = self._read_presence()
        if present != self.present:
            self.present = present
            _LOGGER.debug("Presence: %s", "home" if present else "away")
            self.request_recompute()

    @callback
    def _on_device_change(self, _event: Event[EventStateChangedData]) -> None:
        self.request_recompute()

    async def _async_heartbeat(self, _now: Any) -> None:
        for room in self.rooms.values():
            self._push_external(room, force=True)
        force = not self.settings.get(CONF_SKIP_UNCHANGED, DEFAULT_SKIP_UNCHANGED)
        await self._async_apply(force=force)

    # -------------------------------------------------------------- readers
    def _read_temperature(self, room: Room) -> None:
        state = self.hass.states.get(room.config[CONF_TEMP_SENSOR])
        room.current = _float(state.state) if state else None

    def _windows_open(self, room: Room) -> bool:
        for entity_id in room.config.get(CONF_WINDOWS) or []:
            state = self.hass.states.get(entity_id)
            if state is not None and state.state == STATE_ON:
                return True
        return False

    def _read_presence(self) -> bool:
        entity_id = self.settings.get(CONF_PRESENCE)
        if not entity_id:
            return True
        state = self.hass.states.get(entity_id)
        if state is None or state.state in _BAD_STATES:
            return True  # unknown presence keeps the house warm
        if state.state in (STATE_ON, STATE_HOME):
            return True
        if state.state in (STATE_OFF, STATE_NOT_HOME):
            return False
        if entity_id.startswith("zone."):
            return (_float(state.state) or 0) > 0
        # person/device_tracker in another zone counts as away
        return False

    # -------------------------------------------------------------- writers
    def _in_flight(self, entity_id: str, value: Any) -> bool:
        sent = self._sent.get(entity_id)
        return sent is not None and sent[0] == value and time.monotonic() - sent[1] < WRITE_GRACE_S

    async def _call(self, domain: str, service: str, data: dict[str, Any]) -> None:
        entity_id = data[ATTR_ENTITY_ID]
        value = data.get(ATTR_TEMPERATURE, data.get("value", service))
        self._sent[entity_id] = (value, time.monotonic())
        try:
            await self.hass.services.async_call(domain, service, data, blocking=False)
        except Exception as err:  # noqa: BLE001 - a broken device must not stop the loop
            _LOGGER.warning("Writing %s to %s failed: %s", value, entity_id, err)

    def _push_external(self, room: Room, *, force: bool) -> None:
        """Send the room temperature to every TRV external temperature input."""
        if room.current is None:
            return
        for entity_id in room.config.get(CONF_EXT_TEMP) or []:
            state = self.hass.states.get(entity_id)
            # "unknown" is a fresh input without a value yet: it needs its first write
            if state is None or state.state == STATE_UNAVAILABLE:
                continue
            attrs = state.attributes
            value = round_to_step(room.current, _float(attrs.get("step")))
            low, high = _float(attrs.get("min")), _float(attrs.get("max"))
            if low is not None:
                value = max(value, low)
            if high is not None:
                value = min(value, high)
            if not force and _float(state.state) == value:
                continue
            self.hass.async_create_task(
                self._call("number", "set_value", {ATTR_ENTITY_ID: entity_id, "value": value})
            )

    async def _async_apply(self, force: bool = False) -> None:
        """Recompute the winner and bring TRVs and the heat source in line."""
        demands = [
            RoomDemand(
                room.room_id,
                room.name,
                self.is_heating(room),
                self.effective_target(room),
                room.current,
                room.drives,
            )
            for room in self.rooms.values()
        ]
        self.winner = select_winner(demands, self.off_temp, self.winner.current)
        _LOGGER.debug("Recompute: winner %s target %s", self.winner.name, self.winner.target)

        for room in self.rooms.values():
            target = self.effective_target(room)
            is_winner = self.winner.room_id == room.room_id
            for trv in room.config.get(CONF_TRVS) or []:
                await self._sync_trv(trv, target, is_winner, room.buffer, force)

        await self._sync_heat_source(demands, force)
        self._notify()

    async def _sync_trv(
        self, entity_id: str, target: float, is_winner: bool, buffer: float, force: bool
    ) -> None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state == STATE_UNAVAILABLE:
            return
        attrs = state.attributes
        desired = trv_target(
            target,
            is_winner,
            buffer,
            _float(attrs.get(ATTR_TARGET_TEMP_STEP)),
            _float(attrs.get(ATTR_MIN_TEMP)),
            _float(attrs.get(ATTR_MAX_TEMP)),
        )
        if not force and _float(attrs.get(ATTR_TEMPERATURE)) == desired:
            return
        if not force and self._in_flight(entity_id, desired):
            return
        await self._call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: desired},
        )

    async def _sync_switch(self, entity_id: str, on: bool) -> None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in _BAD_STATES:
            return
        if (state.state == STATE_ON) == on:
            return
        service = SERVICE_TURN_ON if on else SERVICE_TURN_OFF
        if self._in_flight(entity_id, service):
            return
        await self._call("homeassistant", service, {ATTR_ENTITY_ID: entity_id})

    async def _sync_heat_source(self, demands: list[RoomDemand], force: bool) -> None:
        settings = self.settings
        source = settings.get(CONF_HEAT_SOURCE_ENTITY)
        any_heating = any(d.heating and d.drives for d in demands)

        if settings.get(CONF_CH_SWITCH):
            await self._sync_switch(settings[CONF_CH_SWITCH], any_heating)

        if not source:
            return
        if settings.get(CONF_HEAT_SOURCE_TYPE) == HEAT_SOURCE_SWITCH:
            state = self.hass.states.get(source)
            is_on = state is not None and state.state == STATE_ON
            hysteresis = float(settings.get(CONF_HYSTERESIS, DEFAULT_HYSTERESIS))
            await self._sync_switch(source, switch_should_run(demands, is_on, hysteresis))
            return

        # climate (ESPHome / DIYLess / generic) and OTGW: send the winner target
        state = self.hass.states.get(source)
        if state is None or state.state in _BAD_STATES:
            return
        desired = round(self.winner.target, 1)
        current = _float(state.attributes.get(ATTR_TEMPERATURE))
        if not force and current is not None and round(current, 1) == desired:
            return
        if not force and self._in_flight(source, desired):
            return
        await self._call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: source, ATTR_TEMPERATURE: desired},
        )
