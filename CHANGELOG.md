# Changelog

## Unreleased

- **Fix:** renaming a room's climate or binary sensor entity ID made the entity disappear until the integration was reloaded. Room devices now link to the heating system device with `via_device_id`; the old `via_device` tuple is deprecated and raises when Home Assistant itself re-adds the entity.
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
