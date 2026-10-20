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
