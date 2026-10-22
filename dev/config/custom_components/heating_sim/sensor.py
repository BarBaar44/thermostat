"""Room and outdoor temperature sensors."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfTemperature

from . import DOMAIN, HeatingSim, SimRoom
from .entity import SimEntity


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    if discovery_info is None:
        return
    sim: HeatingSim = hass.data[DOMAIN]
    async_add_entities(
        [RoomTemperature(room) for room in sim.rooms] + [OutdoorTemperature(sim), GasMeter(sim)]
    )


class _Temp(SimEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1


class RoomTemperature(_Temp):
    def __init__(self, room: SimRoom) -> None:
        self._room = room
        self._attr_name = f"Sim {room.name} temperature"
        self._attr_unique_id = f"heating_sim_{room.key}_temperature"

    @property
    def native_value(self) -> float:
        return round(self._room.temperature, 1)


class OutdoorTemperature(_Temp):
    _attr_name = "Sim outdoor temperature"
    _attr_unique_id = "heating_sim_outdoor_temperature"

    def __init__(self, sim: HeatingSim) -> None:
        self._sim = sim

    @property
    def native_value(self) -> float:
        return self._sim.outdoor


class GasMeter(SimEntity, SensorEntity):
    """Total gas meter, like a P1 meter: grows while the burner runs."""

    _attr_name = "Sim gas meter"
    _attr_unique_id = "heating_sim_gas"
    _attr_device_class = SensorDeviceClass.GAS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "m³"
    _attr_suggested_display_precision = 3

    def __init__(self, sim: HeatingSim) -> None:
        self._sim = sim

    @property
    def native_value(self) -> float:
        return round(self._sim.gas_m3, 3)
