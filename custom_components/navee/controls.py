"""Named, bounded scooter controls.

Ported from Volt (navee-offline) ``ScooterCommands.java``. Option domains and readback offsets
are Volt's UT5 MAX (model 2538) layer; only controls Volt verified on a parked UT5 MAX are
included. Other models get the shared lock only, because their encodings differ per model.

Readback offsets use OEM numbering (after the 0x70 status byte).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from . import protocol


class Kind(Enum):
    TOGGLE = "toggle"
    CHOICE = "choice"
    SLIDER = "slider"


@dataclass(frozen=True, slots=True)
class Control:
    key: str
    kind: Kind
    cmd: int
    offset: int
    """Readback byte in the 0x70 reply (OEM numbering)."""
    prefix: tuple[int, ...] = ()
    """Selector bytes sent before the value (0x6e/0x6f/0x81 selector, 0x6d target and sub)."""
    options: dict[int, str] | None = None
    """Wire value -> option key, for choices."""
    minimum: int = 0
    maximum: int = 1
    unit: str | None = None
    mask: int = 0xFF
    shift: int = 0
    parked: bool = False
    """Refused unless a recent motion report shows the scooter stopped."""
    battery: bool = False
    """Also re-read the battery after a change."""

    def readback(self, settings: bytes | None) -> int | None:
        if settings is None or self.offset >= len(settings):
            return None
        return (settings[self.offset] & self.mask) >> self.shift

    def allows(self, value: int) -> bool:
        if self.options is not None:
            return value in self.options
        return self.minimum <= value <= self.maximum

    def encode(self, value: int) -> bytes:
        if not self.allows(value):
            raise protocol.ProtocolError("Value is outside the feature domain")
        if not self.prefix:
            return protocol.scalar(self.cmd, value)
        if len(self.prefix) == 1:
            return protocol.selector(self.cmd, self.prefix[0], value)
        return protocol.light(self.prefix[0], self.prefix[1], value)


def _toggle(key: str, cmd: int, offset: int, *, parked: bool, **kw) -> Control:
    return Control(key, Kind.TOGGLE, cmd, offset, parked=parked, **kw)


LOCK = _toggle("lock", protocol.LOCK, 2, parked=True)
"""Shared by every standard model (OEM home slider)."""

UT5_MAX_CONTROLS: dict[str, Control] = {
    c.key: c
    for c in (
        LOCK,
        _toggle("cruise_control", 0x52, 3, parked=True),
        Control(
            "energy_recovery",
            Kind.CHOICE,
            0x53,
            5,
            options={0: "off", 30: "low", 60: "medium", 90: "high"},
            parked=True,
        ),
        # OEM DeviceLightType.TailLight: 0x54, read back at offset 4.
        _toggle("tail_light", 0x54, 4, parked=False),
        _toggle("auto_lights", 0x57, 8, parked=False),
        _toggle("traction_control", 0x5F, 11, parked=True),
        _toggle("turn_signal_sound", 0x60, 12, parked=False),
        Control(
            "display_units",
            Kind.CHOICE,
            0x55,
            7,
            options={0: "imperial", 1: "metric"},
        ),
        Control("kick_start_speed", Kind.SLIDER, 0x6A, 19, maximum=5, unit="km/h", parked=True),
        Control(
            "maximum_speed",
            Kind.CHOICE,
            0x6E,
            25,
            prefix=(1,),
            options={25: "25", 32: "32", 40: "40", 50: "50"},
            parked=True,
        ),
        Control(
            "charge_limit",
            Kind.CHOICE,
            0x6F,
            31,
            prefix=(4,),
            options={80: "80", 85: "85", 90: "90", 95: "95", 100: "100"},
            parked=True,
            battery=True,
        ),
        Control(
            "lock_duration",
            Kind.CHOICE,
            0x6F,
            34,
            prefix=(2,),
            options={1: "6h", 2: "12h", 3: "24h"},
            parked=True,
        ),
        _toggle("low_battery_mode", 0x6F, 32, prefix=(5,), parked=True, battery=True),
        _toggle("walk_assist", 0x81, 37, prefix=(1,), mask=1, shift=0, parked=True),
        _toggle("power_assist", 0x81, 37, prefix=(2,), mask=2, shift=1, parked=True),
        _toggle("hill_descent_control", 0x81, 37, prefix=(3,), mask=4, shift=2, parked=True),
        _toggle("line_lights", 0x6D, 10, prefix=(4, 0), mask=0x80, shift=7, parked=False),
        _toggle("star_lights", 0x6D, 17, prefix=(5, 0), mask=0x80, shift=7, parked=False),
    )
}

SHARED_CONTROLS: dict[str, Control] = {"lock": LOCK}


def controls_for(pid: str) -> dict[str, Control]:
    """Controls with a known encoding for this model ID."""
    return UT5_MAX_CONTROLS if pid == "2538" else SHARED_CONTROLS
