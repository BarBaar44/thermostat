"""Shared device info helpers."""

from __future__ import annotations

from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.device_registry import DeviceInfo

from .const import CONF_AREA, DOMAIN
from .controller import HeatingController, Room

MANUFACTURER = "Cloudless Multiroom Thermostat"


def room_device_info(controller: HeatingController, room: Room) -> DeviceInfo:
    """One device per room, placed in the room's area."""
    suggested_area = None
    area_id = room.config.get(CONF_AREA)
    if area_id:
        area = ar.async_get(controller.hass).async_get_area(area_id)
        suggested_area = area.name if area else None
    return DeviceInfo(
        identifiers={(DOMAIN, room.room_id)},
        name=room.name,
        manufacturer=MANUFACTURER,
        model="Room",
        suggested_area=suggested_area,
        via_device=(DOMAIN, controller.entry.entry_id),
    )


def system_device_info(controller: HeatingController) -> DeviceInfo:
    """The heating system itself (winner, process value)."""
    return DeviceInfo(
        identifiers={(DOMAIN, controller.entry.entry_id)},
        name=controller.entry.title,
        manufacturer=MANUFACTURER,
        model="Heating system",
    )
