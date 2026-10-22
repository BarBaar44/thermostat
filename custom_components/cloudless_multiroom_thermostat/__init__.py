"""Cloudless Multiroom Thermostat: multi-room heating from one heat source."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_EXPERIMENTAL,
    DOMAIN,
    SERVICE_EXPERIMENTAL_REPORT,
    SERVICE_SET_ALL_OFF,
)
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

    async def _experimental_report(call: ServiceCall) -> None:
        entry_ids = call.data.get("config_entry_id")
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            if entry_ids and entry.entry_id not in entry_ids:
                continue
            runner = entry.runtime_data.experimental
            if runner is not None:
                await runner.async_report()

    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPERIMENTAL_REPORT,
        _experimental_report,
        schema=vol.Schema({vol.Optional("config_entry_id"): vol.All(cv.ensure_list, [cv.string])}),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CMTConfigEntry) -> bool:
    controller = HeatingController(hass, entry)
    entry.runtime_data = controller
    if controller.settings.get(CONF_EXPERIMENTAL):
        from .experimental.runner import ExperimentalRunner  # noqa: PLC0415

        controller.experimental = ExperimentalRunner(hass, controller)

    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Cloudless Multiroom Thermostat",
        model="Heating system",
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await controller.async_start()
    if controller.experimental is not None:
        await controller.experimental.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CMTConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        if entry.runtime_data.experimental is not None:
            await entry.runtime_data.experimental.async_stop()
        await entry.runtime_data.async_stop()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: CMTConfigEntry) -> None:
    """Options changed or a room was added, edited or removed."""
    await hass.config_entries.async_reload(entry.entry_id)
