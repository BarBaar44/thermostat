
To remove the cloud dependency for my house heating, I decided to create this in Home Assistant. The requirements I set are:
- No cloud dependency
- Multi-room support
- Fully costimzable to my needs
- Auto on/off when somebody/nobody is home

Nice to have:
- Open Window detection

# Hardware

## Used boiler:
- Atag E325EC - 2015. This boiler supports the OpenTherm Protocol

## Thermostat
- [DIYLess OpenTherm thermostat](https://diyless.com/product/opentherm-thermostat?srsltid=AfmBOormPBHNAZ45YebkE3Il7vgdJ_GHKKCUzcF6QX03WaKtlIiR_Y78) with custom ESPHome config

Because I want full control, I immediately pushed ESPHome onto the thermostat. But the DIYLess Thermostat has software on it that should work.

As I already have an OpenTherm Gateway, I already knew what sensors my boiler supports. I basically trimmed down the sensor config towards the sensor my boiler supports.

The hardest part of getting the thermostat dailed in is setting the PID-controller. ESPHome has an autotune function for this. But this only helped me so much. I ran the autotune once to get some initial values. It wasn't long before I found that with the parameters of the autotune, my boiler never got to the actual temperature. Digging around to find the actual meaning of those parameters, I was about to give up, but then I just asked ChatGPT for some help and it got me in the right direction. Awesome AI for once!

## Thermostatic Radiator Valve (TRV)
- [Sonoff TRVZB](https://sonoff.tech/en-nl/products/sonoff-zigbee-thermostatic-radiator-valve?srsltid=AfmBOoqOOgjFyiwdsu4zjLnLMnoswNaaPmH9IqcZ7rdTpexA4DVSjDKT)
- [Shelly TRV Blu](https://www.shelly.com/products/shelly-blu-trv-single-pack?srsltid=AfmBOooqX_GBCt0xQm6i7mDl32QgD_uiqZTU5LVj3VwcJ3pECvlg9b8W)

Yes, you got that right. I use 2 kinds of TRV's. For two radiators, as I have limited space for the TRV's. The Sonoff TRV's just didn't fit, so I had to fall back to the Shelly's. The Shelly TRV's are very much like the Tado's. Both in dimension and in appearance. Here are the pros and cons.

### Sonoff TRZB
Pros:
- Cheaper
- Completely local
- Supported by Zigbee2MQTT

Cons:
- A little bigger than Tado (and Shelly)
- Control is somewhat complicated. You can control the valve opening, but not very straightforward. At least not when using Zigbee2MQTT

### Shelly TRV Blue
Pros:
- Small, about the same size as Tado TRV's
- Possible custom firmware?

Cons:
- Expensive
- Still dependent on a third party
- you need a stick to control them
- You need another integration in HA (Shelly)
- No Zigbee control

# Automations

> **This system was originally one large automation (`housewarming.yaml`).** It has since been split into three focused automations plus two shared scripts, so a bug in one area (e.g. garden door detection) can't take down unrelated logic (e.g. the daily schedule), and each piece gets its own trace history in Home Assistant. `housewarming.yaml` is kept in the repo only for historical reference - it is no longer used and should be deleted/disabled in Home Assistant in favor of the files below.

All three automations share the same underlying design: one "virtual thermostat" per room/zone (`climate.virtual_thermostat_*`) as the single source of truth for the desired room temperature, with physical TRVs and the boiler kept in sync with those virtual thermostats.

### Rooms / zones covered
- Living room (`climate.virtual_thermostat_living_room`) → TRVs: kitchen, living room front, living room rear (+ derives the hallway TRV setpoint)
- Gijs' room (`climate.virtual_thermostat_room_gijs`) → `climate.trv_room_gijs`
- Tim's room (`climate.virtual_thermostat_room_tim`) → `climate.trv_room_tim`
- Sports room (`climate.virtual_thermostat_sports_room`) → `climate.trv_sports_room`
- Office (`climate.virtual_thermostat_office`) → `climate.trv_office`
- Master bedroom (`climate.virtual_thermostat_master_bedroom`) → 2x Shelly BLU TRV
- Hallway (`climate.trv_hallway`) has no virtual thermostat of its own; it's derived from the living room setpoint (see below)

## `automations/trv_setpoint_sync.yaml` - TRV Setpoint Sync

Triggered whenever the `temperature` attribute of any virtual thermostat changes. Each of the six room-sync actions is individually gated by an `if:` condition checking `trigger.entity_id == <that room's virtual thermostat>` **and** that its `temperature` attribute `is not none`, before pushing the new setpoint out to that room's physical TRV(s) (with a **+1°C** offset - TRVs tend to read warmer than the room actually is, since they sit close to the radiator).

This per-room gating was added after a production bug: the sequence used to unconditionally re-sync *all six* rooms on every single setpoint change, regardless of which thermostat actually fired the trigger. If any one of those six virtual thermostats was `off` (and therefore had no `temperature` attribute set), reading `None + 1` crashed the whole sequence - silently skipping every sync step listed after the crash point. Scoping each sync to its own trigger, with an explicit null-check, fixed this and also cut down on redundant service calls to unrelated TRVs.

The hallway is a special case: since it has no thermostat of its own, its TRV setpoint is derived from the living room setpoint **minus 8°C** (with a floor of 5°C), so it stays comfortably lower than the living room instead of overheating. It's synced together with the living room TRVs since it depends on the same source value.

This automation also pushes the overall central heating setpoint to `climate.thermostat_central_heating` (the boiler), sourced from the `sensor.delta_temperature` helper (see below) - parsed with `from_json` to extract its `.target` field, with a safe `float(10)` fallback.

## `automations/heating_schedule.yaml` - Heating Schedule

**1. Start of day - weekdays (`Weekdays Start` @ 07:30) or someone arriving home (`Arriving Home`)**
Only runs while `input_boolean.somebody_home` is on and before 20:00. It checks tomorrow's... actually today's daily weather forecast (`weather.forecast_home`):
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
A self-healing safety net for step 1. HA's `state` trigger with `from`/`to` only fires on the *exact moment* of an `off → on` transition - if that transition is missed (e.g. Home Assistant restarts, or the automation/underlying template sensor reloads, while the garden doors are *already* open), step 1 never fires and the living room can stay heating indefinitely with the doors open. This trigger re-checks every 5 minutes: if the doors are currently open **and** the living room virtual thermostat is still in `heat`, it calls `script.pause_livingroom_heating` again, so the system self-corrects within 5 minutes regardless of *why* the original edge trigger was missed.

## `scripts/pause_livingroom_heating.yaml` and `scripts/resume_livingroom_heating.yaml`

Extracted from the duplicated scene-snapshot/turn-off/turn-on sequences that used to appear inline in two separate places in `housewarming.yaml` (the doors-open branch and the new safety-net branch would otherwise have had identical logic copy-pasted). Now both `Garden Door Heating Pause` branches (the edge-triggered one and the safety-net one) call the same `script.pause_livingroom_heating`, so any future change to the pause behaviour only needs to happen in one place.

- **`script.pause_livingroom_heating`**: snapshots the living room TRVs + virtual thermostat into `scene.heatinglivingroomscene`, turns the living room virtual thermostat off, and sets `input_boolean.garden_heating_paused` on.
- **`script.resume_livingroom_heating`**: restores `scene.heatinglivingroomscene` and turns `input_boolean.garden_heating_paused` off.

### Helper entities used (across all three automations)
- `input_boolean.somebody_home` - presence flag driving the daily start/stop logic
- `input_boolean.sun_assist_pending` - tracks whether a "sunny start" is awaiting its 1-hour follow-up check
- `input_boolean.garden_heating_paused` - tracks whether living room heating was paused due to open garden doors
- `input_datetime.sun_check_time` - dynamically scheduled time for the follow-up sun check
- `binary_sensor.backdoors_garden` - open/closed sensor for the garden doors (open window detection); combines `binary_sensor.door_sensor_garden_doors_contact` and `binary_sensor.door_sensor_kitchen_door_contact`
- `switch.main_thermostat_central_heating_enabled` - master enable/disable switch for the boiler
- `weather.forecast_home` - used to decide whether to rely on passive solar heating in the morning
- `sensor.delta_temperature` - see below; drives the central heating boiler setpoint

## Delta Temperature sensor (`deltaTemperature`)

A template sensor helper (`sensor.delta_temperature`) that recalculates every 10 seconds (`time_pattern`, `seconds: "/10"`) and acts as the "brain" deciding what setpoint the central heating boiler should actually be given.

### What it does
It scans every `climate.virtual_thermostat_*` entity currently in `heat` mode and computes `delta = target - current_temperature` for each:
- **Rule 1/2 - genuine demand:** among rooms with a *positive* delta (i.e. actually calling for more heat), it picks the one with the **largest** unmet demand.
- **Rule 3 - overshoot fallback:** if no room has positive demand (everything is at or above its target), it falls back to whichever room is furthest *over*-target, just so the sensor always returns a value.
- **Default:** if no virtual thermostat is in `heat` mode at all, it reports `area: "Off", target: 10` (frost-protection baseline).

Output is a JSON string, e.g.:
```json
{"area": "Living Room", "target": 18.0, "curr": 18.9, "delta": -0.9}
```

This is consumed by `automations/trv_setpoint_sync.yaml`'s "Sent the Virtual Setpoint to the CV" step, which parses it with `from_json` and sends `.target` to `climate.thermostat_central_heating`.

### ⚠️ Known limitation (not yet fixed)
Rule 3's overshoot fallback currently reports the overshooting room as the "winner" with its stale target - even when the underlying issue is that a virtual thermostat is stuck in `heat` mode when it shouldn't be (e.g. living room heating not stopping despite the garden doors being open, at 25°C+, target 18°C). In that scenario the boiler is told "target 18°C" as if it were real demand, when the honest answer is that nothing in the house currently has genuine heating demand.

**Planned fix ("Rule 0"):** after the main loop, if `best.delta <= 0` (meaning every candidate found was an overshoot fallback, not real demand), override the result to `area: "Off", target: 10` before emitting JSON, instead of passing along a misleading overshoot room. This is a defense-in-depth fix - it doesn't replace fixing the root cause of a thermostat getting stuck in `heat` (the Garden Door Heating Pause safety-net trigger above helps with that), but it stops that class of bug from ever reaching the boiler. Not yet applied to the live helper as of this writing.
