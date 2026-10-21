"""Owner identity import (Volt OwnerIdentityParserTest equivalents)."""

import pytest

from custom_components.navee import protocol
from custom_components.navee.identity import IdentityError, OwnerIdentity, parse


def test_plain_hex() -> None:
    assert parse(" 860012345678\n") == OwnerIdentity("860012345678", 0)


def test_setup_file() -> None:
    assert parse('{"identity": "8600123456ab", "role": 1}') == OwnerIdentity("8600123456AB", 1)
    assert parse("identity: 8600123456ab\nShared-user option: ON\n") == OwnerIdentity(
        "8600123456AB", 1
    )


def test_captured_auth_frame() -> None:
    frame = protocol.auth(bytes.fromhex("860012345678"), 0, 1).hex()
    log = f"10-20 12:00:00 D/Ble: Send authData: [{frame}]\n"
    assert parse(log) == OwnerIdentity("860012345678", 0)


def test_corrupt_or_conflicting() -> None:
    frame = bytearray(protocol.auth(bytes.fromhex("860012345678"), 0, 1))
    frame[14] ^= 1
    with pytest.raises(IdentityError):
        parse(f"Send authData: {frame.hex()}")
    with pytest.raises(IdentityError):
        parse("identity: 860012345678\nidentity: 860012345679")
    with pytest.raises(IdentityError):
        parse("identity: nothex")
    with pytest.raises(IdentityError):
        parse("")


def test_volt_helper_setup_file() -> None:
    """The JSON file Volt's computer helper and setup website save."""
    text = """{
  "format": "navee-offline-owner-v1",
  "owner_identity": "860012345678",
  "role": 0,
  "model_name": "UT5 MAX",
  "captured_at_utc": "2026-10-20T12:00:00Z",
  "note": "Private Bluetooth owner identity; not a NAVEE password or cloud login token."
}
"""
    assert parse(text) == OwnerIdentity("860012345678", 0)


def test_logcat_output_with_spaced_bytes() -> None:
    """`adb logcat` output from the manual steps in the README."""
    frame = " ".join(f"{b:02x}" for b in protocol.auth(bytes.fromhex("860012345678"), 0, 1))
    assert frame == "55 aa 00 30 09 01 00 86 00 12 34 56 78 00 d3 fe fd", "README example"
    log = (
        "--------- beginning of main\n"
        f"10-20 12:00:01.123  4321  4400 D com.navee.ucaret: Send authData: [{frame}]\n"
        f"10-20 12:00:09.456  4321  4400 D com.navee.ucaret: Send authData: [{frame}]\n"
    )
    assert parse(log) == OwnerIdentity("860012345678", 0)
