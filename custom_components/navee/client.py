"""One serialized Bluetooth session with a NAVEE scooter.

Flow (from Volt's ``BleController.java``): connect, subscribe to notifications, send the owner
authentication, answer a challenge if one arrives (then authenticate again), and once a
status-only 0x30 reply arrives the session is authenticated. Every write is checked against
fresh status first and confirmed by its acknowledgement plus a fresh 0x70 readback.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from . import protocol
from .controls import Control
from .telemetry import NaveeState

_LOGGER = logging.getLogger(__name__)

AUTH_TIMEOUT = 15.0
READ_TIMEOUT = 5.0
ACK_TIMEOUT = 4.0
CONFIRM_TIMEOUT = 6.5
STATUS_MAX_AGE = 7.0
WRITE_GAP = 0.08
MAX_AUTH_ROUNDS = 3

# Sensitive opcodes are never logged with their bytes.
_SENSITIVE = frozenset({0x30, 0x31, 0x63, 0x7A})


class NaveeError(Exception):
    """Base error for scooter sessions."""


class NaveeConnectionError(NaveeError):
    """The scooter could not be reached or the link dropped."""


class NaveeAsleepError(NaveeConnectionError):
    """The scooter is not advertising: asleep, powered off or out of range."""


class NaveeAuthError(NaveeError):
    """The scooter rejected the owner identity."""


class NaveeIdentityError(NaveeError):
    """Connected to a different scooter than the one configured."""


class NaveeCommandError(NaveeError):
    """A control change was refused, rejected or not confirmed."""


class NaveeClient:
    """Connection, handshake and request/reply handling for one scooter."""

    def __init__(
        self,
        identity: str,
        role: int = protocol.ROLE_OWNER,
        expected_serial: str | None = None,
    ) -> None:
        self._identity = protocol.parse_identity(identity)
        self._role = role
        self._expected_serial = expected_serial or None
        self._client: BleakClientWithServiceCache | None = None
        self._decoder = protocol.Decoder()
        self._waiters: dict[int, list[asyncio.Future[protocol.Frame]]] = {}
        self._write_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._authenticated = False
        self._auth_event: asyncio.Event | None = None
        self._auth_error: NaveeError | None = None
        self._auth_rounds = 0
        self._generation = 0
        self._write_response = True
        self._listeners: list[Callable[[], None]] = []
        self._disconnect_listeners: list[Callable[[], None]] = []
        self.state = NaveeState()
        self.last_frame_at = 0.0

    # ---- Public API --------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected and self._authenticated

    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Called after every inbound frame that changed telemetry."""
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)

    def add_disconnect_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._disconnect_listeners.append(callback)
        return lambda: self._disconnect_listeners.remove(callback)

    async def connect(self, device: BLEDevice) -> None:
        """Connect and authenticate, then read the serial and current status."""
        async with self._connect_lock:
            if self.connected:
                return
            await self._disconnect()
            self._decoder.reset()
            self.state.new_session()
            self._generation += 1
            generation = self._generation
            try:
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    device,
                    device.name or device.address,
                    # A late callback from an earlier link must not end this one.
                    disconnected_callback=lambda _c: self._on_disconnect(generation),
                    max_attempts=3,
                )
            except (BleakError, TimeoutError) as err:
                raise NaveeConnectionError(f"Could not connect: {err}") from err
            self._client = client
            try:
                await self._setup(client)
                await self._authenticate()
                await self._identify()
            except NaveeError:
                await self._disconnect()
                raise
            except (BleakError, TimeoutError) as err:
                await self._disconnect()
                raise NaveeConnectionError(f"Connection failed: {err}") from err

    async def disconnect(self) -> None:
        async with self._connect_lock:
            await self._disconnect()

    async def refresh(self, battery: bool = True) -> NaveeState:
        """Read vehicle status (and battery). Raises if the status read gets no reply."""
        self._require_connected()
        reads = [protocol.READ_VEHICLE]
        if battery:
            reads += [protocol.READ_BATTERY, protocol.READ_BATTERY_SIGN]
        tasks = [self._request(cmd, READ_TIMEOUT) for cmd in reads]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        if isinstance(results[0], BaseException):
            raise NaveeConnectionError("No status reply from the scooter") from results[0]
        return self.state

    async def read_firmware(self) -> None:
        self._require_connected()
        with contextlib.suppress(NaveeError):
            await self._request(protocol.READ_FIRMWARE, READ_TIMEOUT)

    async def set_control(self, control: Control, value: int) -> None:
        """Admit, send and confirm one control change."""
        async with self._command_lock:
            self._require_connected()
            if not control.allows(value):
                raise NaveeCommandError("Value is outside this control's options")
            # Fresh status first: every decision uses this session's current readback.
            await self._request(protocol.READ_VEHICLE, READ_TIMEOUT)
            self._admit(control, value)
            if control.readback(self.state.settings) == value:
                return
            frame = control.encode(value)
            await self._send_and_ack(frame)
            await asyncio.sleep(0.25)
            await self._confirm(control, value)

    async def sync_clock(self, unix_seconds: int, tz_offset_seconds: int) -> None:
        """Set the scooter clock to local time. The firmware exposes no readback."""
        async with self._command_lock:
            self._require_connected()
            await self._send_and_ack(protocol.set_clock(unix_seconds, tz_offset_seconds))

    # ---- Admission and confirmation ---------------------------------------------------

    def _admit(self, control: Control, value: int) -> None:
        state = self.state
        now = time.monotonic()
        if not state.status_fresh(STATUS_MAX_AGE, now):
            raise NaveeCommandError("Waiting for fresh scooter status")
        if control.readback(state.settings) is None:
            raise NaveeCommandError("This scooter does not report this setting")
        # Unlocking follows the recovery contract: an owner can always unlock while connected.
        if control.key == "lock" and value == 0:
            return
        if control.parked and not state.stationary(now):
            if state.motion_seen():
                raise NaveeCommandError("Stop the scooter first")
            raise NaveeCommandError(
                "No motion report from the scooter yet; power it on and keep it parked"
            )
        if control.key == "lock" and state.warning_active(now):
            raise NaveeCommandError(f"Resolve scooter warning {state.warning_code} before locking")

    async def _send_and_ack(self, frame: bytes) -> None:
        cmd = frame[3]
        future = self._waiter(cmd)
        try:
            await self._write(frame)
            reply = await asyncio.wait_for(future, ACK_TIMEOUT)
        except TimeoutError as err:
            raise NaveeCommandError("The scooter did not acknowledge the change") from err
        finally:
            self._drop_waiter(cmd, future)
        if reply.error != 0:
            raise NaveeCommandError(f"The scooter rejected the change ({reply.error})")

    async def _confirm(self, control: Control, value: int) -> None:
        deadline = time.monotonic() + CONFIRM_TIMEOUT
        reads = [protocol.READ_VEHICLE] + ([protocol.READ_BATTERY] if control.battery else [])
        while time.monotonic() < deadline:
            for cmd in reads[1:]:
                self._spawn_read(cmd)
            with contextlib.suppress(NaveeError):
                await self._request(protocol.READ_VEHICLE, max(0.5, deadline - time.monotonic()))
                if control.readback(self.state.settings) == value:
                    return
            await asyncio.sleep(0.4)
        raise NaveeCommandError("The change was accepted but not confirmed by a readback")

    # ---- Transport ---------------------------------------------------------------------

    async def _setup(self, client: BleakClientWithServiceCache) -> None:
        service = client.services.get_service(protocol.SERVICE_UUID)
        if service is None:
            raise NaveeConnectionError("Not a supported NAVEE scooter (service missing)")
        writer = service.get_characteristic(protocol.WRITE_UUID)
        notifier = service.get_characteristic(protocol.NOTIFY_UUID)
        if writer is None or notifier is None:
            raise NaveeConnectionError("Not a supported NAVEE scooter (characteristics missing)")
        self._write_response = "write-without-response" not in writer.properties
        await client.start_notify(notifier, self._on_notify)

    async def _write(self, frame: bytes) -> None:
        client = self._client
        if client is None or not client.is_connected:
            raise NaveeConnectionError("Not connected")
        cmd = frame[3]
        if cmd not in _SENSITIVE:
            _LOGGER.debug("Send 0x%02X (%d bytes)", cmd, len(frame))
        # A frame longer than one packet goes as a long write with response.
        response = self._write_response or len(frame) > max(20, client.mtu_size - 3)
        async with self._write_lock:
            try:
                await client.write_gatt_char(protocol.WRITE_UUID, frame, response=response)
            except BleakError as err:
                raise NaveeConnectionError(f"Send failed: {err}") from err
            await asyncio.sleep(WRITE_GAP)

    def _waiter(self, cmd: int) -> asyncio.Future[protocol.Frame]:
        future: asyncio.Future[protocol.Frame] = asyncio.get_running_loop().create_future()
        self._waiters.setdefault(cmd, []).append(future)
        return future

    def _drop_waiter(self, cmd: int, future: asyncio.Future[protocol.Frame]) -> None:
        waiters = self._waiters.get(cmd)
        if waiters and future in waiters:
            waiters.remove(future)

    async def _request(self, cmd: int, timeout: float) -> protocol.Frame:
        future = self._waiter(cmd)
        try:
            await self._write(protocol.read(cmd))
            return await asyncio.wait_for(future, timeout)
        except TimeoutError as err:
            raise NaveeConnectionError(f"No reply to read 0x{cmd:02X}") from err
        finally:
            self._drop_waiter(cmd, future)

    def _spawn_read(self, cmd: int) -> None:
        async def _read() -> None:
            with contextlib.suppress(NaveeError):
                await self._request(cmd, READ_TIMEOUT)

        task = asyncio.get_running_loop().create_task(_read())
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    def _on_notify(self, _sender: object, data: bytearray) -> None:
        for f in self._decoder.feed(bytes(data)):
            self.last_frame_at = time.monotonic()
            if f.cmd not in _SENSITIVE:
                _LOGGER.debug(
                    "Receive 0x%02X status %d (%d bytes)", f.cmd, f.error, len(f.data) - 1
                )
            if f.flag != 0:
                _LOGGER.warning("Encrypted frames are not supported by this integration")
                continue
            if f.cmd in (protocol.AUTH, protocol.CHALLENGE_REPLY):
                self._on_auth_frame(f)
            elif self._authenticated:
                self.state.accept(f)
                for listener in list(self._listeners):
                    listener()
            for future in self._waiters.pop(f.cmd, []):
                if not future.done():
                    future.set_result(f)

    def _on_disconnect(self, generation: int) -> None:
        if generation == self._generation:
            self._link_ended(notify=True)

    async def _disconnect(self) -> None:
        """Intentional disconnect: listeners are not told and its late callback is ignored."""
        client, self._client = self._client, None
        self._generation += 1
        self._link_ended(notify=False)
        if client is not None:
            with contextlib.suppress(BleakError, TimeoutError, EOFError):
                await client.disconnect()

    def _link_ended(self, notify: bool) -> None:
        was_authenticated = self._authenticated
        self._authenticated = False
        self._decoder.reset()
        for waiters in self._waiters.values():
            for future in waiters:
                if not future.done():
                    future.set_exception(NaveeConnectionError("Disconnected"))
        self._waiters.clear()
        if self._auth_event is not None and not self._auth_event.is_set():
            self._auth_error = NaveeConnectionError("Disconnected during authentication")
            self._auth_event.set()
        if notify and was_authenticated:
            for listener in list(self._disconnect_listeners):
                listener()

    def _require_connected(self) -> None:
        if not self.connected:
            raise NaveeConnectionError("Not connected to the scooter")

    # ---- Handshake ---------------------------------------------------------------------

    async def _authenticate(self) -> None:
        self._authenticated = False
        self._auth_error = None
        self._auth_rounds = 0
        self._auth_event = asyncio.Event()
        await self._send_auth()
        try:
            await asyncio.wait_for(self._auth_event.wait(), AUTH_TIMEOUT)
        except TimeoutError as err:
            raise NaveeConnectionError("The scooter did not answer authentication") from err
        if self._auth_error is not None:
            raise self._auth_error

    async def _send_auth(self) -> None:
        self._auth_rounds += 1
        if self._auth_rounds > MAX_AUTH_ROUNDS:
            self._finish_auth(NaveeAuthError("Owner check failed"))
            return
        await self._write(protocol.auth(self._identity, self._role))

    def _finish_auth(self, error: NaveeError | None) -> None:
        if error is None:
            self._authenticated = True
        self._auth_error = error
        if self._auth_event is not None:
            self._auth_event.set()

    def _on_auth_frame(self, f: protocol.Frame) -> None:
        if self._auth_event is None or self._auth_event.is_set():
            return
        if f.cmd == protocol.AUTH:
            if f.error != 0:
                self._finish_auth(NaveeAuthError(f"Owner identity rejected ({f.error})"))
            elif len(f.data) > 1:
                try:
                    reply = protocol.challenge_response(f)
                except protocol.ProtocolError as err:
                    self._finish_auth(NaveeAuthError(str(err)))
                    return
                self._background(self._write(reply))
            else:
                self._finish_auth(None)
        elif f.cmd == protocol.CHALLENGE_REPLY:
            if f.error != 0:
                self._finish_auth(NaveeAuthError(f"Owner check failed ({f.error})"))
                return

            async def _again() -> None:
                await asyncio.sleep(0.15)
                await self._send_auth()

            self._background(_again())

    def _background(self, coro) -> None:
        async def _run() -> None:
            try:
                await coro
            except NaveeError as err:
                self._finish_auth(err)

        asyncio.get_running_loop().create_task(_run())

    async def _identify(self) -> None:
        reply = await self._request(protocol.READ_SERIAL, READ_TIMEOUT)
        serial = protocol.serial_from_frame(reply)
        if serial is None:
            raise NaveeIdentityError("Unrecognised scooter serial")
        if self._expected_serial and serial != self._expected_serial:
            raise NaveeIdentityError("This is not the configured scooter")
        self.state.serial = serial
        await self.refresh()
        await self.read_firmware()
