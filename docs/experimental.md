# Experimental features

Cloudless Multiroom Thermostat has a set of **observe-only** diagnostics. They record how the house and the boiler behave and report what they *would* change. **Nothing here changes the heating**: no setpoints, no PID values, no schedule.

They are off by default. To switch them on: **Settings > Devices & services > Cloudless Multiroom Thermostat > Configure**, tick **Enable experimental features**, then fill in the entities on the next page. Every entity is optional; each feature runs as soon as the entities it needs are set. [The entities on the settings page](#the-entities-on-the-settings-page) explains each one and where to get it.

## What this is for

The aim is to learn how one room and the boiler behave, so the heating can later tune its own settings to the weather instead of running on fixed values. The room you pick under **Room to follow** (usually the living room: people are there and it leads the heating most often) is profiled:

- **How it warms up**: how the boiler's PID loop behaves on each warm-up, per outdoor temperature band (warm-up episodes).
- **How it cools down**: how fast the room loses heat at night (night cooldown model).
- **What it costs**: gas use corrected for the weather (gas per degree-day). This one covers the whole house and is the yardstick: it shows whether a change to the other two actually saved gas.

The end goal is **dynamic parameters**: PID values per outdoor band instead of one fixed set, and a morning start time that reaches the comfort temperature just in time (optimum start).

## When a feature stops being experimental

Each feature has a bar to clear. Until it does, it stays observe only. After it does, it may act on the heating, but only once you switch that on separately.

| Feature | Stable when | Then it may |
|---|---|---|
| Gas per degree-day | A full year of data (so a whole heating season), and the hot-water rate is a setting instead of a constant | Serve as the official check for the other features |
| Night cooldown model | At least 30 predicted nights spread over 3 or more outdoor bands, average error 0.3 °C or less, no night off by more than 0.6 °C | Optimum start: begin heating at the predicted time |
| Warm-up episodes and PID advice | PID values changed by hand in at least 2 outdoor bands, each followed by 5 or more valid episodes of the followed room with less overshoot or a faster warm-up on average than the 5 before, and gas per degree-day not worse | Set the PID values per outdoor band (gain scheduling) |
| Evening report | The features it reports on are stable | Stay as it is |

The night model counts its last 30 valid nights that had a prediction. A change by hand is noticed when the Kp, Ki or Kd of an episode differ from the episode before; the band is the one the first episode after the change ran in. Whether gas per degree-day got worse is left to you: one change does not move it enough to tell automatically.

The [evening report](#evening-report) shows where each feature stands against these numbers. They are a starting point and may be adjusted as more data comes in.

## Maturity at a glance

| Feature | Maturity | Needs | Output |
|---|---|---|---|
| [Gas per degree-day](#gas-per-degree-day) | **Beta**: running daily since October 2026, the figures are usable for comparing weeks | Gas meter, outdoor temperature. For the hot-water split also burner flame, hot water active, central heating active. Optional: people at home | `sensor.<system>_gas_per_degree_day` |
| [Night cooldown model](#night-cooldown-model) | **Alpha**: still learning; first back-test was 0.2 to 0.8 °C off | Outdoor temperature, weather forecast, a room to follow | `sensor.<system>_night_prediction` |
| [Warm-up episodes and PID advice](#warm-up-episodes-and-pid-advice) | **Alpha**: records reliably, advice not yet validated, never applied | Room to follow. Helpful: burner flame, outdoor temperature. For advice: Kp, Ki, Kd of a firmware PID (for example ESPHome) | `sensor.<system>_last_warm_up_overshoot`, `sensor.<system>_pid_advice` |
| [Evening report](#evening-report) | **Beta**: a summary of the above | Any of the above | Persistent notification at 21:00 |

Maturity means:

- **Alpha**: the data collection works, the conclusions are not trustworthy yet. Read them, don't act on them.
- **Beta**: the numbers are useful; details may still change.
- **Stable**: not used yet; nothing here is stable. See [When a feature stops being experimental](#when-a-feature-stops-being-experimental).

The analysis has run in one house (an Atag boiler on OpenTherm with an ESPHome PID) since October 2026; the maturity above describes that analysis. If something looks wrong, please [open an issue](https://github.com/BarBaar44/thermostat/issues).

## The entities on the settings page

The settings page asks for a lot of entities. You do not need all of them: each feature starts as soon as the entities it needs are set, and an empty field simply means less detail. This section explains every field: what it is, why it is asked for, whether you need it and where you can get it.

### Which fields each feature needs

✔ = needed for the feature to run, ○ = optional, adds detail, blank = not used.

| Field | Gas per degree-day | Night cooldown model | Warm-up episodes and PID advice |
|---|---|---|---|
| Room to follow | | ✔ | ✔ (advice only counts this room) |
| Comfort time on weekdays / at weekends | | ✔ | |
| Outdoor temperature | ✔ | ✔ | ○ (sorts warm-ups by weather) |
| Weather forecast | | ✔ (for the prediction) | |
| People at home | ○ | ○ | ○ |
| Gas meter | ✔ | ○ | ○ |
| Burner flame | ○ (hot-water split) | | ○ (short-cycling check) |
| Hot water active | ○ (hot-water split) | | ○ (skips hot-water disturbed warm-ups) |
| Central heating active | ○ (hot-water split) | | |
| Flow water temperature | | | ○ (shown in the report) |
| Return water temperature | | | ○ (shown in the report) |
| PID Kp, Ki, Kd | | | ✔ for advice (all three) |
| Gas price before / after 06:00 | | ○ | |

**A sensible start for most houses:** outdoor temperature, weather forecast and gas meter. That gives you gas per degree-day and the night model. The boiler fields only make sense with a smart (OpenTherm) boiler connection; the PID fields only with an ESPHome style PID.

**Long-term statistics.** Gas per degree-day reads Home Assistant's long-term statistics of the gas meter and the outdoor temperature, so both sensors need them. A sensor has them when it has a `state_class` (`measurement` for a temperature, `total_increasing` or `total` for a meter). Most integrations set this already. Check it under **Developer tools > Statistics**: if the sensor is listed there, it is fine.

### Room to follow

- **What:** one of your rooms, picked from a list.
- **Why:** the night model and the PID advice study one room in depth. Pick the room where people spend the most time and that leads the heating most often, usually the living room. Its **warm** temperature is the morning target for the night prediction.
- **Required:** yes, the form always has a value.
- **Where from:** your own rooms in this integration.

### Comfort time on weekdays / at weekends

- **What:** the time in the morning by which the followed room should be warm.
- **Why:** the night prediction works out how cold the room will be at that time and when the heating would have to start to be warm by then.
- **Required:** yes, with defaults you can keep.
- **Where from:** your own routine.

### Outdoor temperature

- **What:** a sensor with the temperature outside, in °C.
- **Why:** almost everything here depends on the weather. Gas use is corrected for how cold it was (degree-days), the night model needs the difference between inside and outside to know how fast the room cools, and warm-ups are grouped by outdoor temperature so advice compares like with like.
- **Required:** for gas per degree-day and the night model, yes. Without it, warm-ups still get recorded but all land in one "unknown" weather group.
- **Where from:**
  - **Your own outdoor sensor** (Zigbee, Bluetooth, a weather station) is best: it measures your house's real outside. Place it in the shade, out of the sun and away from walls that warm up.
  - **Your boiler**, if it has an outdoor sensor and an OpenTherm connection (OpenTherm Gateway, ESPHome OpenTherm).
  - **A weather service** works too: many weather integrations (Buienradar, OpenWeatherMap) add a temperature *sensor*. The default Met.no integration only has a weather *entity*; you can turn its temperature into a sensor with a template sensor (device class temperature, state class measurement).

### Weather forecast

- **What:** a `weather` entity that offers an **hourly** forecast.
- **Why:** at 21:00 the night model reads tonight's expected outdoor temperatures to predict how cold the room will be in the morning. It also records the wind speed during the night, since wind cools a house faster.
- **Required:** for the prediction, yes. Without it nights are still recorded and learned from, but no prediction is made.
- **Where from:** Home Assistant sets one up for you at installation: Met.no, usually called `weather.forecast_home`. Buienradar, OpenWeatherMap and similar weather integrations also work, as long as they have an hourly forecast. If the forecast is missing, the Home Assistant log shows "No hourly forecast from …".

### People at home

- **What:** something that says how many people are at home, as a number.
- **Why:** days when nobody was home are left out of the gas figures, because an empty house uses less gas and would make the weather correction look better than it is. Warm-ups and nights also note how many people were home: people, cooking and appliances warm a room too.
- **Required:** no. Without it, every day counts and nothing is left out.
- **Where from:** `zone.home` (filled in for you when it exists). Its value is the number of **persons** inside your home zone, so it works when each person has a device tracker, usually the Home Assistant Companion app on their phone. A sensor that counts people in another way (for example a template sensor) works too.

### Gas meter

- **What:** a sensor with the **total** gas use in m³, the number that only goes up.
- **Why:** this is the gas use itself, the thing gas per degree-day measures. Nights and warm-ups also record how much gas they used.
- **Required:** for gas per degree-day, yes.
- **Where from:** in the Netherlands and Belgium, the smart meter's P1 port, read with the DSMR Smart Meter integration, a HomeWizard P1 meter or a similar dongle. If your gas already shows up in Home Assistant's **Energy** dashboard, that sensor is the right one. A daily or monthly usage sensor (one that resets) does **not** work.

### Burner flame, Hot water active, Central heating active

- **What:** three on/off sensors from the boiler: is the flame burning, is the boiler making hot water for a tap or shower, and is it heating the house.
- **Why:**
  - Together they split each day's gas into **hot water** and **heating**. Only the heating part depends on the weather, so without the split a long shower can look like a cold day. All three are needed for the split.
  - Burner flame also counts how often the boiler starts during a warm-up. Many short starts (short-cycling) wear the boiler and waste gas.
  - Hot water active lets warm-ups be skipped when a lot of hot water was used in the middle of one; the boiler then stops heating the house for a while and the warm-up does not show how the heating behaves.
- **Required:** no. Without them gas per degree-day still works, but uses an estimate for hot water.
- **Where from:** only boilers with a digital connection give these, usually **OpenTherm**:
  - the **OpenTherm Gateway** integration (OTGW),
  - an **ESPHome OpenTherm** board (flame, DHW active and CH active binary sensors),
  - a **DIYLess** thermostat,
  - some manufacturers' own integrations.
  A boiler switched by a simple relay (on/off) cannot report these.

  **Note:** the hot-water split uses a fixed rate of 0.019 m³ of gas per burner minute, measured on one boiler (about 11 kW on hot water). With a different boiler the split will be off until this becomes a setting.

### Flow water temperature, Return water temperature

- **What:** two sensors in °C. *Flow* is the water leaving the boiler towards the radiators, *return* is the water coming back.
- **Why:** recorded with each warm-up and shown in the report (highest flow, average return), so you can see how hard the boiler worked. A condensing boiler is most efficient when the return water stays below about 55 °C: then it recovers extra heat from its exhaust.
- **Required:** no. They do not affect the advice.
- **Where from:** the same sources as the flame sensors: OpenTherm Gateway, ESPHome OpenTherm, DIYLess.

### PID Kp, PID Ki, PID Kd

- **What:** the three tuning values of the PID controller in your boiler firmware that decides the flow temperature. Roughly: **Kp** reacts to how far the room is below target, **Ki** to how long it has been below, **Kd** to how fast the temperature changes.
- **Why:** the PID advice suggests a better value for one of these. It needs to know the current values to suggest a change, and it notices when you change one by hand so it can check whether the change helped.
- **Required:** all three for advice. Without them warm-ups are still recorded and judged, but no advice is given.
- **Where from:** an ESPHome firmware with a PID climate, where the values are exposed as **number** entities (or kept in **input_number** helpers that match what you flashed). The advice limits (Kp 0.3 to 1.5, Ki 0.0005 to 0.01, Kd 0 to 2000) fit an ESPHome PID driving an OpenTherm flow temperature. **If your boiler has no such PID, leave these empty**: the advice would not make sense for it.

### Gas price tomorrow before 06:00 / after 06:00

- **What:** two sensors with the gas price for tomorrow, before and after 06:00.
- **Why:** dynamic gas contracts change price at 06:00. The prices are shown next to the night prediction, so you can see whether starting the heating a bit earlier would be cheaper. Nothing is calculated with them yet.
- **Required:** no, purely informational.
- **Where from:** an integration for a dynamic energy contract, such as Frank Energie, Zonneplan, Tibber or EnergyZero. These usually give the current price or a list of prices; you may need a template sensor to pick tomorrow's price before and after 06:00.

## Gas per degree-day

**Question it answers:** did a change (a new setpoint, PID values, a schedule) save gas?

Raw gas use per day mostly follows the weather, so it can't show that. The feature normalises it with **degree-days**: base 18 °C, so a day averaging 8 °C outside counts 10. It reports **heating gas per degree-day** over the last 7 days (the sensor value) and 28 days (an attribute). It reads Home Assistant's long-term statistics of the gas meter and the outdoor temperature, up to a year back, so it has values straight away. Single days are too noisy in mild weather (one shower more or less outweighs the heating), which is why only rolling values are reported.

- **Hot water is split off per day.** When burner flame, hot water active and central heating active are set, each complete day is split into hot-water and heating gas from the hourly gas statistics and the boiler history. An hour without heating demand burned only for hot water. In an hour with heating demand, the hot-water part is its hot-water minutes at **0.019 m³ per burner minute**. That rate was measured on one boiler (about 11 kW on hot water); **with a different boiler the split will be off** until this becomes a setting. Home Assistant keeps that history for 7 days, so each split is stored as it is made. Days before the first split use a base estimate instead: the median measured hot-water gas, or the median gas on summer days without heating.
- **Days nobody was home are left out.** With **People at home** set (`zone.home` by default), a day counts as away only if nobody was home for the whole calendar day. The day you come back stays in: warming the house up again is real gas the trip cost.

**Gas meter:** a total meter in m³ with long-term statistics (for example a P1 or HomeWizard gas meter). The sensor shows `unknown` until a 7-day window holds at least 5 degree-days.

## Night cooldown model

**Question it answers:** how cold will the room be tomorrow morning, and when would the heating have to start to be warm in time?

When the heating goes off in the evening (no room heating any more, between 18:00 and 02:00), a **night** starts. Every 10 minutes it samples the room you chose under **Room to follow**, the outdoor temperature, wind, that room's windows, the sun and how many people are home, until the heating comes back on (or 11:00). Nights shorter than 4 hours are ignored.

- **The quiet part of the night** (from 01:00 until sunrise or until heating starts, windows closed) follows Newton's law of cooling, `dT/dt = -k * (T_room - T_outdoor)`, with time constant tau = 1/k.
- **The evening does not follow that law.** People, cooking and appliances keep a living room warm for a few hours after the heating stops, then it drops around bedtime. That part is learned as a plain number: the evening drop from 21:00 to 01:00.

At 21:00 it takes the hourly forecast and predicts the room temperature at your **comfort time** (weekday and weekend times are settings). From the warm-up speed of earlier morning warm-ups in a similar outdoor temperature, it says when heating would have to start to reach the room's **warm** temperature by then. The next morning the prediction is compared with what really happened.

**Why alpha:** predictions start after 2 valid nights and 2 morning warm-ups. In the house it was built in, a back-test on the first 9 evenings was off by 0.2 to 0.8 °C. It needs many more nights, in different weather, before it can drive anything (for example an optimum start).

## Warm-up episodes and PID advice

**Question it answers:** is the boiler's PID loop tuned well for this house?

Once a minute it samples the heat source target, the winning room's temperature, flame, hot water, flow and return temperature, outdoor temperature and the gas meter. An **episode** starts when the winning room drops more than 0.1 °C below target, and ends 90 minutes after the room first reaches the target (or after 4 hours without reaching it, or when heating stops).

Each episode is judged on time to target (more than 60 minutes is slow), overshoot (more than 0.4 °C), the dip below target afterwards (more than 0.4 °C) and flame starts per hour (more than 6 is short-cycling).

- **Only episodes led by the room to follow count for advice.** Others are recorded (reason `other_room`).
- **Disturbed episodes are skipped:** winner or target changed, that room's window paused heating, more than 10 minutes of hot water, or shorter than 30 minutes.
- **Advice needs agreement:** the last 2 valid episodes in the same outdoor band (below 0, 0 to 8, 8 to 15, above 15 °C) must point the same way. Then exactly one value moves by 10%: overshoot means lower Ki; swinging or short-cycling means lower Kp; slow without overshoot means raise Kp. Hard limits: Kp 0.3 to 1.5, Ki 0.0005 to 0.01, Kd 0 to 2000.
- **Advice only appears when Kp, Ki and Kd are all set.** Those limits fit an ESPHome PID climate driving an OpenTherm flow temperature; for another controller they may not make sense.

**Why alpha:** no advice has been applied and checked yet. It says what it *would* do, nothing more.

## Evening report

Every evening at 21:00 a persistent notification lists today's warm-up episodes, the current PID values and advice, the last night, tonight's prediction, gas per degree-day and the progress of each feature towards stable, with a link to this page. Call the service `cloudless_multiroom_thermostat.experimental_report` to get it on demand (it then shows the stored prediction instead of making a new one).

The time is fixed at 21:00 because the night model measures the evening drop from 21:00.

## Data and privacy

Everything stays on your Home Assistant. Files are stored in `config/cloudless_multiroom_thermostat/<entry id>/`:

- `episodes.jsonl`, `nights.jsonl`, `gas_days.jsonl`: append-only, one JSON record per line.
- `state.json`, `night_state.json`: small state files.

Switching the features off stops all sampling; the files and your entity choices are kept, so switching back on continues where it left off. Delete the folder to start over.

### Coming from the pyscript tuner

The integration reads the same five files as the pyscript tuner ([`pyscript/` in v1.1.0](https://github.com/BarBaar44/thermostat/tree/v1.1.0/pyscript)), so its history carries over:

1. Stop the tuner (remove it from pyscript's `apps:` or disable pyscript) and **restart Home Assistant**. The tuner's sensors, such as `sensor.heating_gas_per_degree_day`, stay in the state machine until a restart. If one is still there when you enable the features, the integration's sensor gets `_2` instead of that entity ID.
2. Copy `episodes.jsonl`, `nights.jsonl`, `gas_days.jsonl`, `state.json` and `night_state.json` from the tuner's `data_dir` (default `config/heating_tuner`) into `config/cloudless_multiroom_thermostat/<entry id>/`.
3. Enable the experimental features. The integration appends to the copied files, and dashboards that used the tuner's gas sensor keep working when the system is called "Heating". The tuner's other sensors get new IDs, so dashboards using them need editing: `sensor.heating_tuner_last_overshoot` → `sensor.heating_last_warm_up_overshoot`, `sensor.heating_tuner_night_prediction` → `sensor.heating_night_prediction`, `sensor.heating_tuner_recommendation` → `sensor.heating_pid_advice` (for a system called "Heating").
