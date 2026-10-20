"""NaveeClient against a simulated scooter."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from custom_components.navee.client import (
    NaveeAuthError,
    NaveeClient,
    NaveeCommandError,
    NaveeIdentityError,
)
from custom_components.navee.controls import UT5_MAX_CONTROLS

from .fake_scooter import IDENTITY, SERIAL, FakeBleakClient, FakeScooter


async def connected(scooter: FakeScooter, **kwargs) -> NaveeClient:
    client = NaveeClient(kwargs.pop("identity", IDENTITY), **kwargs)

    async def establish(*_args, **_kwargs):
        return FakeBleakClient(scooter)

    with patch("custom_components.navee.client.establish_connection", establish):
        await client.connect(object.__new__(type("Dev", (), {"name": "NAVEE", "address": "AA"})))
    return client


async def test_handshake_and_initial_reads() -> None:
    scooter = FakeScooter()
    client = await connected(scooter)
    assert client.connected
    assert scooter.challenge_passed
    state = client.state
    assert state.serial == SERIAL and state.pid == "2538"
    assert state.battery_percent == 67
    assert state.voltage_v == pytest.approx(52.345)
    assert state.charging is True
    assert state.lock_status == 0
    assert state.firmware["meter"] == "1.2.3.4"
    # The auth frame carries key index 1 and the owner role.
    assert scooter.written[0].hex().startswith("55aa0030090100860012345678")


async def test_wrong_identity_rejected() -> None:
    with pytest.raises(NaveeAuthError):
        await connected(FakeScooter(), identity="111111111111")


async def test_wrong_scooter_rejected() -> None:
    with pytest.raises(NaveeIdentityError):
        await connected(FakeScooter(), expected_serial="N2538US0000000002")


async def test_lock_needs_parked_motion_report() -> None:
    scooter = FakeScooter()
    client = await connected(scooter)
    lock = UT5_MAX_CONTROLS["lock"]
    with pytest.raises(NaveeCommandError, match="No motion report"):
        await client.set_control(lock, 1)
    scooter.push_motion(speed_tenths=55, driving=1)
    await client.refresh(battery=False)
    with pytest.raises(NaveeCommandError, match="Stop the scooter"):
        await client.set_control(lock, 1)
    scooter.push_motion()
    await client.refresh(battery=False)
    await client.set_control(lock, 1)
    assert scooter.settings[2] == 1 and client.state.lock_status == 1


async def test_unlock_needs_no_motion_report() -> None:
    scooter = FakeScooter()
    scooter.settings[2] = 1
    client = await connected(scooter)
    await client.set_control(UT5_MAX_CONTROLS["lock"], 0)
    assert scooter.settings[2] == 0


async def test_charge_limit_and_bitfield_controls_confirm() -> None:
    scooter = FakeScooter()
    client = await connected(scooter)
    scooter.push_motion()
    await client.refresh(battery=False)
    await client.set_control(UT5_MAX_CONTROLS["charge_limit"], 85)
    await client.set_control(UT5_MAX_CONTROLS["power_assist"], 1)
    assert UT5_MAX_CONTROLS["charge_limit"].readback(client.state.settings) == 85
    assert UT5_MAX_CONTROLS["power_assist"].readback(client.state.settings) == 1
    assert UT5_MAX_CONTROLS["walk_assist"].readback(client.state.settings) == 0


async def test_rejected_and_unconfirmed_changes() -> None:
    scooter = FakeScooter()
    client = await connected(scooter)
    scooter.push_motion()
    await client.refresh(battery=False)
    scooter.reject_writes = True
    with pytest.raises(NaveeCommandError, match="rejected"):
        await client.set_control(UT5_MAX_CONTROLS["charge_limit"], 90)
    scooter.reject_writes = False
    scooter.ignore_writes = True
    with (
        patch("custom_components.navee.client.CONFIRM_TIMEOUT", 1.0),
        pytest.raises(NaveeCommandError, match="not confirmed"),
    ):
        await client.set_control(UT5_MAX_CONTROLS["charge_limit"], 90)


async def test_unchanged_value_sends_nothing() -> None:
    scooter = FakeScooter()
    client = await connected(scooter)
    scooter.push_motion()
    await client.refresh(battery=False)
    before = len(scooter.written)
    await client.set_control(UT5_MAX_CONTROLS["charge_limit"], 100)
    assert all(len(p) == 7 for p in scooter.written[before:])
