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

# Automations

## House warming (`housewarming.yaml`)

This is the main Home Assistant automation that ties the whole multi-room heating setup together. It runs in `queued` mode (max 10 queued runs) so overlapping triggers (e.g. a setpoint change right as the schedule starts) don't get dropped.

It uses one "virtual thermostat" per room/zone (`climate.virtual_thermostat_*`) as the single source of truth for the desired room temperature, and then keeps the physical TRVs and the boiler in sync with those virtual thermostats.

### Rooms / zones covered
- Living room (`climate.virtual_thermostat_living_room`) → TRVs: kitchen, living room front, living room rear
- Gijs' room (`climate.virtual_thermostat_room_gijs`) → `climate.trv_room_gijs`
- Tim's room (`climate.virtual_thermostat_room_tim`) → `climate.trv_room_tim`
- Sports room (`climate.virtual_thermostat_sports_room`) → `climate.trv_sports_room`
- Office (`climate.virtual_thermostat_office`) → `climate.trv_office`
- Master bedroom (`climate.virtual_thermostat_master_bedroom`) → 2x Shelly BLU TRV
- Hallway (`climate.trv_hallway`) has no virtual thermostat of its own; it's derived from the living room setpoint (see below)

### What it does

**1. Setpoint change (`id: Setpoint change`)**
Triggered whenever the `temperature` attribute of any virtual thermostat changes. Pushes that setpoint out to the matching physical TRV(s), and pushes the overall virtual heating setpoint to `climate.thermostat_central_heating` (the boiler).

Because TRVs tend to read warmer than the room actually is (they sit close to the radiator), most rooms get the virtual setpoint **+1°C** when applied to the TRV, to compensate. The hallway is a special case: since it has no thermostat of its own, its TRV setpoint is derived from the living room setpoint **minus 8°C** (with a floor of 5°C), so it stays comfortably lower than the living room instead of overheating.

**2. Start of day — weekdays (`Weekdays Start` @ 07:30) or someone arriving home (`Arriving Home`)**
Only runs while `input_boolean.somebody_home` is on and before 20:00. It checks tomorrow's... actually today's daily weather forecast (`weather.forecast_home`):
- If it's **sunny/clear and mild (>12°C)**, it assumes passive solar heating will help, so it sets the living room to a lower 15°C, turns the boiler enabler switch on, and schedules a follow-up check in 1 hour via `input_datetime.sun_check_time` (flagged with `input_boolean.sun_assist_pending`).
- Otherwise, it goes straight to a normal 18°C heat setpoint and turns the boiler on.

**3. Start of day — weekend (`Weekend Start` @ 08:00)**
Same sunny/mild-vs-normal logic as above, just on the weekend schedule (only while somebody is home).

**4. Sun check (`Sun check`, dynamic time from `input_datetime.sun_check_time`)**
Follow-up to the "sunny and mild" branch above. If `input_boolean.sun_assist_pending` is still on, it checks the living room's actual current temperature. If it's still below 16°C (i.e. the sun didn't do enough), it bumps the living room up to a full 18°C heat setpoint and makes sure the boiler is enabled. Either way, `sun_assist_pending` is cleared afterwards.

**5. Stop heating (`Schedule stop` @ 20:00, or `Leaving Home`)**
Sets all managed climate entities (living room, master bedroom, Gijs' room, hallway, office, master bedroom BLU TRVs, Tim's room, sports room) to 10°C and `hvac_mode: off`, and turns off the boiler enabler switch. Also clears the `garden_heating_paused` and `sun_assist_pending` helper booleans so the state is clean for the next day.

**6. Open window / door detection — garden doors open (`Garden doors open`, debounced 2 min)**
If the living room is actively heating when the garden doors have been open for 2+ minutes, it snapshots the current state of the living room TRVs + virtual thermostat into a scene (`scene.heatinglivingroomscene`), turns the living room thermostat off, and sets `input_boolean.garden_heating_paused` so it knows to resume later.

**7. Garden doors closed, still within schedule (`Garden doors closed`, debounced 1 min, before 20:00)**
If heating was paused due to open doors, restores the previously saved scene (bringing the living room back to whatever it was set to before) and clears the paused flag.

**8. Garden doors closed, after schedule stop (after 20:00)**
If the doors close after the 20:00 schedule stop already happened, it doesn't resume heating — it explicitly sets the living room to 10°C / off and clears the paused flag, so the room doesn't accidentally start heating again outside the schedule.

### Helper entities used
- `input_boolean.somebody_home` — presence flag driving the daily start/stop logic
- `input_boolean.sun_assist_pending` — tracks whether a "sunny start" is awaiting its 1‑hour follow-up check
- `input_boolean.garden_heating_paused` — tracks whether living room heating was paused due to open garden doors
- `input_datetime.sun_check_time` — dynamically scheduled time for the follow-up sun check
- `binary_sensor.backdoors_garden` — open/closed sensor for the garden doors (open window detection)
- `switch.main_thermostat_central_heating_enabled` — master enable/disable switch for the boiler
- `weather.forecast_home` — used to decide whether to rely on passive solar heating in the morning
