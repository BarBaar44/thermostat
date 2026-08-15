To remove the cloud dependency for my house heating, I decided to create this in Home Assistant. The requirements I set are:
- No cloud dependency
- Multi-room support
- Fully costimzable to my needs
- Auto on/off when somebody/nobody is home

Nice to have:
- Open Window detection

# Hardware

## Used boiler:
- Atag E325ec - 2015. This boiler supports the OpenThem Protocol

## Thermostat
- DIYLess OpenTherm thermostat with custom ESPHome config

Because I want full control, I immediately pushed ESPHome onto the thermostat. But the DIYLess Thermostat has software on it that should work..

I mostly got my config from XXX and XXX

As I already have an OpenTherm Gateway, I already knew what sensors my boiler supports. I basically trimmed down the sensor config towards the sensor my boiler supports.

The hardest part of getting the thermostat dailed in is setting the PID-controller. ESPHome has an autotune function for this. But this only helped me so much. I ran the autotune once to get some initial values. It wasn't long before I found that with the parameters of the autotune, my boiler never got to the actual temperature. Digging around to find the actual meaning of those parameters, I was about to give up, but then I just asked ChatGPT for some help and it got me in the right direction. Awesome AI for once!

## Thermostatic Radiator Valve (TRV)
- Sonoff TRVZB
- Shelly TRV Blu

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
- Very limited control.

# System overview

The whole heating system is built around one idea: each room/zone gets its own **virtual thermostat** (`climate.virtual_thermostat_*`) that acts as the single source of truth for "what temperature does this room want to be". Nothing else in the system is a source of truth for desired temperature - not the physical TRVs, not the boiler. Everything downstream (physical TRVs, the boiler setpoint) is kept in sync *with* the virtual thermostats, never the other way around.

This is split into three automations, two shared scripts, and one template sensor:

| File | Type | Responsibility |
|---|---|---|
| `automations/heating_schedule.yaml` | Automation | Daily start/stop scheduling + passive-solar "sun assist" logic |
| `automations/trv_setpoint_sync.yaml` | Automation | Keeps physical TRVs + the boiler in sync with the virtual thermostats |
| `automations/garden_door_heating_pause.yaml` | Automation | Open-window/door detection for the living room garden doors |
| `scripts/pause_livingroom_heating.yaml` | Script | Snapshot + pause living room heating (shared by two triggers) |
| `scripts/resume_livingroom_heating.yaml` | Script | Restore living room heating from snapshot |
| `sensors/delta_temperature.yaml` | Template sensor | Decides the single boiler setpoint across all rooms |

> **History note:** this used to be one large automation, `housewarming.yaml`, with all of the above logic inline. It was split up for better isolation (a bug in door detection can no longer break the daily schedule) and so each concern gets its own trace history in Home Assistant. `housewarming.yaml` and the original `deltaTemperature` file are both kept in the repo only as deprecated historical references - see the deprecation notice at the top of each file. If you're setting this up fresh, use the files in `automations/`, `scripts/`, and `sensors/` instead.

### Rooms / zones covered
- Living room (`climate.virtual_thermostat_living_room`) → TRVs: kitchen, living room front, living room rear (+ derives the hallway TRV setpoint)
- Gijs' room (`climate.virtual_thermostat_room_gijs`) → `climate.trv_room_gijs`
- Tim's room (`climate.virtual_thermostat_room_tim`) → `climate.trv_room_tim`
- Sports room (`climate.virtual_thermostat_sports_room`) → `climate.trv_sports_room`
- Office (`climate.virtual_thermostat_office`) → `climate.trv_office`
- Master bedroom (`climate.virtual_thermostat_master_bedroom`) → 2x Shelly BLU TRV
- Hallway (`climate.trv_hallway`) has no virtual thermostat of its own; it's derived from the living room setpoint (see below)

> **Naming tip if you're copying this approach:** name every virtual thermostat with a consistent prefix (here, `climate.virtual_thermostat_*`). Both `trv_setpoint_sync.yaml`'s trigger list and `delta_temperature.yaml`'s `startswith('climate.virtual_thermostat_')` filter rely on that consistent prefix to find all rooms automatically. This repo previously had one room (`climate.thermostat_room_tim`) that didn't follow the convention, which caused it to be silently invisible to the Delta Temperature sensor's room-selection logic until it was renamed to `climate.virtual_thermostat_room_tim`.

# Automations

## `automations/trv_setpoint_sync.yaml` - TRV Setpoint Sync

Triggered whenever the `temperature` attribute of any virtual thermostat changes. Each of the six room-sync actions is individually gated by an `if:` condition checking `trigger.entity_id == <that room's virtual thermostat>` **and** that its `temperature` attribute `is not none`, before pushing the new setpoint out to that room's physical TRV(s) (with a **+1°C** offset - TRVs tend to read warmer than the room actually is, since they sit close to the radiator).

This per-room gating was added after a production bug: the sequence used to unconditionally re-sync *all six* rooms on every single setpoint change, regardless of which thermostat actually fired the trigger. If any one of those six virtual thermostats was `off` (and therefore had no `temperature` attribute set), reading `None + 1` crashed the whole sequence - silently skipping every sync step listed after the crash point. Scoping each sync to its own trigger, with an explicit null-check, fixed this and also cut down on redundant service calls to unrelated TRVs.

**If you're copying this approach:** this pattern (gate every action with `trigger.entity_id == X and state_attr(X, ...) is not none`) is worth using any time a single trigger definition lists multiple entities but the following actions assume a specific one of them fired. Otherwise a `None` attribute on *any* listed entity can crash actions meant for a *different* entity entirely.

The hallway is a special case: since it has no thermostat of its own, its TRV setpoint is derived from the living room setpoint **minus 8°C** (with a floor of 5°C), so it stays comfortably lower than the living room instead of overheating. It's synced together with the living room TRVs since it depends on the same source value.

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

# Delta Temperature sensor (`sensors/delta_temperature.yaml`)

A template sensor helper (`sensor.delta_temperature`) that recalculates every 10 seconds (`time_pattern`, `seconds: "/10"`) and acts as the "brain" deciding what setpoint the central heating boiler should actually be given, across a house with multiple independently-controlled rooms that a single-setpoint boiler cannot natively understand.

### Why this is needed
A boiler only has one setpoint. But this system has many rooms, each with their own virtual thermostat, each potentially wanting a different temperature at any given moment. Something has to reduce "many rooms, many demands" down to "one number the boiler understands" - that's this sensor's entire job.

### How it decides
It scans every `climate.virtual_thermostat_*` entity currently in `heat` mode and computes `delta = target - current_temperature` for each:

- **Rule 1/2 - genuine demand:** among rooms with a *positive* delta (i.e. actually calling for more heat because they're below their target), it picks the one with the **largest** unmet demand. That room's target becomes the boiler's setpoint.
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
