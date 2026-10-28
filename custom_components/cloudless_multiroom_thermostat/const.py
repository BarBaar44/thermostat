"""Constants for Cloudless Multiroom Thermostat."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "cloudless_multiroom_thermostat"

SUBENTRY_ROOM: Final = "room"

# System (config entry) keys
CONF_HEAT_SOURCE_TYPE: Final = "heat_source_type"
CONF_HEAT_SOURCE_ENTITY: Final = "heat_source_entity"
CONF_CH_SWITCH: Final = "ch_switch"
CONF_OFF_TEMP: Final = "off_temperature"
CONF_HYSTERESIS: Final = "hysteresis"
CONF_PRESENCE: Final = "presence_entity"
CONF_AWAY_MODE: Final = "away_mode"
CONF_SKIP_UNCHANGED: Final = "skip_unchanged_writes"
CONF_HEARTBEAT_MIN: Final = "external_temp_heartbeat"

HEAT_SOURCE_SWITCH: Final = "switch"
HEAT_SOURCE_CLIMATE: Final = "climate"
HEAT_SOURCE_OTGW: Final = "otgw"
HEAT_SOURCE_TYPES: Final = [HEAT_SOURCE_SWITCH, HEAT_SOURCE_CLIMATE, HEAT_SOURCE_OTGW]

AWAY_OFF: Final = "off"
AWAY_ECO: Final = "eco"
AWAY_MODES: Final = [AWAY_OFF, AWAY_ECO]

# Room (subentry) keys
CONF_ROOM_NAME: Final = "name"
CONF_TEMP_SENSOR: Final = "temperature_sensor"
CONF_TRVS: Final = "trvs"
CONF_EXT_TEMP: Final = "external_temperature_inputs"
CONF_WINDOWS: Final = "window_sensors"
CONF_WINDOW_OPEN_DELAY: Final = "window_open_delay"
CONF_WINDOW_CLOSE_DELAY: Final = "window_close_delay"
CONF_BUFFER: Final = "overshoot_buffer"
CONF_DRIVES_SOURCE: Final = "drives_heat_source"
CONF_MIN_TEMP: Final = "min_temp"
CONF_MAX_TEMP: Final = "max_temp"
CONF_STEP: Final = "target_temp_step"
CONF_ECO_TEMP: Final = "eco_temperature"
CONF_WARM_TEMP: Final = "warm_temperature"

# Defaults
DEFAULT_OFF_TEMP: Final = 10.0
DEFAULT_HYSTERESIS: Final = 0.3
DEFAULT_AWAY_MODE: Final = AWAY_OFF
DEFAULT_SKIP_UNCHANGED: Final = True
DEFAULT_HEARTBEAT_MIN: Final = 60
DEFAULT_WINDOW_OPEN_DELAY: Final = 120
DEFAULT_WINDOW_CLOSE_DELAY: Final = 60
DEFAULT_BUFFER: Final = 1.0
DEFAULT_DRIVES_SOURCE: Final = True
DEFAULT_MIN_TEMP: Final = 5.0
DEFAULT_MAX_TEMP: Final = 25.0
DEFAULT_STEP: Final = 0.5
DEFAULT_ECO_TEMP: Final = 15.0
DEFAULT_WARM_TEMP: Final = 19.0

# Experimental features (options)
CONF_EXPERIMENTAL: Final = "experimental"
CONF_EXP_ROOM: Final = "exp_room"  # subentry id of the room the night model follows
CONF_EXP_COMFORT_WEEKDAY: Final = "exp_comfort_weekday"
CONF_EXP_COMFORT_WEEKEND: Final = "exp_comfort_weekend"
CONF_EXP_OUTDOOR: Final = "exp_outdoor"
CONF_EXP_WEATHER: Final = "exp_weather"
CONF_EXP_GAS: Final = "exp_gas"
CONF_EXP_FLAME: Final = "exp_flame"
CONF_EXP_DHW: Final = "exp_dhw"
CONF_EXP_CH_ACTIVE: Final = "exp_ch_active"
CONF_EXP_WATER: Final = "exp_water"
CONF_EXP_RETURN: Final = "exp_return"
CONF_EXP_PEOPLE: Final = "exp_people"
CONF_EXP_KP: Final = "exp_kp"
CONF_EXP_KI: Final = "exp_ki"
CONF_EXP_KD: Final = "exp_kd"
CONF_EXP_INTEGRAL: Final = "exp_integral"
CONF_EXP_GAS_BEFORE6: Final = "exp_gas_price_before6"
CONF_EXP_GAS_AFTER6: Final = "exp_gas_price_after6"
EXP_ENTITY_KEYS: Final = (
    CONF_EXP_OUTDOOR, CONF_EXP_WEATHER, CONF_EXP_GAS, CONF_EXP_FLAME, CONF_EXP_DHW,
    CONF_EXP_CH_ACTIVE, CONF_EXP_WATER, CONF_EXP_RETURN, CONF_EXP_PEOPLE, CONF_EXP_KP,
    CONF_EXP_KI, CONF_EXP_KD, CONF_EXP_INTEGRAL, CONF_EXP_GAS_BEFORE6, CONF_EXP_GAS_AFTER6,
)
DEFAULT_COMFORT_WEEKDAY: Final = "07:30:00"
DEFAULT_COMFORT_WEEKEND: Final = "08:00:00"
EXP_SUMMARY_HOUR: Final = 21  # fixed: the night model measures the evening drop from 21:00
SERVICE_EXPERIMENTAL_REPORT: Final = "experimental_report"
DOCS_EXPERIMENTAL_URL: Final = (
    "https://github.com/BarBaar44/thermostat/blob/main/docs/experimental.md"
)

# Presets
PRESET_ECO: Final = "eco"
PRESET_WARM: Final = "warm"

# Why a room is held off although its own mode is heat
PAUSE_WINDOW: Final = "window"
PAUSE_AWAY: Final = "away"
PAUSE_MANUAL: Final = "manual"

# Services
SERVICE_PAUSE_ROOM: Final = "pause_room"
SERVICE_RESUME_ROOM: Final = "resume_room"
SERVICE_SET_ALL_OFF: Final = "set_all_off"

# Seconds an issued write counts as "in flight" before it may be resent
WRITE_GRACE_S: Final = 120
# Debounce for recomputing after a burst of state changes
RECOMPUTE_COOLDOWN_S: Final = 1.0

WINNER_OFF: Final = "Off"


def signal_update(entry_id: str) -> str:
    """Dispatcher signal fired after every recompute."""
    return f"{DOMAIN}_{entry_id}_update"
