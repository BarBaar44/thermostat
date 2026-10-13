"""
heating_gas_core - gas per degree-day for the heating tuner
-------------------------------------------------------------
Plain Python with no pyscript or Home Assistant dependencies, like
heating_tuner_core. Observe only.

Gas use depends mostly on the weather, so raw m3 per day can't show whether a
change saved anything. Degree-days normalise for that: a day with an average
outdoor temperature of 8 degC counts 18 - 8 = 10 degree-days, a day at or
above 18 degC counts 0.

Not all gas is heating: hot water and cooking use some every day. That base
is estimated as the median gas use on days without heating demand (at most
BASE_MAX_DD degree-days, i.e. summer) and subtracted before dividing.

    heating gas per degree-day = sum(gas - base) / sum(degree-days)

over the last 7 and 28 complete days. Single days are too noisy in mild
weather (a shower more or less outweighs the heating), so only the rolling
values are reported, and only once the window holds MIN_WINDOW_DD degree-days.
"""

BASE_TEMP = 18.0       # degree-day base temperature (degC)
BASE_MAX_DD = 0.5      # days at or below this count as "no heating" for the base
MIN_BASE_DAYS = 5      # need this many no-heating days for a base estimate
MIN_WINDOW_DD = 5.0    # a window needs this many degree-days for a value
MAX_DAY_GAS = 20.0     # m3; larger daily changes are meter glitches


def degree_days(outdoor_mean):
    return max(BASE_TEMP - outdoor_mean, 0.0)


def build_days(gas_by_date, outdoor_by_date, today):
    """
    Merge daily gas use and daily mean outdoor temperature (both keyed by
    local ISO date) into a sorted list of complete days before `today`.
    Days missing either value, or with an implausible gas change, are left out.
    """
    days = []
    for date in sorted(set(gas_by_date) & set(outdoor_by_date)):
        gas, outdoor = gas_by_date[date], outdoor_by_date[date]
        if date >= today or gas is None or outdoor is None:
            continue
        if gas < 0 or gas > MAX_DAY_GAS:
            continue
        days.append({"date": date, "gas": round(gas, 3), "outdoor": round(outdoor, 1),
                     "dd": round(degree_days(outdoor), 1)})
    return days


def _median(values):
    values = sorted(values)
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def base_gas(days):
    """Median daily gas on no-heating days (hot water + cooking), or None."""
    summer = [d["gas"] for d in days if d["dd"] <= BASE_MAX_DD]
    if len(summer) < MIN_BASE_DAYS:
        return None, len(summer)
    return _median(summer), len(summer)


def _window(days, n, base):
    last = days[-n:]
    dd = sum([d["dd"] for d in last])
    gas = sum([d["gas"] for d in last])
    heating = sum([max(d["gas"] - base, 0.0) for d in last])
    value = round(heating / dd, 3) if dd >= MIN_WINDOW_DD else None
    return {"days": len(last), "dd": round(dd, 1), "gas": round(gas, 2),
            "heating_gas": round(heating, 2), "per_dd": value}


def summary(days):
    """Rolling 7- and 28-day heating gas per degree-day, plus the last day."""
    base, n_base = base_gas(days)
    out = {"base": round(base, 3) if base is not None else None, "base_days": n_base,
           "last": days[-1] if days else None, "w7": None, "w28": None}
    if base is None or not days:
        return out
    out["w7"] = _window(days, 7, base)
    out["w28"] = _window(days, 28, base)
    return out


def summary_line(s):
    """One human-readable line for the daily notification."""
    if s["base"] is None:
        return "- Gas per degree-day: no baseline yet ({}/{} days without heating)".format(
            s["base_days"], MIN_BASE_DAYS)
    last = s["last"]
    line = "- Gas {}: {:.2f} m3 at {} degree-days".format(last["date"], last["gas"], last["dd"])

    def fmt(w):
        if w["per_dd"] is None:
            return "n/a ({} dd)".format(w["dd"])
        return "{:.3f} m3/dd".format(w["per_dd"])

    line += "; heating gas per degree-day 7 days {}, 28 days {} (hot water/cooking base {:.2f} m3/day)".format(
        fmt(s["w7"]), fmt(s["w28"]), s["base"])
    return line
