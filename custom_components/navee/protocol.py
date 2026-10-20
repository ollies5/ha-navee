"""NAVEE BLE wire protocol: framing, stream decoding and the owner handshake.

Pure Python with no Home Assistant or Bluetooth imports, so it can be unit tested on its own.
Ported from Volt (navee-offline) ``Protocol.java`` / ``WireCommands.java``; protocol research
builds on pepperonas/navee-st3-pro.

Frame layout::

    55 AA <flag> <cmd> [<len>] <data...> <checksum> FE FD

Outbound reads carry no length byte and no data. Outbound writes and every inbound
notification carry a length byte. Inbound data starts with a status byte (0 = OK).
The checksum is the low byte of the sum of every byte before it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SERVICE_UUID = "0000d0ff-3c17-d293-8e48-14fe2e4da212"
WRITE_UUID = "0000b002-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000b003-0000-1000-8000-00805f9b34fb"

AUTH = 0x30
CHALLENGE_REPLY = 0x31
LOCK = 0x51
READ_VEHICLE = 0x70
READ_RIDE = 0x71
READ_BATTERY = 0x72
READ_FIRMWARE = 0x73
READ_SERIAL = 0x74
READ_BATTERY_SERIAL = 0x75
READ_HISTORY = 0x76
READ_BATTERY_SIGN = 0x79
READ_FAULTS = 0x7B
REPORT_HOME = 0x90
REPORT_RIDE_V0 = 0x91
REPORT_RIDE_V1 = 0x92

READ_COMMANDS = frozenset(
    {
        READ_VEHICLE,
        READ_RIDE,
        READ_BATTERY,
        READ_FIRMWARE,
        READ_SERIAL,
        READ_BATTERY_SERIAL,
        READ_HISTORY,
        READ_BATTERY_SIGN,
        READ_FAULTS,
    }
)

# The key index the Volt app authenticates with (the OEM app's choice as well).
AUTH_KEY_INDEX = 1

# Constants from the installed OEM app.
_KEYS = (
    bytes.fromhex("a0a1a2a3a4a5a6a7a8a9aaabacadaeaf"),
    bytes.fromhex("446d10726dbe05f662dfaaf01327303f"),
    bytes.fromhex("a285ccec814fe961742995e8eba92247"),
    bytes.fromhex("3fee80ff96df5cf542eaac93281fe529"),
    bytes.fromhex("4eb4d464d6ef53ed6ce94558de9a5ee3"),
)

ROLE_OWNER = 0
ROLE_SHARED = 1

_SERIAL_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{16}")


class ProtocolError(ValueError):
    """A frame or payload outside what this protocol port accepts."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProtocolError(message)


def checksum(data: bytes) -> int:
    """Low byte of the sum of every byte."""
    return sum(data) & 0xFF


def _encode(cmd: int, data: bytes, length_present: bool, flag: int = 0) -> bytes:
    head = bytes((0x55, 0xAA, flag, cmd)) + (bytes((len(data),)) if length_present else b"")
    body = head + data
    return body + bytes((checksum(body), 0xFE, 0xFD))


def frame(cmd: int, payload: bytes | None = None) -> bytes:
    """Build one outbound frame. Writes must pass the control allowlist."""
    data = bytes(payload or b"")
    if cmd in READ_COMMANDS:
        _require(not data, "Read commands cannot contain payload")
        return _encode(cmd, data, False)
    if cmd == AUTH:
        _require(len(data) == 9, "Authentication payload must contain nine bytes")
        _require(0 <= data[0] < len(_KEYS), "Authentication key index must be 0 through 4")
        _require(data[1] <= 1 and data[8] == 0, "Invalid authentication role/trailer")
    elif cmd == CHALLENGE_REPLY:
        _require(
            16 <= len(data) <= 240 and len(data) % 16 == 0,
            "Challenge response must contain whole AES blocks",
        )
    else:
        _require(valid_payload(cmd, data), "Command or payload is outside the control allowlist")
    return _encode(cmd, data, True)


def read(cmd: int) -> bytes:
    """A seven-byte read request."""
    _require(cmd in READ_COMMANDS, "Not a known read command")
    return frame(cmd)


def parse_identity(identity: str) -> bytes:
    """Six-byte owner identity from twelve hex characters (separators allowed)."""
    cleaned = re.sub(r"[\s:-]", "", identity or "")
    _require(re.fullmatch(r"[0-9a-fA-F]{12}", cleaned) is not None, "Need 12 hex characters")
    return bytes.fromhex(cleaned)


def auth(identity6: bytes, role: int = ROLE_OWNER, key_index: int = AUTH_KEY_INDEX) -> bytes:
    """0x30 owner/shared-user authentication request."""
    _require(len(identity6) == 6, "Owner identity must contain six bytes")
    _require(role in (ROLE_OWNER, ROLE_SHARED), "Role must be owner (0) or shared user (1)")
    _require(0 <= key_index < len(_KEYS), "Authentication key index must be 0 through 4")
    return frame(AUTH, bytes((key_index, role)) + bytes(identity6) + b"\x00")


@dataclass(frozen=True, slots=True)
class Frame:
    """One complete inbound notification."""

    cmd: int
    flag: int
    data: bytes
    """Notification data with the status byte at offset zero."""

    @property
    def error(self) -> int:
        return self.data[0]

    @property
    def payload(self) -> bytes:
        return self.data[1:]


def challenge_response(notification: Frame, key_index: int = AUTH_KEY_INDEX) -> bytes:
    """0x31 reply to an authentication challenge (legacy AES, extended AES or XOR)."""
    _require(notification.cmd == AUTH, "Expected an authentication notification")
    _require(notification.error == 0, "Scooter rejected authentication")
    data = notification.data
    _require(len(data) > 1, "Authentication completed without a challenge")
    offset = 1
    use_xor = False
    # Legacy: status + 16-byte challenge. Extended: status + method + challenge.
    if len(data) > 17:
        use_xor = data[1] == 0
        offset = 2
    challenge = data[offset:]
    key = _KEYS[key_index]
    if use_xor:
        _require(len(challenge) == 16, "XOR challenge must contain sixteen bytes")
        response = bytes(c ^ k for c, k in zip(challenge, key, strict=True))
    else:
        _require(
            len(challenge) >= 16 and len(challenge) % 16 == 0,
            "AES challenge must contain whole blocks",
        )
        encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        response = encryptor.update(challenge) + encryptor.finalize()
    return frame(CHALLENGE_REPLY, response)


def parse(packet: bytes) -> Frame:
    """Parse one complete inbound notification."""
    _require(len(packet) >= 9, "Notification is too short")
    _require(packet[0] == 0x55 and packet[1] == 0xAA, "Bad notification header")
    _require(packet[2] in (0, 1), "Unknown encryption flag")
    length = packet[4]
    _require(length >= 1 and len(packet) == length + 8, "Notification length mismatch")
    _require(packet[-2] == 0xFE and packet[-1] == 0xFD, "Bad notification footer")
    _require(packet[-3] == checksum(packet[:-3]), "Notification checksum mismatch")
    return Frame(packet[3], packet[2], bytes(packet[5 : 5 + length]))


class Decoder:
    """Bounded stream assembler for fragmented and coalesced notifications.

    Header/footer bytes inside data are ordinary data; a candidate's declared length wins.
    Reset on disconnect so a truncated stale candidate is discarded.
    """

    MAX_FRAME = 263

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.rejected = 0

    def reset(self) -> None:
        self._buffer.clear()

    def feed(self, notification: bytes) -> list[Frame]:
        frames: list[Frame] = []
        for byte in notification:
            if len(self._buffer) == self.MAX_FRAME:
                del self._buffer[0]
                self.rejected += 1
            self._buffer.append(byte)
            self._drain(frames)
        return frames

    def _drain(self, frames: list[Frame]) -> None:
        buf = self._buffer
        while len(buf) >= 2:
            if buf[0] != 0x55 or buf[1] != 0xAA:
                del buf[0]
                self.rejected += 1
                continue
            if len(buf) < 5:
                return
            length = buf[4]
            if buf[2] not in (0, 1) or length == 0:
                del buf[0]
                self.rejected += 1
                continue
            total = length + 8
            if len(buf) < total:
                return
            try:
                frames.append(parse(bytes(buf[:total])))
                del buf[:total]
            except ProtocolError:
                del buf[0]
                self.rejected += 1


def serial_from_frame(f: Frame) -> str | None:
    """Validated 17-character scooter serial from a 0x74 reply, else None."""
    if f.cmd != READ_SERIAL or f.error != 0 or len(f.data) < 2:
        return None
    text = f.data[1:18].decode("ascii", errors="replace").replace("\x00", "").strip()
    return text if _SERIAL_RE.fullmatch(text) else None


def serial_pid(serial: str | None) -> str:
    """Model ID embedded in the serial (characters 1..5), or ''."""
    if not serial or not _SERIAL_RE.fullmatch(serial):
        return ""
    pid = serial[1:5]
    return pid if pid.isdigit() else ""


def advertised_pid(manufacturer_data: dict[int, bytes]) -> str | None:
    """Model ID from the first manufacturer-data structure.

    The OEM app reads PID = mfr[1] | mfr[2] << 8 from the raw structure. Bleak/Home Assistant
    split off the first two bytes as a company ID (little-endian), so the PID low byte is the
    company ID's high byte and the PID high byte is data[0].
    """
    for company_id, data in manufacturer_data.items():
        if not data:
            return None
        return str(((company_id >> 8) & 0xFF) | (data[0] << 8))
    return None


# ---- Writes ------------------------------------------------------------------------------


def scalar(cmd: int, value: int) -> bytes:
    """[v]: 0x51-0x58, 0x5f, 0x60, 0x6a, 0x82."""
    return frame(cmd, bytes((value,)))


def selector(cmd: int, sel: int, value: int) -> bytes:
    """[selector, v]: 0x6e, 0x6f, 0x81."""
    return frame(cmd, bytes((sel, value)))


def light(target: int, sub: int, value: int) -> bytes:
    """0x6d [target 4 line | 5 star, sub, v]."""
    return frame(0x6D, bytes((target, sub, value)))


def local_time(sel: int, unix_seconds: int, tz_offset_seconds: int) -> bytes:
    """0x6f [selector 3 charge start | 6 clock, BE32 local seconds]."""
    _require(-18 * 3600 <= tz_offset_seconds <= 18 * 3600, "Timezone offset is invalid")
    local = unix_seconds + tz_offset_seconds
    _require(unix_seconds > 0 and 0 < local <= 0xFFFFFFFF, "Timestamp must fit a four-byte clock")
    return frame(0x6F, bytes((sel,)) + local.to_bytes(4, "big"))


def set_clock(unix_seconds: int, tz_offset_seconds: int) -> bytes:
    """The OEM app sets the scooter clock to local time after authentication."""
    return local_time(6, unix_seconds, tz_offset_seconds)


def _binary(v: int) -> bool:
    return v in (0, 1)


def valid_payload(cmd: int, d: bytes) -> bool:
    """Defensive write allowlist (from Volt). PIN (0x63) and firmware/reset opcodes are excluded."""
    a = d[0] if len(d) > 0 else -1
    b = d[1] if len(d) > 1 else -1
    if cmd in (0x51, 0x52, 0x54, 0x55, 0x57, 0x5F, 0x60, 0x82):
        return len(d) == 1 and _binary(a)
    if cmd == 0x53:
        return len(d) == 1 and a in (0, 1, 30, 60, 90)
    if cmd == 0x56:
        return len(d) == 1 and 0 <= a <= 4
    if cmd == 0x58:
        return len(d) == 1 and a in (3, 5)
    if cmd == 0x6A:
        return len(d) == 1 and 0 <= a <= 5
    if cmd == 0x6B:
        return len(d) == 1 and (a == 0 or (a >= 128 and 8 <= (a & 127) <= 32))
    if cmd == 0x6D:
        if len(d) == 2:
            return 1 <= a <= 3 and 0 <= b <= 2
        if len(d) != 3 or a not in (4, 5):
            return False
        c = d[2]
        return (b == 0 and _binary(c)) or (b == 1 and 0 <= c <= 2) or (b in (2, 3) and 0 <= c <= 5)
    if cmd == 0x6E:
        return len(d) == 2 and (
            (a == 1 and b in (25, 30, 32, 35, 40, 45, 50, 55, 60, 65, 70))
            or (a == 2 and _binary(b))
            or (a == 3 and (b == 0 or (b >= 128 and 5 <= (b & 127) <= 20)))
        )
    if cmd == 0x6F:
        if len(d) == 5:
            return a in (3, 6) and any(d[1:])
        return len(d) == 2 and (
            (a in (1, 5, 7) and _binary(b))
            or (a == 2 and 1 <= b <= 3)
            or (a == 4 and 80 <= b <= 100 and b % 5 == 0)
        )
    if cmd == 0x81:
        return len(d) == 2 and 1 <= a <= 3 and _binary(b)
    return False
