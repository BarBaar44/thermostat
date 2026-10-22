"""Room climate entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_platform
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import CMTConfigEntry
from .const import (
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_STEP,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_STEP,
    PRESET_ECO,
    PRESET_WARM,
    SERVICE_PAUSE_ROOM,
    SERVICE_RESUME_ROOM,
    SUBENTRY_ROOM,
    signal_update,
)
from .controller import HeatingController, Room
from .entity import room_device_info

ATTR_PRESET = "preset_mode"
ATTR_MANUAL_PAUSE = "manual_pause"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CMTConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    controller = entry.runtime_data
    for sub_id, sub in entry.subentries.items():
        if sub.subentry_type != SUBENTRY_ROOM:
            continue
        async_add_entities(
            [RoomClimate(controller, controller.rooms[sub_id])],
            config_subentry_id=sub_id,
        )

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(SERVICE_PAUSE_ROOM, None, "async_pause")
    platform.async_register_entity_service(SERVICE_RESUME_ROOM, None, "async_resume")


class RoomClimate(ClimateEntity, RestoreEntity):
    """The single source of truth for what one room wants."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "room"
    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_preset_modes = [PRESET_NONE, PRESET_ECO, PRESET_WARM]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, controller: HeatingController, room: Room) -> None:
        self._controller = controller
        self._room = room
        self._attr_unique_id = f"{room.room_id}_climate"
        self._attr_device_info = room_device_info(controller, room)
        cfg = room.config
        self._attr_min_temp = float(cfg.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP))
        self._attr_max_temp = float(cfg.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP))
        self._attr_target_temperature_step = float(cfg.get(CONF_STEP, DEFAULT_STEP))
        self._attr_precision = 0.1

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        room = self._room
        last = await self.async_get_last_state()
        if last is not None:
            room.hvac_on = last.state == HVACMode.HEAT
            target = last.attributes.get(ATTR_TEMPERATURE)
            if target is not None:
                room.target = float(target)
            preset = last.attributes.get(ATTR_PRESET)
            room.preset = preset if preset in (PRESET_ECO, PRESET_WARM) else None
            room.manual_pause = bool(last.attributes.get(ATTR_MANUAL_PAUSE, False))
        else:
            room.target = room.warm_temp
            room.preset = PRESET_WARM
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_update(self._controller.entry.entry_id), self._refresh
            )
        )
        self._controller.request_recompute()

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()

    # ------------------------------------------------------------------ state
    @property
    def current_temperature(self) -> float | None:
        return self._room.current

    @property
    def target_temperature(self) -> float:
        return self._room.target

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self._room.hvac_on else HVACMode.OFF

    @property
    def preset_mode(self) -> str:
        return self._room.preset or PRESET_NONE

    @property
    def hvac_action(self) -> HVACAction:
        role = self._controller.role(self._room)
        if role in ("off", "paused"):
            return HVACAction.OFF
        if role in ("winner", "passenger") and self._controller.source_running():
            return HVACAction.HEATING
        return HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ctl = self._controller
        room = self._room
        return {
            "role": ctl.role(room),
            "paused_reason": ctl.pause_reason(room),
            ATTR_MANUAL_PAUSE: room.manual_pause,
            "window_open": room.window_open,
            "effective_target": ctl.effective_target(room),
            "is_winner": ctl.winner.room_id == room.room_id,
        }

    # -------------------------------------------------------------- commands
    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        hvac_mode = kwargs.get("hvac_mode")
        if hvac_mode is not None:
            self._controller.set_room_state(
                self._room.room_id, hvac_on=hvac_mode == HVACMode.HEAT
            )
        if temperature is not None:
            value = min(max(float(temperature), self.min_temp), self.max_temp)
            self._controller.set_room_state(self._room.room_id, target=value, preset=None)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        self._controller.set_room_state(self._room.room_id, hvac_on=hvac_mode == HVACMode.HEAT)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        self._controller.set_room_preset(
            self._room.room_id, None if preset_mode == PRESET_NONE else preset_mode
        )

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_pause(self) -> None:
        self._controller.set_room_state(self._room.room_id, manual_pause=True)

    async def async_resume(self) -> None:
        self._controller.set_room_state(self._room.room_id, manual_pause=False)
