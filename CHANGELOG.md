# Changelog

## Unreleased

- **Fix:** a TRV that was switched off (on the device, or by another automation) stayed off for good: the integration only wrote setpoints, which an off TRV ignores, and kept rewriting them. A TRV that reports off and supports heat is now set back to heat. The integration holds rooms off with the off temperature, never with the TRV's off mode, so this doesn't change how rooms switch off.
- **Fix:** changing a room's eco or warm temperature in Edit room did nothing while that preset was active: the room kept the old temperature until the preset was selected again. A room on a preset now takes its temperature from the room settings after an edit or a restart, and a manual target is kept within a changed minimum and maximum.
- **Fix:** a learning features file that can't be read (not valid JSON, for example after a disk problem) stopped the whole integration from loading, so the heating didn't run. Such a file is now moved aside as `<name>.corrupt-<time>`, the feature starts over, and any other error while starting the learning features is logged while the heating carries on without them.

## 1.4.1

- **Fix:** a report on demand (`experimental_report`) took the "Boiler settings changed" line away from the evening report: it stored the new settings, so at 21:00 nothing had changed any more. Only the 21:00 report stores them now.
- **Night prediction retries a missing forecast:** if the weather forecast is unavailable at 21:00, it is tried again at 22:00 and 23:00. When a retry works, the prediction is made and the evening report is replaced, so one forecast outage no longer loses the whole night's prediction.
- **Repository renamed** to [BarBaar44/Cloudless-Multiroom-Thermostat](https://github.com/BarBaar44/Cloudless-Multiroom-Thermostat). The documentation and issue links in the integration, the README, the docs and the learning features report point to the new name. GitHub redirects the old URL, but HACS users can update the custom repository to the new one.

## 1.4.0

- **Boiler settings in the learning features:** two new optional fields on the learning features page, **Boiler maximum flow temperature** (OpenTherm maximum CH setpoint) and **Boiler hot water setpoint**. Both are only read. Each warm-up episode stores the flow limit (`max_ch`); PID advice and the progress check only compare warm-ups with the same limit, so lowering the limit on the boiler is not credited to (or blamed on) the PID values. A warm-up during which the limit changed is skipped (`max_ch_changed`). Each gas day stores both settings (`max_ch`, `dhw_setpoint`). The evening report gets a line when either changed since the last report. Setting the flow limit field starts the advice and the progress check over: older warm-ups without a recorded limit are not compared with new ones. Details: [docs/experimental.md](docs/experimental.md#boiler-maximum-flow-temperature-boiler-hot-water-setpoint).
- **Fix:** a warm-up's highest flow temperature (`water_peak`, "water max" in the report) included hot-water draws, when a combi boiler runs at 60 °C or more. It now leaves out minutes with hot water active.

## 1.3.1

- **Fix:** with no room calling for heat and the heating room exactly at its target, the heat source's current temperature (and `sensor.<system>_winning_room_temperature`) was not taken from that room. It kept the previous winner's value, which could be another room's stale temperature, or stayed unknown right after a restart until the room moved 0.1 °C. A room at target now fills it, as an overshooting room already did.
- **Warm-up episodes store the room that led them** (`room_id` in `episodes.jsonl`), and the room to follow is matched by that id instead of its name. Before, renaming the followed room marked its new episodes `other_room` and dropped its history from the night model's warm-up estimate. Older records without `room_id` are still matched by name (now ignoring case).
- **Cleanup:** removed the leftover "Living Room" defaults from the pyscript days in the learning features code. The integration never used them (it always passes the room chosen under **Room to follow**), but they read as if the room were hard-coded.

## 1.3.0

- **Fix:** warm-up episodes were never judged. The tuner sampled the winning room, and the winner turns Off (target = off temperature) the moment the room reaches its target, so an episode never saw the target reached: it ran for 4 hours and was recorded as invalid (`target_changed`, `target_not_reached`, no overshoot). An episode now follows the room that led it from the start.
- **Warm-up episodes survive a restart:** the episode in progress is saved every 5 minutes and when Home Assistant stops (new file `episode_state.json`), and picked up again after a restart such as an update. Missed minutes are filled with the last sample; after more than 15 minutes the episode is dropped.

## 1.2.1

- **Experimental features are now called learning features (observe only).** They never changed the heating, and what keeps them from stable is proof, not data collection, so the old name oversold the risk. The option, the step title, the service name and the evening report say *learning features*; each feature keeps its own maturity (alpha or beta) in [docs/experimental.md](docs/experimental.md). Nothing to migrate: the option key, the service `experimental_report`, entity IDs, file names and the docs URL are unchanged.
- **Fix:** a room less than 0.05 °C below its target showed role `calls_for_heat` (or `waiting`/`passenger`) while no room was the winner and the heat source stayed off. The role now rounds the gap to 0.1 °C, the same as the winner selection, so it shows `reached`.

## 1.2.0

- **Hot water gas rate is measured:** the hot-water split no longer uses a fixed 0.019 m³ of gas per minute. The integration measures the rate from hot-water draws outside heating hours (draws under 3 minutes left out) and uses it once it has 60 minutes; until then 0.019. A new optional field on the experimental settings page pins your own value instead. The evening report and the gas per degree-day sensor show which rate is used. Only days split after a change use the new rate; each split day stores its rate. Details: [docs/experimental.md](docs/experimental.md#hot-water-gas-rate).
- **Fix:** a room with **Drives the heat source** off (new in 1.1.0) kept the experimental features seeing the heating as on. While such a room stayed on heat overnight, no night was recorded for the night model, and a warm-up episode didn't end when the heating stopped. Those rooms are now ignored there, as they already were for the central heating enable switch.
- **Removed the legacy YAML setup and the pyscript tuner** (`automations/`, `scripts/`, `sensors/`, `deltaTemperature`, `housewarming.yaml`, `pyscript/`). The integration replaces them; they're still in the [v1.1.0 tag](https://github.com/BarBaar44/thermostat/tree/v1.1.0) for reference and for carrying tuner history over.
- **Docs:** [docs/experimental.md](docs/experimental.md) now explains every entity on the experimental settings page: what it is, why it's needed, whether it's required and where to get it.

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
