"""A simulated NAVEE scooter behind a fake Bleak client."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import SimpleNamespace

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from custom_components.navee import protocol

KEY1 = bytes.fromhex("446d10726dbe05f662dfaaf01327303f")
IDENTITY = "860012345678"
SERIAL = "N2538US0000000001"


def notification(cmd: int, data: bytes) -> bytes:
    body = bytes((0x55, 0xAA, 0, cmd, len(data))) + data
    return body + bytes((protocol.checksum(body), 0xFE, 0xFD))


class FakeScooter:
    """Answers reads, runs the AES challenge handshake and applies allowlisted writes."""

    def __init__(self, identity: str = IDENTITY, serial: str = SERIAL) -> None:
        self.identity = bytes.fromhex(identity)
        self.serial = serial
        self.settings = bytearray(43)
        self.settings[2] = 0  # unlocked
        self.settings[31] = 100  # charge limit
        self.challenge = bytes(range(16))
        self.challenge_passed = False
        self.reject_writes = False
        self.ignore_writes = False
        self.written: list[bytes] = []
        self.notify: Callable[[object, bytearray], None] | None = None

    def emit(self, cmd: int, data: bytes) -> None:
        packet = notification(cmd, data)
        assert self.notify is not None
        # Deliver in 20-byte fragments, as an unnegotiated MTU would.
        for i in range(0, len(packet), 20):
            asyncio.get_running_loop().call_soon(self.notify, None, bytearray(packet[i : i + 20]))

    def push_motion(self, speed_tenths: int = 0, driving: int = 0) -> None:
        self.emit(0x92, bytes((0, 80, driving)) + speed_tenths.to_bytes(2, "little") + bytes(1))

    def receive(self, packet: bytes) -> None:
        self.written.append(packet)
        cmd = packet[3]
        if len(packet) == 7:
            self._read(cmd)
            return
        data = packet[5 : 5 + packet[4]]
        if cmd == protocol.AUTH:
            if data[2:8] != self.identity:
                self.emit(0x30, b"\x01")
            elif self.challenge_passed:
                self.emit(0x30, b"\x00")
            else:
                self.emit(0x30, b"\x00" + self.challenge)
        elif cmd == protocol.CHALLENGE_REPLY:
            enc = Cipher(algorithms.AES(KEY1), modes.ECB()).encryptor()
            expected = enc.update(self.challenge) + enc.finalize()
            self.challenge_passed = data == expected
            self.emit(0x31, b"\x00" if self.challenge_passed else b"\x02")
        else:
            self._write(cmd, data)

    def _read(self, cmd: int) -> None:
        if cmd == 0x74:
            self.emit(cmd, b"\x00" + self.serial.encode())
        elif cmd == 0x70:
            self.emit(cmd, b"\x00" + bytes(self.settings))
        elif cmd == 0x72:
            battery = bytearray(25)
            battery[1] = 67
            battery[2:6] = (52345).to_bytes(4, "little")
            battery[12] = 1
            self.emit(cmd, b"\x00" + bytes(battery))
        elif cmd == 0x73:
            self.emit(cmd, b"\x00" + b"1234" + b"5678")
        elif cmd == 0x79:
            self.emit(cmd, b"\x00\x00\x00")

    def _write(self, cmd: int, data: bytes) -> None:
        if self.ignore_writes:
            self.emit(cmd, b"\x00")
            return
        if self.reject_writes:
            self.emit(cmd, b"\x05")
            return
        if cmd == 0x51:
            self.settings[2] = data[0]
        elif cmd == 0x6F and data[0] == 4:
            self.settings[31] = data[1]
        elif cmd == 0x81:
            bit = 1 << (data[0] - 1)
            self.settings[37] = (self.settings[37] & ~bit) | (bit if data[1] else 0)
        self.emit(cmd, b"\x00")


class FakeBleakClient:
    """Just enough of BleakClient for NaveeClient."""

    def __init__(self, scooter: FakeScooter) -> None:
        self.scooter = scooter
        self.is_connected = True
        self.mtu_size = 23
        writer = SimpleNamespace(
            uuid=protocol.WRITE_UUID, properties=["write-without-response", "write"]
        )
        notifier = SimpleNamespace(uuid=protocol.NOTIFY_UUID, properties=["notify"])
        chars = {protocol.WRITE_UUID: writer, protocol.NOTIFY_UUID: notifier}
        service = SimpleNamespace(get_characteristic=chars.get)
        self.services = SimpleNamespace(
            get_service=lambda uuid: service if uuid == protocol.SERVICE_UUID else None
        )

    async def start_notify(self, _char, callback) -> None:
        self.scooter.notify = callback

    async def write_gatt_char(self, _uuid, data, response=False) -> None:
        self.scooter.receive(bytes(data))

    async def disconnect(self) -> None:
        self.is_connected = False
