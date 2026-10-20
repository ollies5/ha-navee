"""Protocol vectors shared with Volt's ProtocolTest.java and OemControlsTest.java."""

import pytest

from custom_components.navee import protocol
from custom_components.navee.controls import UT5_MAX_CONTROLS

AUTH_OK = bytes.fromhex("55aa0030010030fefd")
CHALLENGE = bytes.fromhex("55aa00301100000102030405060708090a0b0c0d0e0fb8fefd")


def notification(cmd: int, data: bytes) -> bytes:
    body = bytes((0x55, 0xAA, 0, cmd, len(data))) + data
    return body + bytes((protocol.checksum(body), 0xFE, 0xFD))


def test_frames_match_oem() -> None:
    assert protocol.read(0x70).hex() == "55aa00706ffefd"
    assert protocol.read(0x73).hex() == "55aa007372fefd"
    assert protocol.read(0x74).hex() == "55aa007473fefd"
    assert protocol.scalar(0x51, 1).hex() == "55aa0051010152fefd"
    assert protocol.scalar(0x51, 0).hex() == "55aa0051010051fefd"
    identity = bytes.fromhex("860012345678")
    assert protocol.auth(identity, 0, 3).hex() == "55aa003009030086001234567800d5fefd"
    assert protocol.auth(identity, 1, 3).hex() == "55aa003009030186001234567800d6fefd"


@pytest.mark.parametrize(
    ("key", "expected"),
    enumerate(
        [
            "55aa0031101466752c7f159722b2410a6a94875538b7fefd",
            "55aa003110c8c0526bdd6b5d9d9214d15c89042f98eefefd",
            "55aa003110653dbf363ad1b7a5d22fc7bfecef6a9da7fefd",
            "55aa003110a4ceaa30911ba7617bf543f43a5d498148fefd",
            "55aa00311061d25f08fa6083a3413bda11d230bbd14ffefd",
        ]
    ),
)
def test_aes_challenge_vectors(key: int, expected: str) -> None:
    assert protocol.challenge_response(protocol.parse(CHALLENGE), key).hex() == expected


def test_extended_and_xor_challenges() -> None:
    challenge = bytes(range(16))
    aes = "55aa003110a4ceaa30911ba7617bf543f43a5d498148fefd"
    for method in (1, 2):
        f = protocol.parse(notification(0x30, bytes((0, method)) + challenge))
        assert protocol.challenge_response(f, 3).hex() == aes
    xor = protocol.challenge_response(
        protocol.parse(notification(0x30, b"\x00\x00" + challenge)), 0
    )
    assert len(xor) == 24 and all(b == 0xA0 for b in xor[5:21])
    assert protocol.parse(xor).cmd == 0x31


def test_rejects_malformed_and_dangerous() -> None:
    for cmd in (0x50, 0x59, 0x61, 0x63, 0x6C, 0x6F, 0x77, 0x78, 0x7A, 0x80):
        with pytest.raises(protocol.ProtocolError):
            protocol.frame(cmd, b"\x01")
    with pytest.raises(protocol.ProtocolError):
        protocol.frame(0x51, b"\x02")
    with pytest.raises(protocol.ProtocolError):
        protocol.frame(0x70, b"\x00")
    with pytest.raises(protocol.ProtocolError):
        protocol.auth(bytes(5))
    with pytest.raises(protocol.ProtocolError):
        protocol.auth(bytes(6), role=2)
    with pytest.raises(protocol.ProtocolError):
        protocol.challenge_response(protocol.parse(AUTH_OK))
    with pytest.raises(protocol.ProtocolError):
        protocol.parse(bytes.fromhex("55aa00706ffefd"))
    for index in (0, 1, 2, 4, 6, 7, 8):
        damaged = bytearray(AUTH_OK)
        damaged[index] ^= 0x20 if index != 6 else 1
        with pytest.raises(protocol.ProtocolError):
            protocol.parse(bytes(damaged))


def test_notifications_retain_status() -> None:
    f = protocol.parse(CHALLENGE)
    assert (f.cmd, f.flag, f.error) == (0x30, 0, 0)
    assert f.payload == bytes(range(16))
    car = protocol.parse(notification(0x70, bytes((0, 1, 2, 1, 0))))
    assert car.data[3] == 1 and car.payload[2] == 1


def test_decoder_assembly() -> None:
    for split in range(len(CHALLENGE) + 1):
        decoder = protocol.Decoder()
        frames = decoder.feed(CHALLENGE[:split]) + decoder.feed(CHALLENGE[split:])
        assert [f.cmd for f in frames] == [0x30]
    decoder = protocol.Decoder()
    frames = []
    for b in CHALLENGE:
        frames += decoder.feed(bytes((b,)))
    assert len(frames) == 1
    frames = decoder.feed(CHALLENGE + AUTH_OK + notification(0x70, bytes((0, 1, 2, 0))))
    assert [f.cmd for f in frames] == [0x30, 0x30, 0x70]
    bad = bytearray(AUTH_OK)
    bad[6] ^= 1
    frames = decoder.feed(bytes((2, 3, 0x55)) + bytes(bad) + bytes.fromhex("55aa003000") + AUTH_OK)
    assert [f.cmd for f in frames] == [0x30]
    assert decoder.rejected > 0
    embedded = notification(0x90, b"\x00" + AUTH_OK + bytes.fromhex("fefd55aa"))
    assert [f.cmd for f in decoder.feed(embedded)] == [0x90]
    decoder.feed(bytes.fromhex("55aa0030ff"))
    assert len(decoder.feed(bytes(600) + AUTH_OK)) == 1
    decoder.feed(CHALLENGE[:12])
    decoder.reset()
    assert len(decoder.feed(AUTH_OK)) == 1


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("traction_control", 0, "55aa005f01005ffefd"),
        ("traction_control", 1, "55aa005f010160fefd"),
        ("low_battery_mode", 1, "55aa006f02050176fefd"),
        ("charge_limit", 80, "55aa006f020450c4fefd"),
        ("charge_limit", 100, "55aa006f020464d8fefd"),
        ("lock_duration", 3, "55aa006f02020375fefd"),
        ("walk_assist", 1, "55aa008102010184fefd"),
        ("power_assist", 0, "55aa008102020084fefd"),
        ("hill_descent_control", 1, "55aa008102030186fefd"),
        ("maximum_speed", 25, "55aa006e02011989fefd"),
        ("maximum_speed", 50, "55aa006e020132a2fefd"),
        ("kick_start_speed", 5, "55aa006a01056ffefd"),
    ],
)
def test_control_frames_match_oem(key: str, value: int, expected: str) -> None:
    assert UT5_MAX_CONTROLS[key].encode(value).hex() == expected


def test_control_domains() -> None:
    with pytest.raises(protocol.ProtocolError):
        UT5_MAX_CONTROLS["charge_limit"].encode(75)
    with pytest.raises(protocol.ProtocolError):
        UT5_MAX_CONTROLS["maximum_speed"].encode(30)
    with pytest.raises(protocol.ProtocolError):
        UT5_MAX_CONTROLS["kick_start_speed"].encode(6)


def test_light_readback_masks() -> None:
    settings = bytearray(43)
    settings[37] = 0b101
    settings[10] = 0x82
    assert UT5_MAX_CONTROLS["walk_assist"].readback(bytes(settings)) == 1
    assert UT5_MAX_CONTROLS["power_assist"].readback(bytes(settings)) == 0
    assert UT5_MAX_CONTROLS["hill_descent_control"].readback(bytes(settings)) == 1
    assert UT5_MAX_CONTROLS["line_lights"].readback(bytes(settings)) == 1
    assert UT5_MAX_CONTROLS["line_lights"].encode(0).hex() == protocol.light(4, 0, 0).hex()


def test_charge_start_and_clock() -> None:
    assert protocol.local_time(3, 1700002800, 0).hex() == "55aa006f05036553fbf019fefd"
    assert protocol.set_clock(1700002800, 3600)[5] == 6


def test_serial_and_advertised_pid() -> None:
    serial = b"N2538US0000000001"
    f = protocol.parse(notification(0x74, b"\x00" + serial))
    assert protocol.serial_from_frame(f) == "N2538US0000000001"
    assert protocol.serial_pid("N2538US0000000001") == "2538"
    assert protocol.serial_from_frame(protocol.parse(notification(0x74, b"\x00bad"))) is None
    # Raw manufacturer structure 05 EA 09 …: marker 5, PID 0x09EA = 2538.
    assert protocol.advertised_pid({0xEA05: bytes((0x09, 1, 2))}) == "2538"


def test_identity_parsing() -> None:
    assert protocol.parse_identity("86:00:12:34:56:78") == bytes.fromhex("860012345678")
    with pytest.raises(protocol.ProtocolError):
        protocol.parse_identity("xyz")
