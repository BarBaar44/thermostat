"""autotune: apply the PID advice, keep it when it helps, revert it when not."""

from __future__ import annotations

from custom_components.cloudless_multiroom_thermostat.experimental import autotune, tuner_core

P = {"kp": 1.75, "ki": 0.0075, "kd": 0.0}


def _ep(start: str, params: dict, overshoot: float = 0.8, band: str = "0_8", **extra) -> dict:
    return {
        "start": f"2026-11-{start}:00+01:00", "valid": True, "room_class": "living", "outdoor_band": band,
        "overshoot": overshoot, "flame_starts_per_hour": 1.0, "undershoot": 0.0, "rise_min": 30,
        "max_ch": 40.0, "schema": 2, "params": dict(params), **extra,
    }


def _rec(history, params):
    return tuner_core.recommend(history, params)


def test_applies_advice_based_on_the_current_values() -> None:
    history = [_ep("01T06:30", P), _ep("02T06:30", P)]
    action, state = autotune.decide(history, P, _rec(history, P), {}, "2026-11-02T09:00:00+01:00")
    assert action == {"action": "apply", "param": "ki", "value": 0.00675, "old": 0.0075, "why": "overshoot above 0.4 degC"}
    pending = state["pending"]
    assert pending["params_after"] == {**P, "ki": 0.00675} and pending["before"] == {"overshoot": 0.8}
    assert state["log"][-1]["action"] == "applied"


def test_waits_for_warm_ups_on_the_new_values() -> None:
    history = [_ep("01T06:30", P), _ep("02T06:30", P)]
    _, state = autotune.decide(history, P, _rec(history, P), {}, "2026-11-02T09:00:00+01:00")
    new = {**P, "ki": 0.00675}
    history.append(_ep("03T06:30", new, overshoot=0.7))
    # The advice now rests on one old and one new episode: no further change
    action, state = autotune.decide(history, new, _rec(history, new), state, "2026-11-03T09:00:00+01:00")
    assert action is None and state["pending"] is not None


def test_keeps_a_change_that_helps_and_moves_on() -> None:
    history = [_ep("01T06:30", P), _ep("02T06:30", P)]
    _, state = autotune.decide(history, P, _rec(history, P), {}, "2026-11-02T09:00:00+01:00")
    new = {**P, "ki": 0.00675}
    history += [_ep("03T06:30", new, overshoot=0.6), _ep("04T06:30", new, overshoot=0.5)]
    action, state = autotune.decide(history, new, _rec(history, new), state, "2026-11-04T09:00:00+01:00")
    # Kept, and the advice (still overshoot, now on the new values) takes the next step
    assert [e["action"] for e in state["log"]] == ["applied", "kept", "applied"]
    assert action["action"] == "apply" and action["value"] == round(0.00675 * 0.9, 5)


def test_reverts_a_change_that_does_not_help_and_never_tries_it_again() -> None:
    history = [_ep("01T06:30", P), _ep("02T06:30", P)]
    _, state = autotune.decide(history, P, _rec(history, P), {}, "2026-11-02T09:00:00+01:00")
    new = {**P, "ki": 0.00675}
    history += [_ep("03T06:30", new, overshoot=0.9), _ep("04T06:30", new, overshoot=0.8)]
    action, state = autotune.decide(history, new, _rec(history, new), state, "2026-11-04T09:00:00+01:00")
    assert action == {"action": "revert", "param": "ki", "value": 0.0075, "old": 0.00675, "why": "not better after the change"}
    assert state["pending"] is None and state["reverted"] == [{"param": "ki", "old": 0.0075, "new": 0.00675}]
    # Back on the old values the same advice comes back: not applied again
    history += [_ep("05T06:30", P), _ep("06T06:30", P)]
    rec = _rec(history, P)
    assert rec["new"] == 0.00675 and autotune.is_reverted(state, rec)
    action, state = autotune.decide(history, P, rec, state, "2026-11-06T09:00:00+01:00")
    assert action is None and state["pending"] is None


def test_other_bands_and_limits_do_not_count_for_the_check() -> None:
    history = [_ep("01T06:30", P), _ep("02T06:30", P)]
    _, state = autotune.decide(history, P, _rec(history, P), {}, "2026-11-02T09:00:00+01:00")
    new = {**P, "ki": 0.00675}
    history += [_ep("03T06:30", new, overshoot=2.0, band="below_0"), _ep("04T06:30", new, max_ch=50.0)]
    action, state = autotune.decide(history, new, None, state, "2026-11-04T09:00:00+01:00")
    assert action is None and state["pending"] is not None
    assert "0 of 2 warm-ups in band 0_8" in autotune.status_line(state, history)


def test_values_changed_by_hand_end_the_check() -> None:
    history = [_ep("01T06:30", P), _ep("02T06:30", P)]
    _, state = autotune.decide(history, P, _rec(history, P), {}, "2026-11-02T09:00:00+01:00")
    by_hand = {**P, "kp": 1.5}
    action, state = autotune.decide(history, by_hand, None, state, "2026-11-03T09:00:00+01:00")
    assert action is None and state["pending"] is None and state["log"][-1]["action"] == "dropped"


def test_float32_read_back_counts_as_the_written_value() -> None:
    history = [_ep("01T06:30", P), _ep("02T06:30", P)]
    _, state = autotune.decide(history, P, _rec(history, P), {}, "2026-11-02T09:00:00+01:00")
    read_back = {**P, "ki": 0.006750000175088644}
    action, state = autotune.decide(history, read_back, None, state, "2026-11-03T09:00:00+01:00")
    assert action is None and state["pending"] is not None


def test_no_action_without_usable_advice() -> None:
    assert autotune.decide([], P, None, {}, "2026-11-02T09:00:00+01:00")[0] is None
    at_limit = {"param": "ki", "old": 0.00125, "new": 0.00125, "at_limit": True}
    assert autotune.decide([], P, at_limit, {}, "2026-11-02T09:00:00+01:00")[0] is None


def test_improved_needs_the_verdicts_own_metric() -> None:
    assert autotune.improved("overshoot", {"overshoot": 0.8}, {"overshoot": 0.5})
    assert not autotune.improved("overshoot", {"overshoot": 0.8}, {"overshoot": 0.8})
    assert autotune.improved("oscillating", {"flame_starts_per_hour": 8, "undershoot": 0.5}, {"flame_starts_per_hour": 6, "undershoot": 0.5})
    assert not autotune.improved(
        "oscillating", {"flame_starts_per_hour": 8, "undershoot": 0.5}, {"flame_starts_per_hour": 6, "undershoot": 0.7}
    )


def test_log_lines_for_the_report() -> None:
    history = [_ep("01T06:30", P), _ep("02T06:30", P)]
    _, state = autotune.decide(history, P, _rec(history, P), {}, "2026-11-02T09:00:00+01:00")
    assert autotune.log_lines(state, "2026-11-02") == ["- 09:00 applied: ki 0.0075 -> 0.00675 (overshoot above 0.4 degC)"]
    assert autotune.log_lines(state, "2026-11-03") == []


def test_top_ups_do_not_count_for_the_check() -> None:
    history = [_ep("01T06:30", P), _ep("02T06:30", P)]
    _, state = autotune.decide(history, P, _rec(history, P), {}, "2026-11-02T09:00:00+01:00")
    new = {**P, "ki": 0.00675}
    history += [_ep(f"03T1{h}:00", new, overshoot=0.9, target=18.0, start_temp=17.85) for h in range(4)]
    action, state = autotune.decide(history, new, None, state, "2026-11-03T15:00:00+01:00")
    assert action is None and state["pending"] is not None


def test_a_check_without_verdict_ends_after_14_days_and_keeps_the_change() -> None:
    history = [_ep("01T06:30", P), _ep("02T06:30", P)]
    _, state = autotune.decide(history, P, _rec(history, P), {}, "2026-11-02T09:00:00+01:00")
    new = {**P, "ki": 0.00675}
    history.append(_ep("03T06:30", new, overshoot=0.9))
    action, state = autotune.decide(history, new, None, state, "2026-11-15T09:00:00+01:00")
    assert action is None and state["pending"] is not None  # 13 days: still waiting
    action, state = autotune.decide(history, new, None, state, "2026-11-16T09:00:00+01:00")
    assert action is None and state["pending"] is None and state["reverted"] == []
    assert state["log"][-1]["action"] == "inconclusive" and "1 of 2 warm-ups" in state["log"][-1]["why"]
