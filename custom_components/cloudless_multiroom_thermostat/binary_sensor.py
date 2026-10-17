"""Per room binary sensors: window open (debounced) and paused."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CMTConfigEntry
from .const import CONF_WINDOWS, SUBENTRY_ROOM, signal_update
from .controller import HeatingController, Room
from .entity import room_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CMTConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    controller = entry.runtime_data
    for sub_id, sub in entry.subentries.items():
        if sub.subentry_type != SUBENTRY_ROOM:
            continue
        room = controller.rooms[sub_id]
        entities: list[BinarySensorEntity] = [PausedSensor(controller, room)]
        if room.config.get(CONF_WINDOWS):
            entities.append(WindowSensor(controller, room))
        async_add_entities(entities, config_subentry_id=sub_id)


class _RoomBinary(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, controller: HeatingController, room: Room, key: str) -> None:
        self._controller = controller
        self._room = room
        self._attr_translation_key = key
        self._attr_unique_id = f"{room.room_id}_{key}"
        self._attr_device_info = room_device_info(controller, room)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_update(self._controller.entry.entry_id), self._refresh
            )
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()


class WindowSensor(_RoomBinary):
    """Window state after the open/close delays, as the engine sees it."""

    _attr_device_class = BinarySensorDeviceClass.WINDOW

    def __init__(self, controller: HeatingController, room: Room) -> None:
        super().__init__(controller, room, "window")

    @property
    def is_on(self) -> bool:
        return self._room.window_open


class PausedSensor(_RoomBinary):
    """On while the room is set to heat but held off (window, away, manual)."""

    _attr_icon = "mdi:radiator-off"

    def __init__(self, controller: HeatingController, room: Room) -> None:
        super().__init__(controller, room, "paused")

    @property
    def is_on(self) -> bool:
        return self._controller.pause_reason(self._room) is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"reason": self._controller.pause_reason(self._room)}
