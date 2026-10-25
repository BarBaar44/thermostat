"""Pure logic tests (winner selection, switch hysteresis, TRV targets)."""

from custom_components.cloudless_multiroom_thermostat.logic import (
    RoomDemand,
    round_to_step,
    select_winner,
    switch_should_run,
    trv_target,
)


def room(rid, heating=True, target=20.0, current=19.0):
    return RoomDemand(rid, rid.title(), heating, target, current)


def test_largest_positive_demand_wins():
    w = select_winner([room("a", target=20, current=19), room("b", target=21, current=18)], 10, None)
    assert w.room_id == "b" and w.target == 21 and w.delta == 3


def test_rooms_not_heating_are_ignored():
    w = select_winner([room("a", heating=False, target=25, current=15), room("b")], 10, None)
    assert w.room_id == "b"


def test_rule0_overshoot_only_reports_off():
    w = select_winner([room("a", target=18, current=19.5)], 10, None)
    assert w.room_id is None and w.name == "Off" and w.target == 10
    assert w.current == 19.5 and w.delta == -1.5


def test_no_rooms_keeps_fallback_current():
    w = select_winner([], 10, 17.2)
    assert w.room_id is None and w.current == 17.2


def test_missing_temperature_has_no_demand():
    w = select_winner([room("a", current=None)], 10, None)
    assert w.room_id is None


def test_room_that_does_not_drive_never_wins():
    hall = RoomDemand("hall", "Hall", True, 20.0, 15.0, drives=False)
    assert select_winner([hall, room("a", target=20, current=19.5)], 10, None).room_id == "a"
    assert select_winner([hall], 10, None).room_id is None
    assert switch_should_run([hall], currently_on=False, hysteresis=0.3) is False
    assert switch_should_run([hall], currently_on=True, hysteresis=0.3) is False


def test_switch_hysteresis():
    rooms = [room("a", target=20, current=19.8)]
    assert switch_should_run(rooms, currently_on=False, hysteresis=0.3) is False
    assert switch_should_run(rooms, currently_on=True, hysteresis=0.3) is True
    assert switch_should_run([room("a", target=20, current=20)], True, 0.3) is False
    assert switch_should_run([room("a", target=20, current=19.7)], False, 0.3) is True


def test_trv_target_buffer_step_and_clamp():
    assert trv_target(20, True, 1.0, 0.5, 5, 30) == 21
    assert trv_target(20, False, 1.0, 0.5, 5, 30) == 20
    assert trv_target(20.3, False, 0, 0.5, 5, 30) == 20.5
    assert trv_target(29.5, True, 1.0, 0.5, 5, 30) == 30
    assert round_to_step(19.26, None) == 19.3
