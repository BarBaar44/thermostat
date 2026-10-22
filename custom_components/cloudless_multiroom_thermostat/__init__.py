"""Cloudless Multiroom Thermostat: multi-room heating from one heat source."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import CONF_AREA, DOMAIN, SERVICE_SET_ALL_OFF, SUBENTRY_ROOM
from .controller import HeatingController

PLATFORMS: list[Platform] = [Platform.CLIMATE, Platform.SENSOR, Platform.BINARY_SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type CMTConfigEntry = ConfigEntry[HeatingController]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register domain services."""

    async def _set_all_off(call: ServiceCall) -> None:
        entry_ids = call.data.get("config_entry_id")
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            if entry_ids and entry.entry_id not in entry_ids:
                continue
            controller: HeatingController = entry.runtime_data
            for room_id in controller.rooms:
                controller.set_room_state(room_id, hvac_on=False)

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_ALL_OFF,
        _set_all_off,
        schema=vol.Schema({vol.Optional("config_entry_id"): vol.All(cv.ensure_list, [cv.string])}),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CMTConfigEntry) -> bool:
    controller = HeatingController(hass, entry)
    entry.runtime_data = controller

    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Cloudless Multiroom Thermostat",
        model="Heating system",
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _sync_room_areas(hass, entry)
    await controller.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


def _sync_room_areas(hass: HomeAssistant, entry: CMTConfigEntry) -> None:
    """Move each room device to the area set in the room (suggested_area only works once)."""
    registry = dr.async_get(hass)
    for sub_id, sub in entry.subentries.items():
        area_id = sub.data.get(CONF_AREA)
        if sub.subentry_type != SUBENTRY_ROOM or not area_id:
            continue
        device = registry.async_get_device(identifiers={(DOMAIN, sub_id)})
        if device is not None and device.area_id != area_id:
            registry.async_update_device(device.id, area_id=area_id)


async def async_unload_entry(hass: HomeAssistant, entry: CMTConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: CMTConfigEntry) -> None:
    """Options changed or a room was added, edited or removed."""
    await hass.config_entries.async_reload(entry.entry_id)
