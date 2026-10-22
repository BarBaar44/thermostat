"""Weather with an hourly forecast, for the night prediction."""

from __future__ import annotations

from datetime import timedelta
import math

from homeassistant.components.weather import Forecast, WeatherEntity, WeatherEntityFeature
from homeassistant.const import UnitOfSpeed, UnitOfTemperature
from homeassistant.util import dt as dt_util

from . import DOMAIN, HeatingSim
from .entity import SimEntity


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    if discovery_info is None:
        return
    async_add_entities([SimWeather(hass.data[DOMAIN])])


class SimWeather(SimEntity, WeatherEntity):
    """Outdoor temperature with a gentle day/night swing around the configured value."""

    _attr_name = "Sim weather"
    _attr_unique_id = "heating_sim_weather"
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_wind_speed_unit = UnitOfSpeed.KILOMETERS_PER_HOUR
    _attr_supported_features = WeatherEntityFeature.FORECAST_HOURLY
    _attr_condition = "cloudy"
    _attr_native_wind_speed = 12.0

    def __init__(self, sim: HeatingSim) -> None:
        self._sim = sim

    def _temp_at(self, when) -> float:
        hour = dt_util.as_local(when).hour
        return round(self._sim.outdoor + 2.0 * math.cos((hour - 15) / 24 * 2 * math.pi), 1)

    @property
    def native_temperature(self) -> float:
        return self._sim.outdoor

    async def async_forecast_hourly(self) -> list[Forecast]:
        start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
        return [
            Forecast(
                datetime=(start + timedelta(hours=h)).isoformat(),
                native_temperature=self._temp_at(start + timedelta(hours=h)),
                condition="cloudy",
            )
            for h in range(1, 25)
        ]
