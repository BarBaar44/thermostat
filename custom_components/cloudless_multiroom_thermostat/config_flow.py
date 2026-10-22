"""Config flow: the heating system, its options, and rooms as subentries."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    AWAY_MODES,
    CONF_AWAY_MODE,
    CONF_BUFFER,
    CONF_CH_SWITCH,
    CONF_ECO_TEMP,
    CONF_EXT_TEMP,
    CONF_HEARTBEAT_MIN,
    CONF_HEAT_SOURCE_ENTITY,
    CONF_HEAT_SOURCE_TYPE,
    CONF_HYSTERESIS,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_OFF_TEMP,
    CONF_PRESENCE,
    CONF_ROOM_NAME,
    CONF_SKIP_UNCHANGED,
    CONF_STEP,
    CONF_TEMP_SENSOR,
    CONF_TRVS,
    CONF_WARM_TEMP,
    CONF_WINDOW_CLOSE_DELAY,
    CONF_WINDOW_OPEN_DELAY,
    CONF_WINDOWS,
    DEFAULT_AWAY_MODE,
    DEFAULT_BUFFER,
    DEFAULT_ECO_TEMP,
    DEFAULT_HEARTBEAT_MIN,
    DEFAULT_HYSTERESIS,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_OFF_TEMP,
    DEFAULT_SKIP_UNCHANGED,
    DEFAULT_STEP,
    DEFAULT_WARM_TEMP,
    DEFAULT_WINDOW_CLOSE_DELAY,
    DEFAULT_WINDOW_OPEN_DELAY,
    DOMAIN,
    HEAT_SOURCE_CLIMATE,
    HEAT_SOURCE_OTGW,
    HEAT_SOURCE_SWITCH,
    HEAT_SOURCE_TYPES,
    SUBENTRY_ROOM,
)

DEFAULT_TITLE = "Heating"


def _temp(minimum: float, maximum: float, step: float = 0.5) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement="°C",
        )
    )


def _seconds() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0, max=3600, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="s"
        )
    )


def _entities(domain: str | list[str], multiple: bool = False, **kwargs: Any) -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=domain, multiple=multiple, **kwargs)
    )


def heat_source_schema(source_type: str) -> vol.Schema:
    """Entities and tuning that depend on the heat source type."""
    if source_type == HEAT_SOURCE_SWITCH:
        return vol.Schema(
            {
                vol.Required(CONF_HEAT_SOURCE_ENTITY): _entities(["switch", "input_boolean"]),
                vol.Required(CONF_HYSTERESIS, default=DEFAULT_HYSTERESIS): _temp(0.1, 2.0, 0.1),
            }
        )
    source = (
        _entities("climate", integration="opentherm_gw")
        if source_type == HEAT_SOURCE_OTGW
        else _entities("climate")
    )
    return vol.Schema(
        {
            vol.Required(CONF_HEAT_SOURCE_ENTITY): source,
            vol.Optional(CONF_CH_SWITCH): _entities(["switch", "input_boolean"]),
        }
    )


SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_OFF_TEMP, default=DEFAULT_OFF_TEMP): _temp(5, 16),
        vol.Optional(CONF_PRESENCE): _entities(
            ["binary_sensor", "input_boolean", "person", "zone", "group", "device_tracker"]
        ),
        vol.Required(CONF_AWAY_MODE, default=DEFAULT_AWAY_MODE): selector.SelectSelector(
            selector.SelectSelectorConfig(options=AWAY_MODES, translation_key=CONF_AWAY_MODE)
        ),
        vol.Required(CONF_SKIP_UNCHANGED, default=DEFAULT_SKIP_UNCHANGED): selector.BooleanSelector(),
        vol.Required(CONF_HEARTBEAT_MIN, default=DEFAULT_HEARTBEAT_MIN): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=5, max=240, step=5, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="min"
            )
        ),
    }
)


class CloudlessMultiroomThermostatFlow(ConfigFlow, domain=DOMAIN):
    """Create a heating system."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_ROOM: RoomSubentryFlow}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SettingsOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data[CONF_NAME] = user_input[CONF_NAME]
            self._data[CONF_HEAT_SOURCE_TYPE] = user_input[CONF_HEAT_SOURCE_TYPE]
            return await self.async_step_heat_source()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=DEFAULT_TITLE): selector.TextSelector(),
                    vol.Required(CONF_HEAT_SOURCE_TYPE, default=HEAT_SOURCE_CLIMATE): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=HEAT_SOURCE_TYPES, translation_key=CONF_HEAT_SOURCE_TYPE
                        )
                    ),
                }
            ),
        )

    async def async_step_heat_source(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_settings()
        return self.async_show_form(
            step_id="heat_source",
            data_schema=heat_source_schema(self._data[CONF_HEAT_SOURCE_TYPE]),
        )

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            title = self._data.pop(CONF_NAME)
            return self.async_create_entry(title=title, data=self._data)
        return self.async_show_form(step_id="settings", data_schema=SETTINGS_SCHEMA)


class SettingsOptionsFlow(OptionsFlow):
    """Edit heat source entities and system settings (not the source type)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = {**self.config_entry.data, **self.config_entry.options}
        source_type = current[CONF_HEAT_SOURCE_TYPE]
        schema = vol.Schema(
            {**heat_source_schema(source_type).schema, **SETTINGS_SCHEMA.schema}
        )
        if user_input is not None:
            # Optional fields left empty must be removed, not kept from data
            options = {k: v for k, v in user_input.items() if v not in (None, "")}
            for key in (CONF_CH_SWITCH, CONF_PRESENCE):
                options.setdefault(key, None)
            return self.async_create_entry(data=options)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, {k: v for k, v in current.items() if v is not None}
            ),
        )


# Step 1: name and temperature settings
ROOM_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ROOM_NAME): selector.TextSelector(),
        vol.Required(CONF_ECO_TEMP, default=DEFAULT_ECO_TEMP): _temp(5, 30),
        vol.Required(CONF_WARM_TEMP, default=DEFAULT_WARM_TEMP): _temp(5, 30),
        vol.Required(CONF_BUFFER, default=DEFAULT_BUFFER): _temp(0, 3, 0.1),
        vol.Required(CONF_MIN_TEMP, default=DEFAULT_MIN_TEMP): _temp(5, 30),
        vol.Required(CONF_MAX_TEMP, default=DEFAULT_MAX_TEMP): _temp(5, 35),
        vol.Required(CONF_STEP, default=str(DEFAULT_STEP)): selector.SelectSelector(
            selector.SelectSelectorConfig(options=["0.1", "0.5", "1.0"])
        ),
    }
)

# Step 2: sensors and valves
SENSORS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TEMP_SENSOR): _entities("sensor", device_class="temperature"),
        vol.Optional(CONF_TRVS, default=[]): _entities("climate", multiple=True),
        vol.Optional(CONF_EXT_TEMP, default=[]): _entities("number", multiple=True),
        vol.Optional(CONF_WINDOWS, default=[]): _entities(["binary_sensor", "input_boolean"], multiple=True),
        vol.Required(CONF_WINDOW_OPEN_DELAY, default=DEFAULT_WINDOW_OPEN_DELAY): _seconds(),
        vol.Required(CONF_WINDOW_CLOSE_DELAY, default=DEFAULT_WINDOW_CLOSE_DELAY): _seconds(),
    }
)


def validate_room(data: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    low, high = float(data[CONF_MIN_TEMP]), float(data[CONF_MAX_TEMP])
    if low >= high:
        errors[CONF_MAX_TEMP] = "max_below_min"
    for key in (CONF_ECO_TEMP, CONF_WARM_TEMP):
        if not low <= float(data[key]) <= high:
            errors[key] = "preset_out_of_range"
    return errors


class RoomSubentryFlow(ConfigSubentryFlow):
    """Add or edit a room in two steps: room and temperatures, then sensors."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._editing = False

    @property
    def _current(self) -> dict[str, Any]:
        """Stored room data when editing, else empty."""
        return dict(self._get_reconfigure_subentry().data) if self._editing else {}

    def _name_taken(self, name: str) -> bool:
        own_id = self._get_reconfigure_subentry().subentry_id if self._editing else None
        return any(
            sub.subentry_type == SUBENTRY_ROOM
            and sub.subentry_id != own_id
            and sub.title.casefold() == name.casefold()
            for sub in self._get_entry().subentries.values()
        )

    # Step 1 -----------------------------------------------------------------
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._room_step("user", user_input)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        self._editing = True
        return await self._room_step("reconfigure", user_input)

    async def _room_step(self, step_id: str, user_input: dict[str, Any] | None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = {**user_input, CONF_ROOM_NAME: user_input[CONF_ROOM_NAME].strip()}
            errors = validate_room(user_input)
            if not user_input[CONF_ROOM_NAME]:
                errors[CONF_ROOM_NAME] = "name_required"
            elif self._name_taken(user_input[CONF_ROOM_NAME]):
                errors[CONF_ROOM_NAME] = "name_exists"
            if not errors:
                user_input[CONF_STEP] = float(user_input[CONF_STEP])
                self._data.update(user_input)
                return await self.async_step_sensors()
        values = user_input if user_input is not None else self._current
        if values:
            values = {**values, CONF_STEP: str(values.get(CONF_STEP, DEFAULT_STEP))}
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(ROOM_SCHEMA, values),
            errors=errors,
        )

    # Step 2 -----------------------------------------------------------------
    async def async_step_sensors(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        if user_input is not None:
            # "area" was stored by an earlier version; drop it on edit
            data = {**{k: v for k, v in self._data.items() if k != "area"}, **user_input}
            title = data[CONF_ROOM_NAME]
            if self._editing:
                return self.async_update_and_abort(
                    self._get_entry(), self._get_reconfigure_subentry(), title=title, data=data
                )
            return self.async_create_entry(title=title, data=data)
        return self.async_show_form(
            step_id="sensors",
            data_schema=self.add_suggested_values_to_schema(SENSORS_SCHEMA, self._current),
            description_placeholders={"room": self._data[CONF_ROOM_NAME]},
        )
