"""System sensors: winning room, process value, legacy delta JSON."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CMTConfigEntry
from .const import signal_update
from .controller import HeatingController
from .entity import system_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CMTConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    controller = entry.runtime_data
    async_add_entities(
        [
            WinnerSensor(controller),
            ProcessTemperatureSensor(controller),
            LegacyDeltaSensor(controller),
        ]
    )


class _SystemSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, controller: HeatingController, key: str) -> None:
        self._controller = controller
        self._attr_translation_key = key
        self._attr_unique_id = f"{controller.entry.entry_id}_{key}"
        self._attr_device_info = system_device_info(controller)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_update(self._controller.entry.entry_id), self._refresh
            )
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()


class WinnerSensor(_SystemSensor):
    """Room driving the heat source, or Off."""

    _attr_icon = "mdi:trophy-outline"

    def __init__(self, controller: HeatingController) -> None:
        super().__init__(controller, "winner")

    @property
    def native_value(self) -> str:
        return self._controller.winner.name

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ctl = self._controller
        winner = ctl.winner
        area = None
        if winner.room_id and winner.room_id in ctl.rooms:
            area = ctl.area_label(ctl.rooms[winner.room_id])
        return {
            "area": area,
            "target": winner.target,
            "current": winner.current,
            "delta": winner.delta,
            "present": ctl.present,
        }


class ProcessTemperatureSensor(_SystemSensor):
    """Current temperature of the winning room, the process value for a firmware PID."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1

    def __init__(self, controller: HeatingController) -> None:
        super().__init__(controller, "process_temperature")

    @property
    def native_value(self) -> float | None:
        return self._controller.winner.current


class LegacyDeltaSensor(_SystemSensor):
    """Same JSON state as the old Delta Temperature template, for migration only."""

    _attr_entity_registry_enabled_default = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:code-json"

    def __init__(self, controller: HeatingController) -> None:
        super().__init__(controller, "legacy_delta")

    @property
    def native_value(self) -> str:
        return self._controller.legacy_json()
