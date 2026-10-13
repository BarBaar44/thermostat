# 🌡️ Thermostat - A Cloud-Free, Multi-Room Home Assistant Heating System

A local, OpenTherm-based central heating setup built entirely on Home Assistant and ESPHome. One "virtual thermostat" per room drives both the physical TRVs and the boiler itself, with automations handling daily scheduling, passive-solar assist, and open-window detection.

## Goals

- **No cloud dependency** - everything runs locally through Home Assistant, ESPHome, and OpenTherm; no third-party heating app or account required.
- **Multi-room support** - every room/zone gets its own independent setpoint.
- **Fully customizable** - built to fit my own house and habits, not a fixed vendor product.
- **Auto on/off based on presence** - heating starts and stops automatically depending on whether anybody is home.
- **Nice to have: open window detection** - heating pauses automatically if a door/window is left open.

## Table of Contents

- [Hardware](#hardware)
  - [Boiler](#boiler)
  - [Thermostat](#thermostat)
  - [Thermostatic Radiator Valves (TRVs)](#thermostatic-radiator-valves-trvs)
  - [Room Temperature Sensors](#room-temperature-sensors)
  - [Radiator Booster Fans (DBE)](#radiator-booster-fans-dbe)
- [System Overview](#system-overview)
  - [How Multiple Rooms Heat at Once](#how-multiple-rooms-heat-at-once-the-two-tier-control-model)
  - [The +1°C Overshoot Buffer (Winning Room Only)](#the-1c-overshoot-buffer-winning-room-only)
  - [Rooms / Zones Covered](#rooms--zones-covered)
- [ESPHome Thermostat Firmware (`thermostat.yaml`)](#esphome-thermostat-firmware-thermostatyaml)
  - [PID autotune (removed)](#pid-autotune-removed)
  - [Runtime-adjustable PID parameters](#runtime-adjustable-pid-parameters)
  - [Boiler lockout C1 51: never write the hot water setpoint](#boiler-lockout-c1-51-never-write-the-hot-water-setpoint)
- [Automations](#automations)
  - [TRV Setpoint Sync](#automationstrv_setpoint_syncyaml---trv-setpoint-sync)
  - [Heating Schedule](#automationsheating_scheduleyaml---heating-schedule)
  - [Garden Door Heating Pause](#automationsgarden_door_heating_pauseyaml---garden-door-heating-pause)
  - [Shared Scripts](#scriptspause_livingroom_heatingyaml-and-scriptsresume_livingroom_heatingyaml)
  - [Helper Entities Used](#helper-entities-used-across-all-three-automations)
- [Delta Temperature Sensor](#delta-temperature-sensor-sensorsdelta_temperatureyaml)
- [Experimental: Self-Tuning Heating and Night Cooldown Profiling](#experimental-self-tuning-heating-and-night-cooldown-profiling)

---

# Hardware

| Component | Choice | Why |
|---|---|---|
| Boiler | Atag E325ec (2015) | Already installed; supports the OpenTherm protocol out of the box |
| Thermostat | DIYLess OpenTherm thermostat, reflashed with custom ESPHome | Full local control over the PID loop and OpenTherm bus, instead of relying on the stock firmware |
| TRVs (radiators) | Sonoff TRVZB + Shelly TRV Blu (mixed) | Sonoff where it physically fits; Shelly where space is tight |
| Room temperature sensors | Xiaomi/Aqara [LYWSD03MMC](https://www.zigbee2mqtt.io/devices/LYWSD03MMC-z.html), reflashed with custom Zigbee firmware | Cheap, tiny, battery-powered - and made fully local by replacing the stock Bluetooth/Xiaomi firmware with a Zigbee one |
| Radiator booster fans | Custom "DBE" ESPHome + relay-switched fan driver board, per-radiator | Forces convective airflow across radiator fins to extract more heat per °C of water temperature - see [Radiator Booster Fans](#radiator-booster-fans-dbe) below |

## Boiler

**Atag E325ec (2015).** This boiler natively supports the **OpenTherm** protocol, which is what makes this whole local, cloud-free approach possible in the first place - OpenTherm gives direct, real-time read/write access to setpoints and boiler telemetry over a simple two-wire bus, no vendor cloud service required.

## Thermostat

**DIYLess OpenTherm thermostat**, running a fully custom ESPHome config: [`thermostat.yaml`](thermostat.yaml).

The DIYLess thermostat ships with its own stock firmware that technically works out of the box. I flashed ESPHome onto it anyway, because I wanted full control over the OpenTherm bus and the PID logic driving it, rather than being limited to whatever the stock firmware exposes.

Since I already had a separate OpenTherm Gateway in place beforehand, I already knew exactly which telemetry sensors my specific boiler supports - so the ESPHome sensor config here is deliberately trimmed down to just those, rather than a generic "expose everything" config.

By far the hardest part of getting this dialed in was tuning the **PID controller**. ESPHome ships with an autotune function for this, and I ran it once to get a starting point - but the resulting parameters never actually got the boiler up to the real target temperature. After a fair bit of digging to understand what each PID parameter actually does, I ended up asking ChatGPT for help, which pointed me in the right direction. (Genuinely useful AI, for once.)

The values I landed on are `kp: 0.7`, `ki: 0.003` (see the `climate: - platform: pid` section of [`thermostat.yaml`](thermostat.yaml)), driving the OpenTherm `t_set` output. Room temperature is fed in from Home Assistant (`sensor.virtual_heating_temperature`) with a 1-second heartbeat filter, so the PID controller gets frequent updates even when the underlying sensor value hasn't changed.

Those values are now only the starting point: they can be changed from Home Assistant without reflashing (see [Runtime-adjustable PID parameters](#runtime-adjustable-pid-parameters)), and an experimental heating tuner in Home Assistant watches every warm-up to work out whether they should change (see [Experimental: self-tuning heating](#experimental-self-tuning-heating-and-night-cooldown-profiling)).

## Thermostatic Radiator Valves (TRVs)

I use two different TRV models across the house, purely because of space constraints - not every radiator had room for the Sonoff's bulkier housing, so a couple of rooms fell back to the smaller (but pricier) Shelly:

| | Sonoff TRVZB | Shelly TRV Blu |
|---|---|---|
| **Size** | A bit bulkier | Small - about the same footprint as a Tado TRV |
| **Cost** | Cheaper | More expensive |
| **Connectivity** | Fully local via Zigbee2MQTT | Bluetooth only - needs a BLU-compatible stick/gateway for HA to reach it |
| **HA integration** | Native Zigbee2MQTT support | Requires the separate Shelly integration |
| **Control granularity** | Can control valve opening, but not straightforward via Zigbee2MQTT | Very limited control |
| **Custom firmware** | N/A | Possibly? (unconfirmed) |

In short: Sonoff is the better choice when it fits - cheaper, fully local, and better supported. Shelly is the fallback for tight spaces, at the cost of being smaller-but-more-limited and dependent on Shelly's own integration and Bluetooth stick.

## Room Temperature Sensors

Each room's actual current temperature (the `current_temperature` fed into its virtual thermostat, and ultimately into the Delta Temperature sensor and the ESPHome PID loop) comes from a Xiaomi/Aqara **[LYWSD03MMC](https://www.zigbee2mqtt.io/devices/LYWSD03MMC-z.html)** - a small, cheap, battery-powered temperature/humidity sensor.

Out of the box, this sensor only speaks Bluetooth Low Energy with Xiaomi's own (or a custom BLE) firmware, which would mean relying on a BLE gateway/proxy per room - not ideal for a fully local, single-mesh setup. Instead, I reflashed these with **custom Zigbee firmware**, so they join the same Zigbee2MQTT network as the TRVs and other Zigbee devices in the house - one consistent local mesh, no separate Bluetooth infrastructure needed just for temperature readings.

> **Flashing instructions:** the [Zigbee2MQTT device page linked above](https://www.zigbee2mqtt.io/devices/LYWSD03MMC-z.html) documents exactly how to reflash this sensor from its stock BLE firmware to the custom Zigbee firmware - that's the reference I followed, so I won't duplicate the steps here.

I also push this same LYWSD03MMC reading into each room's TRV(s) as their external/room temperature input, instead of letting each TRV rely on its own onboard sensor. A TRV's onboard sensor sits directly on/against the radiator body, so left to its own devices it reads noticeably warmer than the room's actual air temperature - not representative of how the room actually feels. By feeding the TRV the exact same reading that already drives its room's virtual thermostat (and the Delta Temperature sensor), every device involved in a given room - the virtual thermostat, the Delta Temperature calculation, and the TRV's own local control loop - is working off one single, consistent number, instead of each device quietly disagreeing about what "the room's temperature" actually is.

## Radiator Booster Fans (DBE)

Beyond the TRVs, each radiator also has a temperature-differential-driven **booster fan**, custom-built and documented in a separate repo:

➡️ **[BarBaar44/ESPHome/DBE](https://github.com/BarBaar44/ESPHome/tree/main/DBE)** - ESPHome firmware, full write-up of the fan control logic (flow/return ΔT sensing, hysteresis, exponential speed curve, AUTO/MANUAL modes), and the full hardware design story (a **relay-switched** fan power driver - not MOSFET-based, despite the board's "DBE MosFet" naming, which is kept for historical continuity). Hardware design files (schematic/PCB/Gerbers) are in [BarBaar44/EasyEDA](https://github.com/BarBaar44/EasyEDA).

In short: a radiator heats the room by warming the air that passively convects past its fins, which is relatively slow. Each DBE unit uses two Dallas temperature sensors on the radiator's flow and return pipes to detect when there's still meaningful heat being given up by the water passing through, and drives a PWM-controlled fan (with a relay as the hard on/off power cutoff, separate from PWM speed control) to force air across the radiator and pull that heat into the room faster - squeezing more usable heat out of the same water temperature the boiler is already producing, rather than needing the boiler to run hotter.

---

# System overview

The whole heating system is built around one idea: each room/zone gets its own **virtual thermostat** (`climate.virtual_thermostat_*`) that acts as the single source of truth for "what temperature does this room want to be". Nothing else in the system is a source of truth for desired temperature - not the physical TRVs, not the boiler. Everything downstream (physical TRVs, the boiler setpoint) is kept in sync *with* the virtual thermostats, never the other way around.

This is split into three automations, two shared scripts, and one template sensor, on top of the ESPHome thermostat firmware itself - plus an experimental, observe-only pyscript app (see [the last section](#experimental-self-tuning-heating-and-night-cooldown-profiling)):

| File | Type | Responsibility |
|---|---|---|
| `thermostat.yaml` | ESPHome config | The physical OpenTherm thermostat device firmware - PID controller driving the boiler's `t_set`, plus all OpenTherm sensors/switches/binary_sensors |
| `automations/heating_schedule.yaml` | Automation | Daily start/stop scheduling + passive-solar "sun assist" logic |
| `automations/trv_setpoint_sync.yaml` | Automation | Keeps physical TRVs + the boiler in sync with the virtual thermostats |
| `automations/garden_door_heating_pause.yaml` | Automation | Open-window/door detection for the living room garden doors |
| `scripts/pause_livingroom_heating.yaml` | Script | Snapshot + pause living room heating (shared by two triggers) |
| `scripts/resume_livingroom_heating.yaml` | Script | Restore living room heating from snapshot |
| `sensors/delta_temperature.yaml` | Template sensor | Decides the single boiler setpoint across all rooms |
| `pyscript/apps/heating_tuner.py` + `pyscript/modules/` | Pyscript app, experimental | Records every warm-up and every night, recommends PID changes and predicts the morning - observe only |

> **History note:** this used to be one large automation, `housewarming.yaml`, with all of the above logic inline. It was split up for better isolation (a bug in door detection can no longer break the daily schedule) and so each concern gets its own trace history in Home Assistant. `housewarming.yaml` and the original `deltaTemperature` file are both kept in the repo only as deprecated historical references - see the deprecation notice at the top of each file. If you're setting this up fresh, use the files in `automations/`, `scripts/`, and `sensors/` instead.

## How multiple rooms heat at once (the two-tier control model)

A single boiler only has **one** flow water temperature (`t_set`) - it cannot natively know or care that "the office wants 20°C while the sports room wants 16°C" at the same time. This system resolves that with two independent tiers that work together:

**Tier 1 - the boiler's flow temperature (one value, house-wide):**
The `sensor.delta_temperature` helper looks at every room currently calling for heat and picks **whichever room has the biggest unmet demand** (the largest `target - current` gap) - see the [Delta Temperature Sensor](#delta-temperature-sensor-sensorsdelta_temperatureyaml) section below for the full rule set. That single "winning" room's target is what gets sent to the boiler as its flow water temperature. In other words: **the boiler is only ever tuned to satisfy the room that needs heat the most.**

**Tier 2 - each room's TRV self-regulates its own valve:**
It's important to be precise about what Home Assistant actually controls here, because it's *not* direct valve control. What `automations/trv_setpoint_sync.yaml` does is send each room's TRV(s) a **target temperature** (the room's virtual thermostat setpoint, plus a small overshoot buffer for the room currently driving the boiler - see below) - nothing more. **I don't have any way to directly command a TRV's valve position from Home Assistant.** Every TRV listed in this repo has its own local closed-loop control logic built into the device itself: it continuously compares a room temperature reading against the target it was last given, and opens or closes its valve accordingly, entirely on its own, without needing to hear from Home Assistant again until the target changes. As covered in [Room Temperature Sensors](#room-temperature-sensors) above, that comparison uses the same externally-pushed LYWSD03MMC reading the virtual thermostat itself uses - not the TRV's own onboard sensor - so the TRV's decision-making is working from the same accurate number as everything else in the system.

So the actual flow is: **virtual thermostat → decides room demand (drives Tier 1) and sends a target to the TRV (initiates Tier 2) → the TRV's own firmware takes it from there.** As long as the boiler is producing water hot enough to physically reach that TRV's target, the TRV will keep its valve open and let heat through. Once the room reaches its target, the TRV's own internal logic closes the valve by itself to stop the flow - Home Assistant isn't polling it every few seconds to tell it to close; the TRV decides that autonomously, the same way it would if it were paired with any other thermostat.

**Putting it together:** say the Office has the highest demand (biggest gap between target and current temperature) and "wins" Tier 1 - its target becomes the boiler's flow temperature. Because the boiler is now producing water hot enough for the Office, every *other* room whose own target is at or below that same flow temperature can **also** heat at the same time - their TRVs simply keep their own valves open, since the water arriving at their radiator is already warm enough to reach their own target, and each TRV is independently deciding this for itself. As soon as a room reaches its own target, that room's TRV closes its valve on its own initiative, regardless of what any other room is doing. Only a room asking for a *higher* temperature than the current Tier 1 winner would be under-served until it takes over as the new winner (which happens automatically: `sensor.delta_temperature` recalculates as soon as any virtual thermostat changes).

This is precisely why the system doesn't need to explicitly coordinate "who gets to heat now": the boiler tier picks a flow temperature high enough for the neediest room, and every TRV independently self-regulates against its own last-given target, opening or closing its own valve without any further instruction from Home Assistant.

The [radiator booster fans](#radiator-booster-fans-dbe) described above operate as an independent layer on top of this: they don't influence which room "wins" Tier 1 or when a TRV opens/closes in Tier 2 - they simply make whichever heat a radiator *is* currently receiving transfer into the room faster, regardless of which tier decided to send that heat there.

## The +1°C overshoot buffer (winning room only)

Only the room that is currently driving the boiler - the Tier 1 winner, i.e. the `area` reported by `sensor.delta_temperature` - gets its TRV target set **1°C above** its virtual thermostat setpoint. Every other room that heats along at the same time (a "passenger" room) gets its **exact** target. Earlier versions gave every TRV the +1°C, which let passenger rooms overshoot their own target by up to 1°C for no reason.

### Why the winning room needs it

The fight this buffer prevents isn't between the boiler and *other* rooms' TRVs - it's between the boiler's **own PID loop** and the TRV **in the same room that is currently driving the boiler**. Two independent controllers end up watching the same room at the same time:

1. **The boiler's own ESPHome PID loop** (`thermostat.yaml`'s `climate: - platform: pid`), which reads that winning room's current temperature and continuously adjusts the boiler's flow water temperature (`t_set`) to bring that specific room up to its target. This gradual adjustment of flow temperature is the boiler **modulating**: rather than switching abruptly between fully on and fully off, the boiler continuously varies its output to approach the target smoothly.
2. **That same room's physical TRV**, running its own local control logic off the same room temperature reading, deciding independently when to start closing its own valve.

Because these are two separate, uncoordinated controllers reacting to the same room, they can easily get out of step: the boiler's PID is deliberately gentle (see [Runtime-adjustable PID parameters](#runtime-adjustable-pid-parameters)), so it keeps modulating - producing hot flow water for a while - as it gradually approaches the target. If the TRV in that same room starts throttling its own valve closed slightly *before* the boiler has eased off its modulation, the boiler is left pushing hot water into a system with reduced flow - the classic "fight": the boiler pumping more hot water in, the TRV independently restricting it back out. Beyond being inefficient, this can trip a low-flow or pressure fault on the boiler itself.

Giving the *winning* room's TRV a target 1°C above what the boiler is aiming for gives that TRV enough headroom that it won't start closing until after the boiler's modulation has already had a chance to ease off naturally - removing the timing conflict between the two independent controllers. A passenger room's TRV can't get into this fight, because the boiler isn't modulating for that room, so it doesn't need the buffer.

### How the buffer moves between rooms

`automations/trv_setpoint_sync.yaml` (the same as the version running in my Home Assistant) handles this:

- **Setpoint change** on a virtual thermostat: only that room's TRVs are resynced, with the buffer if that room is the current winner.
- **Winner change** (`sensor.delta_temperature`'s `area` changes to a real room): every room is resynced, so the old winner drops to its exact target and the new winner gets the buffer. Rooms are matched by comparing `area_name()` of each virtual thermostat with the sensor's `area`, which is the same function the sensor uses, so no area names are hardcoded.
- **Winner goes to `Off`** (no room has genuine demand): nothing is resynced. The last winner deliberately keeps its buffer, because that is exactly the moment the boiler is still winding down its modulation, which is the fight the buffer exists to prevent.
- A TRV is only written to when its target actually differs, to avoid needless radio traffic and battery drain on the TRVs.

Trade-offs worth knowing:
- A room that becomes the new winner through its own setpoint change first gets its exact target, then the buffer a moment later, when the sensor's new `area` triggers the winner-change resync.
- If two rooms have nearly equal demand, the winner can alternate between them, and each switch moves both rooms' TRV targets by 1°C. If that turns out to be noisy in practice, add hysteresis to the winner selection in `sensors/delta_temperature.yaml`.
- The automation triggers on every `sensor.delta_temperature` state change; the condition stops runs where the winning room did not change, but those runs still appear in the automation's trace history.
- The hallway TRV is unaffected: it is still derived from the living room setpoint (minus 8°C, minimum 5°C) without any buffer.
- The experimental heating tuner (last section) never changes this buffer.

### Rooms / zones covered
- Living room (`climate.virtual_thermostat_living_room`) → TRVs: kitchen, living room front, living room rear (+ derives the hallway TRV setpoint)
- Gijs' room (`climate.virtual_thermostat_room_gijs`) → `climate.trv_room_gijs`
- Tim's room (`climate.virtual_thermostat_room_tim`) → `climate.trv_room_tim`
- Sports room (`climate.virtual_thermostat_sports_room`) → `climate.trv_sports_room`
- Office (`climate.virtual_thermostat_office`) → `climate.trv_office`
- Master bedroom (`climate.virtual_thermostat_master_bedroom`) → 2x Shelly BLU TRV
- Hallway (`climate.trv_hallway`) has no virtual thermostat of its own; it's derived from the living room setpoint (see below)

> **Naming tip if you're copying this approach:** name every virtual thermostat with a consistent prefix (here, `climate.virtual_thermostat_*`). Both `trv_setpoint_sync.yaml`'s trigger list and `delta_temperature.yaml`'s `startswith('climate.virtual_thermostat_')` filter rely on that consistent prefix to find all rooms automatically. This repo previously had one room (`climate.thermostat_room_tim`) that didn't follow the convention, which caused it to be silently invisible to the Delta Temperature sensor's room-selection logic until it was renamed to `climate.virtual_thermostat_room_tim`.

# ESPHome thermostat firmware (`thermostat.yaml`)

This is the actual device config flashed to the DIYLess OpenTherm thermostat. It's the physical bridge between Home Assistant and the boiler over the OpenTherm bus:

- **`opentherm:`** - configures the OpenTherm master on GPIO pins 21 (in) / 22 (out), with central heating enabled and CH2 active. The DHW-enable flag is sent as off (`dhw_enable: false`); this combi boiler still makes hot water on demand with the flag off, at the setpoint set on the boiler's own panel (see below).
- **`output: - platform: opentherm` (`t_set`)** - the boiler's target flow water temperature, written by the PID controller below (min 0, auto max, `zero_means_zero` so 0 genuinely means "boiler off" rather than "no change").
- **`climate: - platform: pid` (`main_climate`, "Central heating")** - the actual PID control loop. Takes the room temperature (`ch_room_temperature`, fed from `sensor.virtual_heating_temperature` in Home Assistant) and drives `t_set` to reach the target. Tuned to `kp: 0.7`, `ki: 0.003` after ESPHome's autotune function alone wasn't sufficient (see the [Thermostat](#thermostat) section above for the story on that).
- **`sensor: - platform: opentherm`** - exposes all the OpenTherm boiler telemetry this specific boiler (Atag E325ec) supports: modulation level, CH water pressure, boiler/DHW/outside/return water temperatures, and the various setpoint bounds.
- **`binary_sensor: - platform: opentherm`** - boiler fault/diagnostic flags, pump control, DHW presence, and setpoint transfer capability flags.
- **`switch: - platform: opentherm`** - the three boiler-side toggles: CH enable, DHW enable, outside temperature compensation.
- **No writable hot water setpoint** - the thermostat only *reads* the hot water setpoint (OpenTherm ID 56, the "Domestic hot water temperature setpoint" sensor); it is set to **50 °C** on the boiler's own panel. A writable "Hot water setpoint" number (`number: - platform: opentherm`, `t_dhw_set`) was tried, but writing ID 56 makes this ATAG lock out with **C1 51** - see [Boiler lockout C1 51](#boiler-lockout-c1-51-never-write-the-hot-water-setpoint). The boiler is a combi without a storage tank, so there is no Legionella reason to keep it at the boiler's former 65 °C; lower means less heat loss in the pipes and less limescale.

## PID autotune (removed)

The firmware used to carry a second PID climate (`pid_climate`, "PID Climate Controller") just for ESPHome's relay autotune. While both climates wrote `heat_output: t_set`, the idle one (all gains `0.0`) kept writing `0` - "boiler off" with `zero_means_zero` - so the CH-active flag flipped on/off roughly every 30 seconds and the radiators stayed cold. That was first fixed by gating the two climates through template outputs and a "PID Autotune Mode" switch.

Autotune has since been removed altogether: its relay test suits fast, simple loops rather than a slow house with TRVs, its result had to be copied in by hand, and a heating tuner in Home Assistant is taking over that job: it judges `main_climate` on real warm-ups and works through the adjustable Kp/Ki/Kd below. `main_climate` drives `t_set` directly again.

## Runtime-adjustable PID parameters

`main_climate`'s gains can be changed from Home Assistant without reflashing - the first step towards Home Assistant tuning the heating itself:

- **"Central heating Kp / Ki / Kd"** - template `number` entities (`entity_category: config`, `restore_value: true`). They are applied to `main_climate` with `climate.pid.set_control_parameters` on every change and once at boot (`on_boot`, priority -100), so a value set in Home Assistant survives a reboot. The `control_parameters` in the climate block (`kp: 0.7`, `ki: 0.003`) are only the starting values. Changing Ki causes no output jump: ESPHome stores the integral with Ki already applied, so a new Ki only affects what accumulates afterwards.
- **"Central heating PID Result / Error / Proportional / Integral / Derivative"** - `platform: pid` sensors on `main_climate`, so you can see *why* the loop does what it does (e.g. an integral that has wound up). ESPHome reports all of these ×100 (an Error of `-90` means the room is 0.9 °C above target).
- **"Outdoor Temperature From Home Assistant"** - `sensor.outdoor_temperature`, pulled in for logging now and for a weather-compensated heating curve later.
- **`min_integral: 0`** - the loop only heats, so a negative integral is never useful: it used to sink to the default floor of -1 overnight while the room sat above target, and every morning's warm-up first had to unwind it. `max_integral` stays at the default 1 so cold days can still build up enough output. These limits can't be changed at runtime (`set_control_parameters` only covers Kp/Ki/Kd).

This firmware is what the rest of the repo (the automations, scripts, and Delta Temperature sensor) ultimately feeds - all of `climate.thermostat_central_heating`'s setpoint changes from `automations/trv_setpoint_sync.yaml` flow down into this device's PID loop, which then talks OpenTherm to the actual boiler. This is the Tier 1 half of the two-tier model described above - the PID loop's whole job is to get the boiler's actual flow water temperature to match whatever the "winning" room currently demands, by modulating output smoothly rather than switching abruptly on/off. This same modulating PID loop is also the other half of the boiler-vs-TRV fight described in [The +1°C overshoot buffer](#the-1c-overshoot-buffer-winning-room-only) above.

## Boiler lockout C1 51: never write the hot water setpoint

> **Do not let the thermostat write the hot water (DHW) setpoint, OpenTherm ID 56.** On this Atag it locks the boiler out with **C1 51** - no heating and no hot water - until the writes stop. Reading ID 56 is fine. Set the hot water temperature on the boiler's own panel.

**What it looks like:** the display shows **C1 51**, which Atag fault lists describe as a fan fault (the fan doesn't reach or hold its speed). In Home Assistant the "Fault indication" binary sensor turns on, the flame stays off on every heating or hot water demand, and a reset clears the fault for only a few seconds before it comes back. It can start while the boiler is idle.

**What it is not:** despite the code, the fan was fine. With the thermostat disconnected the boiler made hot water without any fault. Water pressure and temperatures were normal, and the OpenTherm traffic itself was clean (no timeouts, parity errors or invalid frames).

**The cause (October 2026):** a writable "Hot water setpoint" number was added to `thermostat.yaml`, so the thermostat wrote ID 56 to the boiler every few seconds. C1 51 appeared the next day and came back immediately after every reset - with the thermostat connected directly *and* through an OpenTherm Gateway (OTGW). The gateway's `UI=56` command did not stop the writes either. Removing the number (the thermostat then only reads ID 56) cleared it: after one reset the fault stayed off and the burner lit normally. The same lockout had happened the year before and took days to resolve; that time putting an OTGW in line made it go away. Once the writes had stopped, the OTGW was removed again (13 October 2026): connected directly, the boiler ran its air-removal program and then lit normally on hot water, with no fault. The gateway is not needed - stopping the ID 56 writes was the fix.

**C1 05 is not a fault:** after a power cycle the display shows **C1 05** (or C0 05) for about 7 minutes. That is the automatic air-removal program, even though the "Fault indication" sensor stays on while it runs.

**If C1 51 comes back:**

1. Check what the thermostat *writes* to the boiler before suspecting the fan. The ESPHome log shows every message (`WRITE_DATA; id: ...`); with `web_server:` enabled it can be followed at `http://<thermostat-ip>/events`.
2. Disconnect the thermostat (or the OpenTherm wires at the boiler), reset the boiler and open a hot water tap. No fault means the boiler is fine and something the thermostat sends is the trigger; remove recently added *writes* first.
3. Only if C1 51 also returns with nothing connected is it the fan, its wiring or the control board - a job for the installer.

**After a reset the display shows C1 05 for about 7 minutes.** That is the automatic air-removal program, not a fault; the OpenTherm fault bit can stay on until it finishes, so judge the result only after C1 05 is done.

# Automations

## `automations/trv_setpoint_sync.yaml` - TRV Setpoint Sync

Triggered whenever the `temperature` attribute of any virtual thermostat changes (**Setpoint change**), and whenever `sensor.delta_temperature` reports a different winning room (**Winner change**; updates where the winner stays the same or becomes `Off` are filtered out by the automation's condition). Rooms and their TRVs are listed once, in the `rooms` variable, and synced in a `repeat: for_each` loop. A setpoint change only syncs the room that fired; a winner change syncs every room. Each room is skipped when its `temperature` attribute `is none`, and each TRV is only written to when its current target differs from the new one. The **+1°C** overshoot buffer is only added for the room whose `area_name()` matches the sensor's current `area` - see [Why the winning room needs it](#why-the-winning-room-needs-it) for the reasoning and [How the buffer moves between rooms](#how-the-buffer-moves-between-rooms) for the exact rules.

This is the Tier 2 half of the two-tier model described in [System overview](#how-multiple-rooms-heat-at-once-the-two-tier-control-model) above: this automation only ever sends a **target temperature** to each room's TRV(s), based purely on that room's own virtual thermostat - it never tells a TRV to open or close directly. What the TRV does with that target (opening/closing its own valve to reach and hold it) is entirely up to the TRV's own onboard logic, completely independent of whichever room is currently winning the boiler's flow-temperature race.

This per-room gating was added after a production bug: the sequence used to unconditionally re-sync *all six* rooms on every single setpoint change, regardless of which thermostat actually fired the trigger. If any one of those six virtual thermostats was `off` (and therefore had no `temperature` attribute set), reading `None + 1` crashed the whole sequence - silently skipping every sync step listed after the crash point. Scoping each sync to its own trigger, with an explicit null-check, fixed this and also cut down on redundant service calls to unrelated TRVs. The winner-change path does resync all rooms again, but every room keeps its own null-check, so an `off` thermostat is simply skipped instead of breaking the loop.

**If you're copying this approach:** this pattern (gate every action with `trigger.entity_id == X and state_attr(X, ...) is not none`) is worth using any time a single trigger definition lists multiple entities but the following actions assume a specific one of them fired. Otherwise a `None` attribute on *any* listed entity can crash actions meant for a *different* entity entirely.

The hallway is a special case: since it has no thermostat of its own, its TRV setpoint is derived from the living room setpoint **minus 8°C** (with a floor of 5°C), so it stays comfortably lower than the living room instead of overheating. It's synced only on a living room setpoint change, since it depends on that value alone and never gets the buffer.

This automation also pushes the overall central heating setpoint to `climate.thermostat_central_heating` (the boiler), sourced from the `sensor.delta_temperature` helper (see below) - parsed with `from_json` to extract its `.target` field, with a safe `float(10)` fallback.

## `automations/heating_schedule.yaml` - Heating Schedule

**1. Start of day - weekdays (`Weekdays Start` @ 07:30) or someone arriving home (`Arriving Home`)**
Only runs while `input_boolean.somebody_home` is on and before 20:00. It checks today's daily weather forecast (`weather.forecast_home`):
- If it's **sunny/clear and mild (>12°C)**, it assumes passive solar heating will help, so it sets the living room to a lower 15°C, turns the boiler enabler switch on, and schedules a follow-up check in 1 hour via `input_datetime.sun_check_time` (flagged with `input_boolean.sun_assist_pending`).
- Otherwise, it goes straight to a normal 18°C heat setpoint and turns the boiler on.

**2. Start of day - weekend (`Weekend Start` @ 08:00)**
Same sunny/mild-vs-normal logic as above, just on the weekend schedule (only while somebody is home).

**3. Sun check (`Sun check`, dynamic time from `input_datetime.sun_check_time`)**
Follow-up to the "sunny and mild" branch above. If `input_boolean.sun_assist_pending` is still on, it checks the living room's actual current temperature. If it's still below 16°C (i.e. the sun didn't do enough), it bumps the living room up to a full 18°C heat setpoint and makes sure the boiler is enabled. Either way, `sun_assist_pending` is cleared afterwards.

**4. Stop heating (`Schedule stop` @ 20:00, or `Leaving Home`)**
Sets all managed climate entities (living room, master bedroom, Gijs' room, hallway, office, master bedroom BLU TRVs, Tim's room, sports room) to 10°C and `hvac_mode: off`, and turns off the boiler enabler switch. Also clears the `garden_heating_paused` and `sun_assist_pending` helper booleans so the state is clean for the next day.

## `automations/garden_door_heating_pause.yaml` - Garden Door Heating Pause

Open window/door detection for the living room garden doors. Calls the two shared scripts below instead of duplicating their logic inline.

**1. Garden doors open (`Garden doors open`, debounced 2 min)**
If the living room is actively heating when the garden doors have been open for 2+ minutes, calls `script.pause_livingroom_heating`.

**2. Garden doors closed, still within schedule (`Garden doors closed`, debounced 1 min, before 20:00)**
If heating was paused due to open doors, calls `script.resume_livingroom_heating`.

**3. Garden doors closed, after schedule stop (after 20:00)**
If the doors close after the 20:00 schedule stop already happened, it doesn't resume heating - it explicitly sets the living room to 10°C / off and clears the paused flag, so the room doesn't accidentally start heating again outside the schedule.

**4. Garden doors safety check (`time_pattern`, every 5 minutes)**
A self-healing safety net for step 1. Home Assistant's `state` trigger with `from`/`to` only fires on the *exact moment* of an `off → on` transition - if that transition is missed (e.g. Home Assistant restarts, or the automation/underlying template sensor reloads, while the garden doors are *already* open), step 1 never fires and the living room can stay heating indefinitely with the doors open. This trigger re-checks every 5 minutes: if the doors are currently open **and** the living room virtual thermostat is still in `heat`, it calls `script.pause_livingroom_heating` again, so the system self-corrects within 5 minutes regardless of *why* the original edge trigger was missed.

**If you're copying this approach:** any automation relying on a `from`/`to` state trigger to detect "X is currently true" (rather than "X just became true") is vulnerable to this same class of bug. A cheap `time_pattern` safety net that re-checks the level condition directly is a simple, general-purpose fix.

## `scripts/pause_livingroom_heating.yaml` and `scripts/resume_livingroom_heating.yaml`

Extracted from what used to be duplicated scene-snapshot/turn-off/turn-on sequences inline in two separate places (the doors-open branch and the safety-net branch would otherwise have identical logic copy-pasted). Now both relevant branches in `Garden Door Heating Pause` call the same scripts, so any future change to the pause/resume behaviour only needs to happen in one place.

- **`script.pause_livingroom_heating`**: writes a small JSON snapshot - the living room virtual thermostat's `hvac_mode` and target, plus the three living room TRV targets - to `input_text.livingroom_heating_snapshot`, turns the living room virtual thermostat off, and sets `input_boolean.garden_heating_paused` on.
- **`script.resume_livingroom_heating`**: restores the mode, target and TRV targets from that snapshot, clears the snapshot so a stale copy can't be reused, and turns `input_boolean.garden_heating_paused` off. If there is no usable snapshot (never taken, already cleared, or unreadable), it puts the living room back into `heat` at 18°C and raises a persistent notification, instead of silently leaving the room cold.

These used to snapshot into a scene with `scene.create`. A created scene only lives in memory, so a Home Assistant restart while the garden doors were open lost it, and there was nothing left to restore. A plain `input_text` survives restarts.

### Helper entities used (across all three automations)
- `input_boolean.somebody_home` - presence flag driving the daily start/stop logic
- `input_boolean.sun_assist_pending` - tracks whether a "sunny start" is awaiting its 1-hour follow-up check
- `input_boolean.garden_heating_paused` - tracks whether living room heating was paused due to open garden doors
- `input_text.livingroom_heating_snapshot` - JSON snapshot of the living room heating state while it is paused (see the scripts above)
- `input_datetime.sun_check_time` - dynamically scheduled time for the follow-up sun check
- `binary_sensor.backdoors_garden` - open/closed sensor for the garden doors (open window detection); combines `binary_sensor.door_sensor_garden_doors_contact` and `binary_sensor.door_sensor_kitchen_door_contact`
- `switch.main_thermostat_central_heating_enabled` - master enable/disable switch for the boiler
- `weather.forecast_home` - used to decide whether to rely on passive solar heating in the morning
- `sensor.delta_temperature` - see below; drives the central heating boiler setpoint

# Delta Temperature sensor (`sensors/delta_temperature.yaml`)

A template sensor helper (`sensor.delta_temperature`) that recalculates as soon as any virtual thermostat changes (state-based - it used to poll every 10 seconds with a `time_pattern` trigger; the file header explains why that was dropped) and acts as the "brain" deciding what setpoint the central heating boiler should actually be given, across a house with multiple independently-controlled rooms that a single-setpoint boiler cannot natively understand.

This sensor is **only** responsible for Tier 1 of the [two-tier control model](#how-multiple-rooms-heat-at-once-the-two-tier-control-model) described above - picking the boiler's flow temperature. It has no influence over which rooms actually receive heat; that's entirely down to each room's own TRV self-regulating against the target it was last given, in Tier 2. Its `area` field is also what `automations/trv_setpoint_sync.yaml` checks to decide which single room's TRV currently receives the +1°C buffer (see [above](#how-the-buffer-moves-between-rooms)).

### Why this is needed
A boiler only has one setpoint. But this system has many rooms, each with their own virtual thermostat, each potentially wanting a different temperature at any given moment. Something has to reduce "many rooms, many demands" down to "one number the boiler understands" - that's this sensor's entire job.

### How it decides
It scans every `climate.virtual_thermostat_*` entity currently in `heat` mode and computes `delta = target - current_temperature` for each:

- **Rule 1/2 - genuine demand:** among rooms with a *positive* delta (i.e. actually calling for more heat because they're below their target), it picks the one with the **largest** unmet demand. That room's target becomes the boiler's setpoint. This is "the room with the highest demand wins and drives the boiler" - but winning only sets the boiler's flow temperature; it doesn't stop any other room from heating too (see Tier 2 above).
- **Rule 3 - overshoot fallback:** if no room has positive demand (every heating room is already at or above its target), it falls back to whichever room is furthest *over*-target, just so the sensor always returns a value instead of nothing.
- **Rule 0 - "nothing really needs heat" guard:** if the winning candidate from the loop still has `delta <= 0` (meaning every candidate was an overshoot, never genuine demand), the result is overridden to `area: "Off", target: 10` (a frost-protection baseline) instead of reporting a misleading overshoot room's stale target as if it were real demand. Rule 0 never overrides genuine Rule 1/2 demand - it only kicks in when nothing in the house actually needs heat.
- **Default:** if no virtual thermostat is in `heat` mode at all, it reports `area: "Off", target: 10` directly (same frost-protection baseline).

Output is a JSON string, e.g.:
```json
{"area": "Living Room", "target": 18.0, "curr": 18.9, "delta": -0.9}
```

This is consumed by `automations/trv_setpoint_sync.yaml`'s "Sent the Virtual Setpoint to the CV" step, which parses it with `from_json` and sends `.target` to `climate.thermostat_central_heating`. **Any consumer of this sensor must parse it with `from_json` first** - it is not a bare number, and feeding the raw string straight into `| float` will fail or silently return a default.

### Why Rule 0 was added (a real bug found in production)
Before Rule 0 existed, this sensor had a sharp edge: if a virtual thermostat got stuck in `heat` mode when it shouldn't have been (e.g. a room's garden doors were left open and the room's heating failed to turn off - see the `garden_door_heating_pause.yaml` safety-net trigger above for one real cause of this), Rule 3 would still name that room the "winner" and report its stale target. In one observed case: the living room was stuck in `heat` at an 18°C target while its actual temperature was 25.5°C (garden doors open, heating should clearly have stopped) - and this sensor still reported `{"area": "Living Room", "target": 18.0, ...}`, which the automation then dutifully sent to the boiler as if it were genuine demand.

Rule 0 is a defense-in-depth fix for this class of bug: it stops any stuck/overshooting thermostat from ever misleading the boiler into thinking there's demand when there isn't. It does **not** replace fixing the root cause (a thermostat getting stuck in `heat` in the first place) - both fixes work together: Rule 0 protects the boiler from bad data, while the safety-net automation trigger works to prevent the bad data from happening at all.

**If you're copying this approach:** any "pick the best candidate, with a fallback if none qualify" template pattern should be checked for this exact edge case - make sure the fallback branch can't be mistaken for the genuine, unqualified `best` result by whatever consumes it downstream.

# Experimental: self-tuning heating and night cooldown profiling

> **Status: experimental, observe only.** Everything in this section records data and says what it *would* do - nothing here changes the heating yet. The code is in [`pyscript/`](pyscript/): a [pyscript](https://github.com/custom-components/pyscript) app ([`apps/heating_tuner.py`](pyscript/apps/heating_tuner.py), the Home Assistant glue) with the logic in plain-Python modules that have no Home Assistant dependencies ([`modules/heating_tuner_core.py`](pyscript/modules/heating_tuner_core.py) for warm-ups, [`modules/heating_night_core.py`](pyscript/modules/heating_night_core.py) for nights, [`modules/heating_gas_core.py`](pyscript/modules/heating_gas_core.py) for gas per degree-day, [`modules/heating_tuner_store.py`](pyscript/modules/heating_tuner_store.py) for file storage).

The PID values and the schedule above were tuned by hand, once. The goal is for Home Assistant to keep re-evaluating them from how the house actually behaves, in four phases:

| Phase | What | Status |
|---|---|---|
| 1. Measure | Kp/Ki/Kd adjustable from Home Assistant; PID terms and outdoor temperature exposed by the firmware | Live |
| 2. Observe | Record every warm-up and every night; work out what it *would* change | Live (since October 2026) |
| 3. Heating curve | Flow temperature follows the outdoor temperature, with the PID only trimming | Planned |
| 4. Auto-adjust | Apply its own recommendations within hard limits, with an on/off switch, a reset, and a notification for every change | Planned |

## Warm-up episodes (PID tuning)

Once a minute the tuner samples the boiler target, the room temperature the PID sees (`sensor.virtual_heating_temperature`), the winning room, flame, hot water, flow and return water temperature, outdoor temperature, the PID integral, the garden-door pause, the gas meter and the number of people home (`zone.home`). An **episode** starts when the room drops more than 0.1°C below target while central heating is enabled, and ends 90 minutes after the room first reaches the target (or after 4 hours without reaching it, or when heating stops).

Each episode is judged on time to target (more than 60 minutes counts as slow), overshoot (more than 0.4°C above target), dip below target afterwards (more than 0.4°C), flame starts per hour (more than 6 counts as short-cycling), and gas used. It also stores the average return water temperature while the burner heats (hot-water minutes excluded) - the lower, the more the condensing boiler recovers - as a baseline for the heating curve.

- **Only living-room-led episodes tune.** Other rooms win Tier 1 rarely; their episodes are recorded (reason `other_room`) so it can be judged later whether they need parameters of their own, but they never move the shared values.
- **Disturbed episodes are skipped:** winner or target changed, garden doors paused the heating, more than 10 minutes of hot water, or shorter than 30 minutes.
- **A recommendation needs agreement:** the last 2 valid episodes in the same outdoor-temperature band (below 0, 0-8, 8-15, above 15°C) must point the same way. Then exactly one parameter moves by 10%: overshoot → lower Ki; swinging or short-cycling → lower Kp; slow without overshoot → raise Kp.
- **Hard limits:** Kp 0.3-1.5, Ki 0.0005-0.01, Kd 0-2000.
- **It never touches the +1°C TRV buffer.**

## Night cooldown profiling

The heating goes off at 20:00 and the house cools until the morning start. Every night is sampled every 10 minutes (living room temperature, outdoor temperature, wind, garden doors, sun, people home) and turned into a small model:

- **The quiet part of the night** (from 01:00 until sunrise or until the heating comes on, garden doors closed) follows Newton's law of cooling, `dT/dt = -k · (T_room - T_outdoor)`, with time constant τ = 1/k. The first nights gave τ between 34 and 133 hours: this house holds its heat well, losing about 0.13°C per hour with an 8°C indoor-outdoor difference.
- **The evening doesn't follow that law.** People, cooking and appliances keep the living room warm for 2-4 hours after the heating stops - with the whole family coming home after 20:00 it can even warm up - then it drops 1-2°C around bedtime. That part is learned as a plain number, the "evening drop" from 21:00 to 01:00. The number of people home in the evening is recorded with it, so it can later be learned per occupancy (not everyone is tracked, so it's a relative signal).

At 21:00 the tuner takes the hourly forecast (`weather.forecast_home`) and predicts the living room temperature at the comfort time (07:30 on weekdays, 08:00 at weekends), and - from the warm-up speed measured in morning episodes in the same outdoor-temperature band as the forecast (all mornings until that band has data) - when the heating would have to start to reach 18°C by then. The next morning that prediction is compared with what really happened. A back-test on the first 9 evenings, with the measured outdoor temperature standing in for the forecast, was off by 0.2-0.8°C; it needs more nights before it can be trusted.

**Why not pre-heat during the night?** A house loses heat in proportion to how much warmer it is than outside, so with a gas boiler the cheapest night is the coldest one you accept: pre-heating only adds losses. The dynamic gas tariff doesn't change that much: the price is set per gas day and only changes at 06:00, so shifting heat within a night is worth cents. The tuner logs tomorrow's before/after-06:00 gas price and the gas used by every warm-up, so that question can be answered with data rather than assumed.

Once the predictions are reliable, they can drive an optimum start (start just late enough to be warm in time), an optimum stop (stop earlier in the evening while the room still stays comfortable), and a minimum night temperature for frost nights. The schedule times stay as they are until then.

## Gas per degree-day (did a change save gas?)

Raw gas use per day mostly follows the weather, so it can't show whether a change helped. The tuner normalises it with **degree-days** (base 18°C: a day averaging 8°C outside counts 10) and reports **heating gas per degree-day** over the last 7 and 28 days. Hot water and cooking use gas every day, so their share - the median on summer days without heating demand, about 0.3 m³/day here - is subtracted first. It reads Home Assistant's long-term statistics of the gas meter and the outdoor temperature (up to a year back), so it has values straight away. Single days are too noisy in mild weather (one shower more or less outweighs the heating), which is why only rolling values are reported.


1. Install the [pyscript](https://github.com/custom-components/pyscript) integration (HACS) and copy `pyscript/apps/` and `pyscript/modules/` from this repo into `/config/pyscript/`.
2. In the pyscript configuration (for example `pyscript/config.yaml`, included from `configuration.yaml` with `pyscript: !include pyscript/config.yaml`):

   ```yaml
   allow_all_imports: true   # the app imports homeassistant.util.dt
   hass_is_global: true
   apps:
     heating_tuner:              # needs at least one key: an empty block leaves pyscript.app_config undefined
       data_dir: /config/heating_tuner
       summary_time: "21:00"     # keep at 21:00: the night prediction runs then
   ```

3. The entity IDs are constants at the top of `heating_tuner.py` (thermostat, room and outdoor sensors, garden door, gas meter, Frank Energie gas prices, weather). Change them to yours; the gas-price sensors may be missing - they are only logged.
4. Reload pyscript. No restart is needed.

## Outputs

- A daily persistent notification at 21:00 ("Heating tuner *date*") with the day's warm-ups, the night, tonight's prediction and the current recommendation; `pyscript.heating_tuner_report` shows it on demand.
- `sensor.heating_tuner_last_overshoot`, `sensor.heating_tuner_recommendation`, `sensor.heating_tuner_night_prediction`, `sensor.heating_gas_per_degree_day`.
- Data in `/config/heating_tuner/`: `episodes.jsonl` and `nights.jsonl` (append-only, one JSON object per line) plus `state.json` and `night_state.json`. Not committed to git.

**If you're copying this approach:** pyscript runs your code in its own interpreter, which does **not** support generator expressions (`sum(1 for ...)`, `any(x for ...)`, `next((...), None)`). Use list comprehensions instead - otherwise the code only fails the first time that path actually runs, which for an analysis that runs once per warm-up can be days later.
