"""Read-only NAVEE telemetry decoding.

Ported from Volt (navee-offline) ``Telemetry.java``. Offsets follow the OEM app's numbering:
offset ``n`` is the ``n``-th byte AFTER the response status byte. A short or rejected reply
never invents zero readings or erases earlier data: a field that is absent stays ``None``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import protocol

# Driving-mode names observed on the UT5 MAX (model 2538). Other models assign other meanings.
UT5_MAX_PID = "2538"
UT5_MAX_DRIVING_MODES = {2: "eco", 3: "sport", 11: "walk"}

FIRMWARE_PARTS = ("meter", "controller", "battery", "screen", "uwb")


def _u(data: bytes, offset: int, length: int, big_endian: bool = False) -> int | None:
    if offset < 0 or offset + length > len(data):
        return None
    return int.from_bytes(data[offset : offset + length], "big" if big_endian else "little")


def _signed_magnitude(raw: int, sign_bit: int, separate_sign: int | None) -> int:
    magnitude = raw & (sign_bit - 1)
    negative = bool(raw & sign_bit) or separate_sign == 1
    return -magnitude if negative else magnitude


def _percent(value: int | None) -> int | None:
    return value if value is not None and value <= 100 else None


@dataclass(slots=True)
class NaveeState:
    """Latest decoded readings for one scooter connection."""

    serial: str | None = None
    firmware: dict[str, str] = field(default_factory=dict)

    # Battery
    battery_percent: int | None = None
    battery_status: int | None = None
    voltage_v: float | None = None
    current_a: float | None = None
    power_w: float | None = None
    temperature_c: int | None = None
    battery_health_percent: int | None = None
    charging_state: int | None = None
    charge_cycles: int | None = None
    rated_capacity_ah: float | None = None

    # Ride
    speed_kmh: float | None = None
    remaining_range_km: float | None = None
    trip_km: float | None = None
    trip_minutes: int | None = None
    max_speed_kmh: float | None = None
    average_speed_kmh: float | None = None
    odometer_km: float | None = None
    driving_status: int | None = None
    driving_mode: int | None = None

    # Diagnostics
    warning_code: int | None = None
    lock_status: int | None = None

    # Raw 0x70 vehicle settings (status byte excluded) and when it arrived (monotonic seconds).
    settings: bytes | None = None
    settings_at: float = 0.0
    # Motion facts from 0x91/0x92 reports, for the parked precondition.
    motion_at: float = 0.0
    motion_speed_raw: int = -1
    warning_at: float = 0.0

    # Internal electrical/sign bookkeeping.
    _raw_voltage: int | None = None
    _raw_current: int | None = None
    _raw_temperature: int | None = None
    _voltage_batch: int = -1
    _current_batch: int = -1
    _batch: int = 0
    _current_sign: int | None = None
    _temperature_sign: int | None = None

    @property
    def pid(self) -> str:
        return protocol.serial_pid(self.serial)

    @property
    def charging(self) -> bool | None:
        return None if self.charging_state is None else self.charging_state == 1

    def setting(self, offset: int) -> int | None:
        """One byte of the latest accepted 0x70 reply, or None when not reported."""
        if self.settings is None or offset < 0 or offset >= len(self.settings):
            return None
        return self.settings[offset]

    def setting_be32(self, offset: int) -> int | None:
        return None if self.settings is None else _u(self.settings, offset, 4, True)

    def status_fresh(self, max_age: float, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return self.settings is not None and 0 <= now - self.settings_at <= max_age

    def stationary(self, now: float | None = None) -> bool:
        """A motion report within six seconds says the scooter is stopped."""
        now = time.monotonic() if now is None else now
        return (
            self.motion_at > 0
            and 0 <= now - self.motion_at < 6
            and self.motion_speed_raw == 0
            and self.driving_status == 0
        )

    def motion_seen(self) -> bool:
        return self.motion_at > 0

    def warning_active(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return bool(self.warning_code) and now - self.warning_at < 6

    def driving_mode_label(self) -> str | None:
        if self.driving_mode is None:
            return None
        if self.pid == UT5_MAX_PID and self.driving_mode in UT5_MAX_DRIVING_MODES:
            return UT5_MAX_DRIVING_MODES[self.driving_mode]
        return f"mode_{self.driving_mode}"

    # ---- Decoding --------------------------------------------------------------------

    def accept(self, f: protocol.Frame, now: float | None = None) -> None:
        """Apply one inbound frame. Credentials and PIN replies are never retained."""
        now = time.monotonic() if now is None else now
        if f.error != 0:
            return
        data = f.payload
        if f.cmd == protocol.READ_VEHICLE:
            # Same sanity check the app uses before trusting a status frame for controls.
            if len(f.data) >= 14 and f.data[3] <= 1:
                self.settings = bytes(data)
                self.settings_at = now
                self.lock_status = data[2]
        elif f.cmd == protocol.READ_BATTERY:
            self._battery(data)
        elif f.cmd == protocol.READ_FIRMWARE:
            self._firmware(data)
        elif f.cmd == protocol.READ_SERIAL:
            serial = protocol.serial_from_frame(f)
            if serial:
                self.serial = serial
        elif f.cmd == protocol.READ_BATTERY_SIGN:
            self._signs(data)
        elif f.cmd == protocol.REPORT_HOME:
            self._home(data, now, len(f.data))
        elif f.cmd in (protocol.REPORT_RIDE_V0, protocol.REPORT_RIDE_V1):
            self._ride(data, f.cmd == protocol.REPORT_RIDE_V1, now, len(f.data))

    def _battery(self, d: bytes) -> None:
        if (v := _u(d, 0, 1)) is not None:
            self.battery_status = v
        if (v := _percent(_u(d, 1, 1))) is not None:
            self.battery_percent = v
        self._electrical(d, 2, 6, zero_means_unavailable=False)
        if (v := _percent(_u(d, 10, 1))) is not None:
            self.battery_health_percent = v
        if (v := _u(d, 11, 1)) is not None:
            self._raw_temperature = v
            self._refresh_signed()
        if (v := _u(d, 12, 1)) is not None:
            self.charging_state = v
        if (v := _u(d, 13, 2)) is not None:
            self.charge_cycles = v
        if (v := _u(d, 23, 2, True)) is not None:
            self.rated_capacity_ah = v / 100

    def _electrical(self, d: bytes, v_off: int, c_off: int, zero_means_unavailable: bool) -> None:
        self._batch += 1
        voltage = _u(d, v_off, 4)
        current = _u(d, c_off, 4)
        if voltage is not None and (not zero_means_unavailable or voltage != 0):
            self._raw_voltage = voltage
            self._voltage_batch = self._batch
            self.voltage_v = round(voltage / 1000, 3)
        if current is not None and (not zero_means_unavailable or bool(voltage) or current != 0):
            self._raw_current = current
            self._current_batch = self._batch
        self._refresh_signed()

    def _refresh_signed(self) -> None:
        if self._raw_current is not None:
            amps = _signed_magnitude(self._raw_current, 0x80000000, self._current_sign) / 1000
            self.current_a = round(amps, 3)
            # Never combine measurements from different reports into a power reading.
            if self._raw_voltage is not None and self._voltage_batch == self._current_batch:
                self.power_w = round(self._raw_voltage / 1000 * amps, 1)
        if self._raw_temperature is not None:
            self.temperature_c = _signed_magnitude(
                self._raw_temperature, 0x80, self._temperature_sign
            )

    def _signs(self, d: bytes) -> None:
        if (v := _u(d, 0, 1)) is not None:
            self._temperature_sign = v
        if (v := _u(d, 1, 1)) is not None:
            self._current_sign = v
        self._refresh_signed()

    def _firmware(self, d: bytes) -> None:
        for i, name in enumerate(FIRMWARE_PARTS):
            part = d[i * 4 : i * 4 + 4]
            if len(part) < 4:
                continue
            if all(32 <= b <= 126 for b in part):
                self.firmware[name] = ".".join(chr(b) for b in part)
            else:
                self.firmware[name] = part.hex()

    def _home(self, d: bytes, now: float, data_length: int) -> None:
        if (v := _u(d, 0, 1)) is not None:
            self.warning_code = v
            if data_length >= 9:
                self.warning_at = now
        if (v := _u(d, 1, 1)) is not None:
            self.driving_mode = v
        if (v := _percent(_u(d, 2, 1))) is not None:
            self.battery_percent = v
        if (v := _u(d, 3, 1)) is not None:
            self.battery_status = v
        if (v := _u(d, 4, 1)) is not None:
            self.charging_state = v
        if (v := _u(d, 6, 1)) is not None:
            self.remaining_range_km = v
        # 0 means "not supplied"; 1/2 are unlocked/locked.
        if (v := _u(d, 7, 1)) is not None and v > 0:
            self.lock_status = v - 1
        # All-zero electrical fields in this optional report mean absent.
        self._electrical(d, 8, 12, zero_means_unavailable=True)

    def _ride(self, d: bytes, expanded: bool, now: float, data_length: int) -> None:
        if (v := _percent(_u(d, 0, 1))) is not None:
            self.battery_percent = v
        if (v := _u(d, 1, 1)) is not None:
            self.driving_status = v
        if expanded:
            scale = 10
            speed = _u(d, 2, 2)
            if speed is not None:
                self.speed_kmh = speed / scale
            if (v := _u(d, 4, 1)) is not None:
                self.remaining_range_km = v
            if (v := _u(d, 5, 2)) is not None:
                self.trip_km = v / scale
            if (v := _u(d, 7, 1)) is not None:
                self.trip_minutes = v
            if (v := _u(d, 8, 2)) is not None:
                self.max_speed_kmh = v / scale
            if (v := _u(d, 10, 2)) is not None:
                self.average_speed_kmh = v / scale
            # The OEM app prefers the optional 32-bit odometer when present and nonzero.
            odo32 = _u(d, 14, 4)
            odo = odo32 if odo32 else _u(d, 12, 2)
            if odo is not None:
                self.odometer_km = odo / scale
            if data_length >= 5:
                self.motion_speed_raw = d[2] | (d[3] << 8)
                self.motion_at = now
        else:
            fields = (
                ("speed_kmh", 2),
                ("remaining_range_km", 3),
                ("trip_km", 4),
                ("trip_minutes", 5),
                ("max_speed_kmh", 6),
                ("average_speed_kmh", 7),
                ("odometer_km", 8),
            )
            for name, offset in fields:
                if (v := _u(d, offset, 1)) is not None:
                    setattr(self, name, v)
            if data_length >= 4:
                self.motion_speed_raw = d[2] * 10
                self.motion_at = now
