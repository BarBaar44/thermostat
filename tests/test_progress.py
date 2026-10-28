"""Progress of the experimental features towards stable."""

from __future__ import annotations

from custom_components.cloudless_multiroom_thermostat.experimental import progress

P1 = {"kp": 0.6, "ki": 0.002, "kd": 900}
P2 = {"kp": 0.6, "ki": 0.0018, "kd": 900}


def _night(day: int, error: float, outdoor: float) -> dict:
    return {"valid": True, "prediction_error": error, "outdoor_avg": outdoor, "end": f"2026-11-{day:02d}"}


def _ep(day: int, params: dict, overshoot: float, band: str = "0_8", room_class: str = "living") -> dict:
    return {
        "start": f"2026-11-{day:02d}T07:00:00",
        "valid": True,
        "room_class": room_class,
        "outdoor_band": band,
        "params": params,
        "overshoot": overshoot,
        "rise_min": 50,
    }


def test_gas_progress() -> None:
    assert "not set up" in progress.gas_progress(None)["text"]
    p = progress.gas_progress({"days": 40, "first": "2026-09-14"})
    assert p["text"].startswith("Gas per degree-day: 40 of 365 days of data (since 2026-09-14)")
    assert "rate" not in p["text"] and not p["done"]  # no boiler signals: no rate needed
    p = progress.gas_progress({"days": 365, "rate": {"rate": 0.019, "source": "default", "minutes": 20}})
    assert "still the default (20 of 60 minutes measured)" in p["text"] and not p["done"]
    p = progress.gas_progress({"days": 365, "rate": {"rate": 0.021, "source": "measured", "minutes": 80}})
    assert p["done"]


def test_night_progress_counts_and_errors() -> None:
    nights = [_night(1, 0.5, 4.0), _night(2, -0.2, 10.0), {"valid": False, "prediction_error": 3.0}]
    p = progress.night_progress(nights)
    assert "2 of 30 predicted nights, 2 of 3 outdoor bands" in p["text"]
    assert "average error 0.35" in p["text"] and "worst 0.5" in p["text"]
    assert not p["done"]
    assert "0 of 30" in progress.night_progress([])["text"]


def test_night_progress_done() -> None:
    temps = (-2.0, 4.0, 10.0)
    nights = [_night(1 + i % 28, 0.1, temps[i % 3]) for i in range(30)]
    assert progress.night_progress(nights)["done"]


def test_pid_progress_needs_change_by_hand() -> None:
    p = progress.pid_progress([_ep(1, P1, 0.6), _ep(2, P1, 0.5)])
    assert "0 of 2" in p["text"] and "no change applied by hand yet" in p["text"]


def test_pid_progress_change_improves() -> None:
    episodes = [_ep(1, P1, 0.6), _ep(2, P1, 0.5)] + [_ep(3 + i, P2, 0.2) for i in range(5)]
    episodes.append(_ep(9, P2, 0.9, room_class="other"))  # other rooms do not count
    p = progress.pid_progress(episodes)
    assert "1 of 2 outdoor bands confirmed" in p["text"]
    assert "0_8 5/5 better" in p["text"]
    assert not p["done"]


def test_pid_progress_change_not_better() -> None:
    episodes = [_ep(1, P1, 0.3), _ep(2, {**P2, "kp": 0.4}, 0.6)]
    episodes[1]["rise_min"] = 70
    assert "0_8 1/5 not better" in progress.pid_progress(episodes)["text"]


def test_report_lines() -> None:
    lines = progress.report_lines(None, [], [])
    assert len(lines) == 3 and all(line.startswith("- ") for line in lines)
