# Changelog

## Unreleased

- **Experimental settings page: fewer fields.** Flow water temperature, return water temperature, PID integral and the two gas price fields are gone. They were only recorded or displayed and never used in a calculation. Existing records keep their old values; the evening report and the sensors no longer show them.
- **Docs:** [docs/experimental.md](docs/experimental.md) now explains every entity on the experimental settings page: what it is, why it's needed, whether it's required and where to get it.
- **Removed the legacy YAML setup and the pyscript tuner** (`automations/`, `scripts/`, `sensors/`, `deltaTemperature`, `housewarming.yaml`, `pyscript/`). The integration replaces them; they're still in the [v1.1.0 tag](https://github.com/BarBaar44/thermostat/tree/v1.1.0) for reference and for carrying tuner history over.

## 1.1.0

- **Rooms that only heat along:** a new room option, **Drives the heat source** (default on). Turned off, the room never becomes the winner, never starts an on/off heat source and doesn't turn on the central heating enable switch, but its TRVs still get its target and open while another room drives the heat source. For a hallway or another space that cools much faster than the rooms around it. New role `waiting` while such a room is below target and nothing else needs heat. This is not an offset follower: the room heats to its own target, not "another room minus a few degrees".
- **Evening report:** shows the progress of each experimental feature towards stable, measured against the exit criteria in [docs/experimental.md](docs/experimental.md).
- **Integration icon:** the integration now ships its own icon (`brand/`), shown on the integrations page. Source: [docs/icon.svg](docs/icon.svg).
- **Fix:** renaming a room's climate or binary sensor entity ID made the entity disappear until the integration was reloaded. Room devices now link to the heating system device with `via_device_id`; the old `via_device` tuple is deprecated and raises when Home Assistant itself re-adds the entity.
- **Fix:** the room area lookup (winning room area) uses `async_get_device_by_identifier`; `async_get_device` is deprecated since Home Assistant 2026.10.
- **Gas per degree-day sensor name:** "Gas per degree-day" instead of "Heating gas per degree-day", so the entity is no longer called *Heating Heating gas per degree-day* and a new install gets `sensor.<system>_gas_per_degree_day`. Existing installs keep their entity ID; their friendly name changes to *Heating Gas per degree-day*. [Coming from the pyscript tuner](docs/experimental.md#coming-from-the-pyscript-tuner) explains how to carry its history over without an entity ID clash.
- **Tests:** run against Home Assistant 2026.10.0 (the minimum in `hacs.json`) on Python 3.14.

## 1.0.0

First release of **Cloudless Multiroom Thermostat**, the Home Assistant integration that replaces the YAML setup in this repo.

- **Heating system from the UI:** one heat source per system, as an on/off switch (with hysteresis), a climate entity (ESPHome, DIYLess or any thermostat with a setpoint) or an OpenTherm Gateway. Optional central heating enable switch, off temperature, presence with away mode off or eco.
- **Any number of rooms**, added and edited from the integration page in two steps: name and temperatures, then sensors and valves. Rooms without TRVs are allowed.
- **Room climate entities** with Heat/Off, eco and warm presets and a manual target; role and pause reason as attributes. State survives restarts.
- **Winner selection** as before: the room with the biggest gap drives the heat source, its TRVs get the overshoot buffer, other rooms get their exact target. TRV writes are skipped when nothing changes.
- **TRV external temperature inputs** fed with the room temperature, with a heartbeat for TRVs that fall back to their own sensor.
- **Window and door pause** per room with open and close delays; the room returns to what it was.
- **Sensors:** winning room, winning room temperature (PID process value), legacy Delta temperature JSON for migrating, and per room Paused and Window.
- **Services:** `pause_room`, `resume_room`, `set_all_off`, `experimental_report`.
- **Experimental features**, off by default and observe only: warm-up episodes with PID advice, night cooldown model with a morning prediction, gas per degree-day with the hot water split, and an evening report. See [docs/experimental.md](docs/experimental.md).
- **Test environment** in `dev/`: a simulated house as a Docker container or a Virtual Machine Manager VM, with a script that sets everything up through the real forms.
- **pyscript tuner** (`pyscript/`): fixed overlapping writes to `night_state.json`, which could crash a trigger or lose the night in progress or the evening prediction.
