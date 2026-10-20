"""Telemetry vectors shared with Volt's TelemetryTest.java."""

import pytest

from custom_components.navee import protocol
from custom_components.navee.telemetry import NaveeState


def frame(cmd: int, payload: bytes, status: int = 0) -> protocol.Frame:
    return protocol.Frame(cmd, 0, bytes((status,)) + payload)


def write(buf: bytearray, offset: int, value: int, length: int, big: bool = False) -> None:
    buf[offset : offset + length] = value.to_bytes(length, "big" if big else "little")


def battery_fixture() -> bytearray:
    d = bytearray(36)
    d[1] = 67
    write(d, 2, 52345, 4)
    write(d, 6, 0x800004D2, 4)
    d[10], d[11], d[12] = 97, 0x85, 1
    write(d, 13, 0x1234, 2)
    write(d, 23, 1350, 2, True)
    return d


def test_battery_units_endianness_and_signs() -> None:
    s = NaveeState()
    s.accept(frame(0x72, bytes(battery_fixture())))
    assert s.battery_percent == 67
    assert s.voltage_v == pytest.approx(52.345)
    assert s.current_a == pytest.approx(-1.234)
    assert s.power_w == pytest.approx(-64.59, abs=0.05)
    assert s.temperature_c == -5
    assert s.battery_health_percent == 97
    assert s.charge_cycles == 0x1234
    assert s.rated_capacity_ah == pytest.approx(13.5)
    assert s.charging is True


def test_rejected_and_invalid_replies_keep_data() -> None:
    s = NaveeState()
    s.accept(frame(0x72, bytes((0, 54))))
    s.accept(frame(0x72, bytes((0, 99)), status=7))
    assert s.battery_percent == 54
    s.accept(frame(0x72, bytes((0, 255))))
    assert s.battery_percent == 54


def test_sign_states() -> None:
    s = NaveeState()
    d = battery_fixture()
    write(d, 6, 1800, 4)
    d[11] = 7
    s.accept(frame(0x72, bytes(d)))
    assert s.current_a == pytest.approx(1.8)
    s.accept(frame(0x79, bytes((1, 1))))
    assert s.current_a == pytest.approx(-1.8)
    assert s.temperature_c == -7
    assert s.power_w == pytest.approx(-94.2, abs=0.05)
    s.accept(frame(0x79, bytes((0, 0))))
    assert s.current_a == pytest.approx(1.8)


def test_ride_reports() -> None:
    s = NaveeState()
    s.accept(frame(0x91, bytes((90, 1, 25, 30, 5, 18, 29, 19, 220))))
    assert (s.speed_kmh, s.trip_km, s.trip_minutes, s.odometer_km) == (25, 5, 18, 220)
    ride = bytearray(18)
    ride[0], ride[1], ride[4], ride[7] = 89, 1, 29, 19
    write(ride, 2, 247, 2)
    write(ride, 5, 53, 2)
    write(ride, 8, 303, 2)
    write(ride, 10, 171, 2)
    write(ride, 12, 65000, 2)
    write(ride, 14, 12345678, 4)
    s.accept(frame(0x92, bytes(ride)))
    assert s.speed_kmh == pytest.approx(24.7)
    assert s.remaining_range_km == 29
    assert s.trip_km == pytest.approx(5.3)
    assert s.max_speed_kmh == pytest.approx(30.3)
    assert s.average_speed_kmh == pytest.approx(17.1)
    assert s.odometer_km == pytest.approx(1234567.8)
    write(ride, 14, 0, 4)
    s.accept(frame(0x92, bytes(ride)))
    assert s.odometer_km == pytest.approx(6500)


def test_home_report() -> None:
    s = NaveeState()
    home = bytearray(18)
    home[0:8] = bytes((3, 2, 88, 0, 1, 1, 27, 2))
    write(home, 8, 48500, 4)
    write(home, 12, 2000, 4)
    s.accept(frame(0x90, bytes(home)))
    assert s.battery_percent == 88
    assert s.voltage_v == pytest.approx(48.5)
    assert s.power_w == pytest.approx(97)
    assert s.lock_status == 1
    assert s.warning_code == 3
    home[7] = 0
    s.accept(frame(0x90, bytes(home[:8])))
    assert s.lock_status == 1, "zero lock byte means not supplied"
    s.accept(frame(0x90, bytes(16)))
    assert s.voltage_v == pytest.approx(48.5), "zero electrical fields mean absent"
    write(home, 12, 0, 4)
    s.accept(frame(0x90, bytes(home)))
    assert s.current_a == 0 and s.power_w == 0


def test_settings_and_motion() -> None:
    s = NaveeState()
    settings = bytearray(43)
    settings[2] = 1
    settings[31] = 90
    s.accept(frame(0x70, bytes(settings)), now=100.0)
    assert s.lock_status == 1
    assert s.setting(31) == 90
    assert s.status_fresh(7, now=105.0)
    assert not s.status_fresh(7, now=108.0)
    # An implausible lock byte is not trusted as a status frame.
    settings[2] = 5
    s.accept(frame(0x70, bytes(settings)), now=110.0)
    assert s.lock_status == 1 and s.settings_at == 100.0
    assert not s.stationary(now=100.0)
    s.accept(frame(0x92, bytes((80, 0, 0, 0, 10))), now=200.0)
    assert s.stationary(now=203.0)
    assert not s.stationary(now=207.0)
    s.accept(frame(0x92, bytes((80, 1, 50, 0, 10))), now=210.0)
    assert not s.stationary(now=210.0) and s.motion_seen()


def test_firmware_and_driving_mode() -> None:
    s = NaveeState(serial="N2538US0000000001")
    s.accept(frame(0x73, b"1234" + b"\x01\x02\x03\x04"))
    assert s.firmware == {"meter": "1.2.3.4", "controller": "01020304"}
    s.driving_mode = 3
    assert s.driving_mode_label() == "sport"
