# 🌡️ Cloudless Multiroom Thermostat

A Home Assistant integration for multi-room central heating without a cloud. Any number of rooms, each with its own temperature sensor and radiator valves, share one heat source: the room that needs heat most drives the boiler, and every other room heats along through its own valves. Everything is set up from the UI and runs locally.

- **No cloud:** Home Assistant, your TRVs and your boiler thermostat, nothing else.
- **Any number of rooms**, each with its own target, presets and window sensors.
- **One heat source**, as an on/off relay, an OpenTherm thermostat (ESPHome, DIYLess) or an OpenTherm Gateway.
- **Presence aware:** rooms go off or to eco when everybody is away, and come back when someone is home.
- **Open window detection** per room.

## Contents

- [Install](#install)
- [Set up](#set-up)
- [What it creates](#what-it-creates)
- [How it works](#how-it-works)
- [Experimental features](#experimental-features)
- [Dashboard: Heating Control Card](#dashboard-heating-control-card)
- [Example setup](#example-setup)
- [ESPHome OpenTherm thermostat (`thermostat.yaml`)](#esphome-opentherm-thermostat-thermostatyaml)
- [Development and test environment](#development-and-test-environment)

## Install

Requires Home Assistant 2026.10 or newer.

1. In HACS: **⋮ > Custom repositories**, add `https://github.com/BarBaar44/thermostat` with type **Integration**.
2. Install **Cloudless Multiroom Thermostat** and restart Home Assistant.
3. **Settings > Devices & services > Add integration > Cloudless Multiroom Thermostat.**

## Set up

**The heating system** (one per heat source):

1. A name and the **heat source type**:
   - **On/off switch**: a relay boiler. It turns on when a room is the hysteresis (default 0.3 °C) below its target and off when every room reached its target.
   - **Climate entity**: an ESPHome or DIYLess OpenTherm thermostat, or any thermostat with a setpoint. It receives the target of the room that needs heat most.
   - **OpenTherm Gateway (OTGW)**: the OTGW climate entity, which receives that same target.
2. The heat source entity and, optionally, a **central heating enable** switch that is on while any room is set to heat.
3. Settings: the **off temperature** sent when nothing needs heat (default 10 °C), an optional **presence** entity with what happens when everybody is away (rooms off, or lowered to eco), and how often room temperatures are resent to TRV inputs.

**Rooms**: on the integration page, **Add room**, in two steps:

1. Name, eco and warm temperature, the overshoot buffer (default 1.0 °C) and the target range and step.
2. The room temperature sensor, the TRVs (none is fine: the room then only adds heat demand), the TRVs' external temperature inputs (for example Sonoff TRVZB or Shelly BLU TRV), and window or door sensors with their open and close delays.

Add as many rooms as you like; the pencil next to a room edits it. To put a room in an area, set the area on the room's device page. Settings of the heating system itself are under **Configure**.

Scheduling is left to you: an automation or a scheduler card that sets presets or temperatures on the room climates.

## What it creates

- **A climate entity per room**: Heat/Off, target temperature, presets **Eco** and **Warm** (setting a temperature yourself switches to **Manual**). Attributes show its role (`winner`, `passenger`, `calls_for_heat`, `reached`, `paused`, `off`) and why it is paused. Mode, preset and target survive a restart.
- **Winning room**: the room driving the heat source, or Off, with its target, temperature and gap as attributes.
- **Winning room temperature**: that room's temperature, for a firmware PID that needs it as its process value (see [`thermostat.yaml`](#esphome-opentherm-thermostat-thermostatyaml)).
- Per room: **Paused** (window, away or manual, with the reason) and, with window sensors, a debounced **Window** sensor.
- **Delta temperature (legacy)**: disabled by default; the winner as one JSON state (`area`, `target`, `curr`, `delta`) for dashboards that expect that format.
- Services: `pause_room` and `resume_room` (hold a room off and release it), `set_all_off`.

## How it works

A boiler has only **one** flow temperature, while every room wants its own. Two tiers solve that without any room coordination:

**Tier 1, the heat source:** of all rooms set to heat, the one with the biggest gap between target and temperature is the **winner**. Its target goes to the heat source; with an ESPHome PID thermostat the boiler then modulates its flow temperature to bring exactly that room to its target. When no room has a real gap, the heat source gets the off temperature.

**Tier 2, the valves:** every room's TRVs get the room's target and regulate their own valve. Home Assistant never commands valve positions: as long as the water arriving is warm enough for a room's own target, its TRVs stay open and the room heats along (a **passenger**). When a room reaches its target, its TRVs close by themselves. Only a room that wants more than the current winner waits, until it becomes the winner itself, which happens as soon as its gap is the largest.

**The overshoot buffer (winner only):** the winning room's TRVs get their target plus the buffer (default 1 °C); passengers get their exact target. Two controllers watch the winning room at once: the boiler's PID, which eases off its modulation gradually, and that room's TRV. Without headroom the TRV starts closing while the boiler is still pushing hot water, a fight that wastes heat and can trip a low-flow fault. A passenger's TRV can't get into that fight, because the boiler isn't modulating for it. The buffer moves with the winner.

**One temperature per room:** the room sensor's reading is also pushed to the TRVs' external temperature inputs, with a heartbeat (some TRVs, like the Sonoff TRVZB, fall back to their own sensor after 2 hours without an update). A TRV's own sensor sits on the radiator and reads warm; this way the room climate, the winner choice and the TRV all work from the same number. TRV writes are skipped when nothing changed, to save batteries.

**Windows and presence:** an open window (after its open delay) holds its room off and sets its TRVs to the off temperature; when it closes, the room returns to its own mode and target. When everybody is away, rooms go off or to eco; they return when someone is home. Window has priority over a manual pause, which has priority over away.

## Experimental features

Optional, observe-only diagnostics: warm-up episodes with PID advice, a night cooldown model that predicts the morning, gas per degree-day with the hot water split off, and an evening report. Switch them on under **Configure > Enable experimental features**. What each one does, what it needs and how mature it is: **[docs/experimental.md](docs/experimental.md)**.

## Dashboard: Heating Control Card

**[BarBaar44/Heating-Control-Card](https://github.com/BarBaar44/Heating-Control-Card)** is a Lovelace card for this system, installed through HACS (custom repository, type Dashboard). It shows every room with its temperature, target, Heat/Off and role, highlights the room the boiler heats for, and shows the boiler state: heating, pumping or hot water, modulation, faults and pressure warnings.

![Heating Control Card](https://raw.githubusercontent.com/BarBaar44/Heating-Control-Card/main/shot.png)

<img src="https://raw.githubusercontent.com/BarBaar44/Heating-Control-Card/main/multi-room-card.png" alt="Heating Control Card with several rooms" width="412">

The card's README lists its options, including how to pick the rooms and the boiler entities.

## Example setup

The hardware this was built and tested on. None of it is required: any TRV and temperature sensor Home Assistant can see will do.

| Component | Choice | Why |
|---|---|---|
| Boiler | Atag E325ec (2015) | Supports OpenTherm out of the box |
| Thermostat | [DIYLess OpenTherm thermostat](https://diyless.com/product/opentherm-thermostat) with custom ESPHome ([`thermostat.yaml`](thermostat.yaml)) | Full local control over the PID loop and the OpenTherm bus |
| TRVs | [Sonoff TRVZB](https://sonoff.tech/en-nl/products/sonoff-zigbee-thermostatic-radiator-valve) and [Shelly BLU TRV](https://www.shelly.com/products/shelly-blu-trv-single-pack) | Sonoff where it fits; Shelly where space is tight |
| Room sensors | Xiaomi/Aqara [LYWSD03MMC](https://www.zigbee2mqtt.io/devices/LYWSD03MMC-z.html), reflashed with Zigbee firmware | Cheap, tiny, battery powered, on the same local Zigbee mesh as the TRVs |
| Radiator booster fans | Custom "DBE" ESPHome boards ([BarBaar44/ESPHome/DBE](https://github.com/BarBaar44/ESPHome/tree/main/DBE)) | Push air through the radiator fins to get more heat out of the same water temperature |

**TRVs compared:**

| | Sonoff TRVZB | Shelly BLU TRV |
|---|---|---|
| Size | Bulkier | Small, about the size of a Tado TRV |
| Cost | Cheaper | More expensive |
| Connectivity | Fully local via Zigbee2MQTT | Bluetooth, needs a BLU gateway |
| External temperature input | Yes | Yes |

Sonoff is the better choice where it fits: cheaper, fully local and well supported.

**Room sensors:** the LYWSD03MMC speaks Bluetooth with its stock firmware. Reflashed with Zigbee firmware ([instructions on the Zigbee2MQTT page](https://www.zigbee2mqtt.io/devices/LYWSD03MMC-z.html)) it joins the same Zigbee network as the TRVs, so no Bluetooth proxies are needed per room.

**Booster fans:** each radiator has a fan board that measures flow and return temperature and blows air across the fins while the radiator still gives off heat. They work independently of the integration: they make the heat a radiator receives reach the room faster, whichever room is the winner. Hardware files are in [BarBaar44/EasyEDA](https://github.com/BarBaar44/EasyEDA).

## ESPHome OpenTherm thermostat (`thermostat.yaml`)

[`thermostat.yaml`](thermostat.yaml) is the ESPHome config for the DIYLess OpenTherm thermostat, from `opentherm:` on. Use it with the **Climate entity** heat source: the integration sets the PID climate's target to the winner's target, and the firmware reads the winner's temperature as its process value.

- **`opentherm:`** OpenTherm master on GPIO 21 (in) and 22 (out), central heating enabled. The hot water enable flag is sent as off; this combi boiler still makes hot water on demand, at the setpoint set on its own panel.
- **`output: t_set`** the boiler's target flow temperature, written by the PID (`zero_means_zero`, so 0 means boiler off).
- **`climate: platform: pid`** (`main_climate`, "Central heating") the control loop. Its room temperature comes from Home Assistant: point it at the integration's **Winning room temperature** sensor (the example uses `sensor.virtual_heating_temperature`; rename the sensor to that, or change the ID). A 1 second heartbeat filter keeps the PID fed when the value doesn't change. Starting values `kp: 0.7`, `ki: 0.003`.
- **`sensor`, `binary_sensor`, `switch`** the OpenTherm telemetry this boiler supports: modulation, pressure, flow, return and hot water temperatures, faults, flame, pump, and the central heating, hot water and outside temperature compensation switches.

**Runtime-adjustable PID values.** "Central heating Kp / Ki / Kd" are number entities that apply to `main_climate` on every change and at boot, so values set in Home Assistant survive a reboot. "Central heating PID Result / Error / Proportional / Integral / Derivative" sensors show why the loop does what it does (ESPHome reports them x100). `min_integral: 0`: the loop only heats, so a negative integral is never useful and would otherwise have to unwind every morning. The experimental [PID advice](docs/experimental.md#warm-up-episodes-and-pid-advice) works with these numbers.

### Never write the hot water setpoint (Atag lockout C1 51)

> **Do not let the thermostat write the hot water setpoint, OpenTherm ID 56.** On this Atag it locks the boiler out with **C1 51** (no heating and no hot water) until the writes stop. Reading ID 56 is fine. Set the hot water temperature on the boiler's own panel.

**What it looks like:** the display shows **C1 51**, which Atag fault lists describe as a fan fault. In Home Assistant "Fault indication" turns on, the flame stays off on every demand, and a reset clears it for only a few seconds. The fan itself is fine: with the thermostat disconnected the boiler works normally.

**C1 05 is not a fault:** after a power cycle or reset the display shows **C1 05** (or C0 05) for about 7 minutes. That is the automatic air removal program; the fault bit can stay on until it finishes.

**If C1 51 appears:**

1. Check what the thermostat *writes* to the boiler. The ESPHome log shows every message (`WRITE_DATA; id: ...`); with `web_server:` enabled it can be followed at `http://<thermostat-ip>/events`.
2. Disconnect the thermostat, reset the boiler and open a hot water tap. No fault means the boiler is fine and something the thermostat sends is the trigger; remove recently added writes first.
3. Only if C1 51 also returns with nothing connected is it the fan, its wiring or the control board: a job for the installer.

## Development and test environment

- [`dev/`](dev/README.md): a simulated house (rooms that warm up and cool down, TRVs, a boiler, gas meter and weather) to try everything without hardware, as a Docker container or a VM for Virtual Machine Manager, with a script that sets it all up through the real forms.
- `tests/`: tests on a real Home Assistant core (`pip install -r requirements_test.txt`, then `pytest`). CI also runs ruff, shellcheck, hassfest and the HACS validation.
- [CHANGELOG.md](CHANGELOG.md) lists what changed per release.
