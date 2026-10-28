"""
heating_gas_core - gas per degree-day for the heating tuner
-------------------------------------------------------------
Plain Python with no pyscript or Home Assistant dependencies, like
heating_tuner_core. Observe only.

Gas use depends mostly on the weather, so raw m3 per day can't show whether a
change saved anything. Degree-days normalise for that: a day with an average
outdoor temperature of 8 degC counts 18 - 8 = 10 degree-days, a day at or
above 18 degC counts 0.

Not all gas is heating: the boiler also heats shower water every day (cooking
is electric, the kitchen tap has its own hot water). Each day is split into
hot-water gas and heating gas from the hourly gas use and the boiler's
heating and hot-water signals (split_day). Home Assistant keeps those signals
for 7 days only, so the split is stored per day as it is made. Days from
before the split was stored fall back to a base: the median gas use on days
without heating demand (at most BASE_MAX_DD degree-days, i.e. summer),
subtracted before dividing. Once MIN_BASE_DAYS days are split, the base
shown is the median measured hot-water gas instead.

    heating gas per degree-day = sum(heating gas) / sum(degree-days)

over the last 7 and 28 complete days. Single days are too noisy in mild
weather (a shower more or less outweighs the heating), so only the rolling
values are reported, and only once the window holds MIN_WINDOW_DD degree-days.

Days when nobody was home for the whole calendar day ("away", see
nobody_home) - so only during an absence of more than 24 hours, never a
normal day out at work or school - are left out of the windows and the
hot-water base: the heating schedule switches the heating off when everyone
leaves and nobody showers, so they would count degree-days with no gas and
make both look better than they are. The windows are then
the last 7 and 28 days at home. The day you come back stays in - warming the
house up again is real gas the trip cost.
"""

BASE_TEMP = 18.0       # degree-day base temperature (degC)
BASE_MAX_DD = 0.5      # days at or below this count as "no heating" for the base
MIN_BASE_DAYS = 5      # need this many no-heating days for a base estimate
MIN_WINDOW_DD = 5.0    # a window needs this many degree-days for a value
MAX_DAY_GAS = 20.0     # m3; larger daily changes are meter glitches
DHW_RATE = 0.019       # m3 per hot-water minute until measured (one boiler, 2026-10, ~11 kW)
BASE_RECENT_DAYS = 28  # measured base: median of this many latest split days
RATE_MIN_DRAW = 3.0    # hot-water minutes a draw needs to count for the measured rate
RATE_MIN_MINUTES = 60  # measured rate needs this many hot-water minutes in total
RATE_RECENT_DAYS = 60  # measured rate: samples from this many latest split days
RATE_RANGE = (0.005, 0.06)  # m3/min; a measured rate outside this is ignored


def degree_days(outdoor_mean):
    return max(BASE_TEMP - outdoor_mean, 0.0)


def on_minutes(changes, start, end):
    """
    Minutes a signal was on between start and end. `changes` is a sorted list
    of (time, is_on); the first entry is the state at or before `start`.
    """
    total = 0.0
    for i, (t, is_on) in enumerate(changes):
        if not is_on:
            continue
        until = changes[i + 1][0] if i + 1 < len(changes) else end
        seconds = (min(until, end) - max(t, start)).total_seconds()
        if seconds > 0:
            total += seconds
    return total / 60.0


def nobody_home(changes, start, end):
    """
    True if nobody was home between start and end. `changes` is a sorted list
    of (time, people) from zone.home; the first entry is the state at or before
    `start`. people is None when the state wasn't a number (unavailable around
    a restart) - that counts neither way, but at least one known count is needed.
    """
    seen = False
    for i, (t, people) in enumerate(changes):
        until = changes[i + 1][0] if i + 1 < len(changes) else end
        if until <= start or t >= end or people is None:
            continue
        if people > 0:
            return False
        seen = True
    return seen


def split_day(hours, dhw_rate=DHW_RATE):
    """
    Split one day's gas into hot water and heating. `hours` is a list of
    {"gas": m3, "ch": min, "dhw": min, "flame": min} per hour: minutes with
    heating demand, with hot water active and with the burner on.

    An hour without heating demand burned only for hot water. In an hour with
    heating demand the hot-water part is its hot-water minutes at `dhw_rate`
    (m3 per hot-water minute: the setting, the measured rate or DHW_RATE).
    Gas in an hour without any burner or heating time is meter lag and goes
    with the hour before.
    """
    dhw = heating = 0.0
    previous_heating = False
    for h in hours:
        gas = max(h["gas"] or 0.0, 0.0)
        if h["flame"] == 0 and h["ch"] == 0:
            heating_hour = previous_heating
        else:
            heating_hour = h["ch"] > 0
        if heating_hour:
            part = min(h["dhw"] * dhw_rate, gas)
            dhw += part
            heating += gas - part
        else:
            dhw += gas
        previous_heating = heating_hour
    rate_gas, rate_min = rate_sample(hours)
    return {"dhw_gas": round(dhw, 3), "heating_gas": round(heating, 3),
            "ch_min": round(sum([h["ch"] for h in hours])),
            "dhw_min": round(sum([h["dhw"] for h in hours])),
            "flame_min": round(sum([h["flame"] for h in hours])),
            "dhw_rate": dhw_rate, "rate_gas": round(rate_gas, 3), "rate_min": round(rate_min, 1)}


def rate_sample(hours):
    """
    Gas and hot-water minutes of the draws in one day that can measure the
    hot water gas rate: (gas m3, minutes).

    A draw is a run of hours with burner or hot-water time and no heating
    demand. It counts only with a quiet hour (no heating, no burner, no hot
    water) on both sides, so no heating gas leaks in and gas the meter reports
    an hour late still lands in the quiet hour after it, which is included.
    Draws under RATE_MIN_DRAW minutes (washing hands) are too noisy and left out.
    """
    def quiet(h):
        return h["ch"] == 0 and h["flame"] == 0 and h["dhw"] == 0

    gas = minutes = 0.0
    i = 1
    while i < len(hours) - 1:
        if quiet(hours[i]) or not quiet(hours[i - 1]):
            i += 1
            continue
        j = i
        while j < len(hours) and not quiet(hours[j]) and hours[j]["ch"] == 0:
            j += 1
        # hours[i:j] is the draw; hours[j] must be the quiet hour after it
        if j < len(hours) and quiet(hours[j]):
            draw = sum(h["dhw"] for h in hours[i:j])
            if draw >= RATE_MIN_DRAW:
                gas += sum(max(h["gas"] or 0.0, 0.0) for h in hours[i:j + 1])
                minutes += draw
        i = j + 1
    return gas, minutes


def measured_rate(split_by_date):
    """
    Hot water gas rate measured from the stored split days: total draw gas over
    total draw minutes of the latest RATE_RECENT_DAYS days with samples.
    Returns (rate or None, minutes); None until RATE_MIN_MINUTES are collected
    or when the result is implausible.
    """
    days = [split_by_date[d] for d in sorted(split_by_date) if split_by_date[d].get("rate_min")]
    days = days[-RATE_RECENT_DAYS:]
    gas = sum(d["rate_gas"] for d in days)
    minutes = sum(d["rate_min"] for d in days)
    if minutes < RATE_MIN_MINUTES:
        return None, round(minutes)
    rate = gas / minutes
    if not RATE_RANGE[0] <= rate <= RATE_RANGE[1]:
        return None, round(minutes)
    return round(rate, 4), round(minutes)


def build_days(gas_by_date, outdoor_by_date, today, split_by_date=None):
    """
    Merge daily gas use and daily mean outdoor temperature (both keyed by
    local ISO date) into a sorted list of complete days before `today`.
    Days missing either value, or with an implausible gas change, are left out.
    Days in `split_by_date` (stored split_day results) get "dhw_gas" and
    "heating_gas", and "away" when nobody was home all day.
    """
    split_by_date = split_by_date or {}
    days = []
    for date in sorted(set(gas_by_date) & set(outdoor_by_date)):
        gas, outdoor = gas_by_date[date], outdoor_by_date[date]
        if date >= today or gas is None or outdoor is None:
            continue
        if gas < 0 or gas > MAX_DAY_GAS:
            continue
        day = {"date": date, "gas": round(gas, 3), "outdoor": round(outdoor, 1),
               "dd": round(degree_days(outdoor), 1)}
        split = split_by_date.get(date)
        if split and "dhw_gas" in split:
            day["dhw_gas"] = split["dhw_gas"]
            day["heating_gas"] = split["heating_gas"]
        if split and split.get("away"):
            day["away"] = True
        days.append(day)
    return days


def _median(values):
    values = sorted(values)
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def base_gas(days):
    """
    Daily hot-water gas: the median measured split of the latest days, else
    the median gas on no-heating days (summer). Returns (base, days, source)
    with base None when neither has MIN_BASE_DAYS days.
    """
    measured = [d["dhw_gas"] for d in days if "dhw_gas" in d][-BASE_RECENT_DAYS:]
    if len(measured) >= MIN_BASE_DAYS:
        return _median(measured), len(measured), "measured"
    summer = [d["gas"] for d in days if d["dd"] <= BASE_MAX_DD]
    if len(summer) < MIN_BASE_DAYS:
        return None, len(summer), "summer"
    return _median(summer), len(summer), "summer"


def _heating_gas(day, base):
    if "heating_gas" in day:
        return day["heating_gas"]
    return max(day["gas"] - base, 0.0)


def _window(days, n, base):
    last = days[-n:]
    dd = sum([d["dd"] for d in last])
    gas = sum([d["gas"] for d in last])
    heating = sum([_heating_gas(d, base) for d in last])
    value = round(heating / dd, 3) if dd >= MIN_WINDOW_DD else None
    return {"days": len(last), "dd": round(dd, 1), "gas": round(gas, 2),
            "heating_gas": round(heating, 2), "per_dd": value}


def summary(days):
    """
    Rolling 7- and 28-day heating gas per degree-day over the days at home,
    plus the last day (which may be an away day).
    """
    home = [d for d in days if not d.get("away")]
    base, n_base, source = base_gas(home)
    out = {"base": round(base, 3) if base is not None else None, "base_days": n_base,
           "base_source": source, "last": days[-1] if days else None, "w7": None, "w28": None,
           "away_28": len([d for d in days[-28:] if d.get("away")]),
           "days": len(days), "first": days[0]["date"] if days else None}
    if base is None or not home:
        return out
    out["w7"] = _window(home, 7, base)
    out["w28"] = _window(home, 28, base)
    return out


def summary_line(s):
    """One human-readable line for the daily notification."""
    if s["base"] is None:
        return "- Gas per degree-day: no baseline yet ({}/{} days without heating)".format(
            s["base_days"], MIN_BASE_DAYS)
    last = s["last"]
    line = "- Gas {}: {:.2f} m3 at {} degree-days".format(last["date"], last["gas"], last["dd"])
    if last.get("away"):
        line += " (nobody home, not counted)"
    elif "dhw_gas" in last:
        line += " ({:.2f} hot water, {:.2f} heating)".format(last["dhw_gas"], last["heating_gas"])

    def fmt(w):
        if w["per_dd"] is None:
            return "n/a ({} dd)".format(w["dd"])
        return "{:.3f} m3/dd".format(w["per_dd"])

    line += "; heating gas per degree-day 7 days {}, 28 days {} (shower hot water {} {:.2f} m3/day)".format(
        fmt(s["w7"]), fmt(s["w28"]), s["base_source"], s["base"])
    if s["away_28"]:
        line += "; {} away day{} in the last 28 left out".format(
            s["away_28"], "" if s["away_28"] == 1 else "s")
    return line + rate_line(s.get("rate"))


def rate_line(rate):
    """Which hot water gas rate the split uses, for the daily notification."""
    if not rate:
        return ""
    if rate["source"] == "setting":
        return "; hot water rate {:.4f} m3/min (setting)".format(rate["rate"])
    if rate["source"] == "measured":
        return "; hot water rate {:.4f} m3/min measured over {} min".format(rate["rate"], rate["minutes"])
    return "; hot water rate {:.4f} m3/min default, measuring ({}/{} min)".format(
        rate["rate"], rate["minutes"], RATE_MIN_MINUTES)
