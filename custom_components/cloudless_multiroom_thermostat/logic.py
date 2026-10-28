"""Pure heating logic, no Home Assistant imports, so it is unit testable."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoomDemand:
    """What one room asks for right now."""

    room_id: str
    name: str
    heating: bool  # effective mode is heat (not off, paused or away-off)
    target: float | None
    current: float | None
    drives: bool = True  # False: heats along, never drives the heat source


@dataclass(frozen=True)
class Winner:
    """Result of the Tier 1 selection."""

    room_id: str | None  # None means "Off"
    name: str
    target: float
    current: float | None
    delta: float


def select_winner(
    rooms: list[RoomDemand], off_temp: float, fallback_current: float | None
) -> Winner:
    """Pick the room that drives the heat source.

    Rule 1+2: among heating rooms, the largest positive demand (target minus current).
    Rule 3: with no positive demand, the largest overshoot only fills current/delta.
    Rule 0: no genuine demand means "Off" with the off temperature.
    Rooms that don't drive the heat source never count.
    """
    best_delta = -999.0
    best_dev = 0.0
    best: RoomDemand | None = None

    for room in rooms:
        if not room.heating or not room.drives or room.target is None or room.current is None:
            continue
        delta = round(room.target - room.current, 1)
        dev = abs(delta)
        if delta > 0 and delta > best_delta:
            best_delta, best_dev, best = delta, dev, room
        elif best_delta <= 0 and dev > best_dev:
            best_delta, best_dev, best = delta, dev, room

    if best is None:
        return Winner(None, "Off", off_temp, fallback_current, 0.0)
    if best_delta <= 0:
        return Winner(None, "Off", off_temp, best.current, best_delta)
    return Winner(best.room_id, best.name, best.target, best.current, best_delta)


def switch_should_run(
    rooms: list[RoomDemand], currently_on: bool, hysteresis: float
) -> bool:
    """On/off heat source: start at hysteresis below target, stop at target."""
    deltas = [
        r.target - r.current
        for r in rooms
        if r.heating and r.drives and r.target is not None and r.current is not None
    ]
    if not deltas:
        return False
    worst = max(deltas)
    if currently_on:
        return worst > 0
    return worst >= hysteresis


def round_to_step(value: float, step: float | None) -> float:
    """Round to a device step (0.5 for most TRVs), falling back to 0.1."""
    if not step or step <= 0:
        step = 0.1
    return round(round(value / step) * step, 2)


def clamp(value: float, low: float | None, high: float | None) -> float:
    """Clamp to device limits when they are known."""
    if low is not None:
        value = max(value, low)
    if high is not None:
        value = min(value, high)
    return value


def trv_target(
    room_target: float,
    is_winner: bool,
    buffer: float,
    step: float | None,
    low: float | None,
    high: float | None,
) -> float:
    """Target to send to a TRV: buffer only for the winning room."""
    value = room_target + (buffer if is_winner else 0.0)
    return clamp(round_to_step(value, step), low, high)
