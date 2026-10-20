"""Data coordinator for one NAVEE scooter."""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .client import (
    NaveeAsleepError,
    NaveeAuthError,
    NaveeClient,
    NaveeCommandError,
    NaveeError,
    NaveeIdentityError,
)
from .const import (
    CONF_IDENTITY,
    CONF_KEEP_CONNECTED,
    CONF_MODEL_ID,
    CONF_POLL_INTERVAL,
    CONF_ROLE,
    CONF_SERIAL,
    DEFAULT_KEEP_CONNECTED,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    PUSH_UPDATE_INTERVAL,
)
from .controls import Control, controls_for
from .telemetry import NaveeState

_LOGGER = logging.getLogger(__name__)

# Connection attempts need an advertisement this recent.
ADVERTISEMENT_MAX_AGE = 30.0
# After a failed attempt, retry no sooner than this, doubling per failure up to the maximum.
RECONNECT_BACKOFF = 30.0
MAX_RECONNECT_BACKOFF = 300.0

type NaveeConfigEntry = ConfigEntry[NaveeCoordinator]


class NaveeCoordinator(DataUpdateCoordinator[NaveeState]):
    """Owns the Bluetooth session and polls the scooter."""

    config_entry: NaveeConfigEntry

    def __init__(self, hass: HomeAssistant, entry: NaveeConfigEntry) -> None:
        interval = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data[CONF_ADDRESS]}",
            update_interval=timedelta(seconds=interval),
        )
        self.address: str = entry.data[CONF_ADDRESS]
        self.keep_connected: bool = entry.options.get(CONF_KEEP_CONNECTED, DEFAULT_KEEP_CONNECTED)
        self.client = NaveeClient(
            entry.data[CONF_IDENTITY],
            entry.data.get(CONF_ROLE, 0),
            entry.data.get(CONF_SERIAL),
        )
        self._model_id: str = entry.data.get(CONF_MODEL_ID, "")
        self._push_pending: CALLBACK_TYPE | None = None
        self._last_attempt = 0.0
        self._failures = 0
        self.awake = False
        """The last poll or command reached the scooter (it may be asleep or away otherwise)."""
        self._unsubs: list[CALLBACK_TYPE] = [
            self.client.add_listener(self._on_push),
            self.client.add_disconnect_listener(self._on_disconnect),
        ]

    @property
    def pid(self) -> str:
        return self.client.state.pid or self._model_id

    def controls(self) -> dict[str, Control]:
        return controls_for(self.pid)

    @callback
    def async_start(self) -> CALLBACK_TYPE:
        """Reconnect when the scooter advertises again (it wakes up or comes back in range)."""
        return bluetooth.async_register_callback(
            self.hass,
            self._on_advertisement,
            bluetooth.BluetoothCallbackMatcher(address=self.address, connectable=True),
            bluetooth.BluetoothScanningMode.PASSIVE,
        )

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._push_pending is not None:
            self._push_pending()
            self._push_pending = None
        await self.client.disconnect()

    def _recently_advertised(self) -> bool:
        """A sleeping scooter stops advertising; never spend a proxy slot dialling it then."""
        info = bluetooth.async_last_service_info(self.hass, self.address, connectable=True)
        return info is not None and bluetooth.MONOTONIC_TIME() - info.time <= ADVERTISEMENT_MAX_AGE

    def _backoff(self) -> float:
        """Repeated failures (asleep but still advertising, weak signal) back off up to 5 min."""
        if not self._failures:
            return 0.0
        return min(MAX_RECONNECT_BACKOFF, RECONNECT_BACKOFF * 2 ** (self._failures - 1))

    async def _ensure_connected(self) -> None:
        if self.client.connected:
            return
        if not self._recently_advertised():
            raise NaveeAsleepError("The scooter is asleep or out of Bluetooth range")
        device = bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
        if device is None:
            raise NaveeAsleepError("The scooter is asleep or out of Bluetooth range")
        self._last_attempt = time.monotonic()
        try:
            await self.client.connect(device)
        except NaveeError:
            self._failures += 1
            raise
        self._failures = 0

    def _learn_model(self) -> None:
        """Remember the model so controls exist while asleep; a change reloads the entry."""
        pid = self.client.state.pid
        if pid and pid != self._model_id:
            self._model_id = pid
            self.hass.config_entries.async_update_entry(
                self.config_entry, data={**self.config_entry.data, CONF_MODEL_ID: pid}
            )

    def _set_awake(self, awake: bool) -> None:
        if awake != self.awake:
            _LOGGER.debug("%s: scooter %s", self.address, "reachable" if awake else "unreachable")
        self.awake = awake

    async def _async_update_data(self) -> NaveeState:
        try:
            if self.client.connected:
                await self.client.refresh()
            elif time.monotonic() - self._last_attempt < self._backoff():
                self._set_awake(False)
                return self.client.state
            else:
                await self._ensure_connected()
            self._set_awake(True)
            self._learn_model()
        except NaveeAuthError as err:
            self._set_awake(False)
            await self.client.disconnect()
            raise ConfigEntryAuthFailed(str(err)) from err
        except NaveeIdentityError as err:
            self._set_awake(False)
            raise UpdateFailed(str(err)) from err
        except NaveeError as err:
            # Asleep, out of range or a dropped link: normal for a scooter. Keep the last
            # known values; the next advertisement or poll reconnects.
            _LOGGER.debug("%s: %s", self.address, err)
            self._set_awake(False)
            await self.client.disconnect()
        finally:
            if not self.keep_connected:
                await self.client.disconnect()
        return self.client.state

    async def async_set_control(self, key: str, value: int) -> None:
        control = self.controls().get(key)
        if control is None:
            raise HomeAssistantError(f"{key} is not supported on this scooter model")
        try:
            await self._ensure_connected()
            await self.client.set_control(control, value)
        except NaveeAuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(str(err)) from err
        except (NaveeCommandError, NaveeAsleepError) as err:
            raise HomeAssistantError(str(err)) from err
        except NaveeError as err:
            raise HomeAssistantError(f"Could not reach the scooter: {err}") from err
        finally:
            self.async_set_updated_data(self.client.state)
            if not self.keep_connected:
                await self.client.disconnect()

    async def async_sync_clock(self) -> None:
        now = dt_util.now()
        offset = now.utcoffset()
        try:
            await self._ensure_connected()
            await self.client.sync_clock(
                int(now.timestamp()), int(offset.total_seconds()) if offset else 0
            )
        except NaveeError as err:
            raise HomeAssistantError(str(err)) from err
        finally:
            if not self.keep_connected:
                await self.client.disconnect()

    # ---- Callbacks ---------------------------------------------------------------------

    @callback
    def _on_push(self) -> None:
        """Throttle pushed telemetry into at most one entity update per interval."""
        if self._push_pending is not None:
            return

        @callback
        def _flush(_now: object) -> None:
            self._push_pending = None
            self.async_set_updated_data(self.client.state)

        self._push_pending = async_call_later(self.hass, PUSH_UPDATE_INTERVAL, _flush)

    @callback
    def _on_disconnect(self) -> None:
        """The link dropped, usually because the scooter went to sleep."""
        _LOGGER.debug("%s: connection lost", self.address)
        self._set_awake(False)
        self.async_update_listeners()
        if self.keep_connected:
            self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _on_advertisement(
        self,
        _info: bluetooth.BluetoothServiceInfoBleak,
        _change: bluetooth.BluetoothChange,
    ) -> None:
        if self.awake or self.client.connected:
            return
        if time.monotonic() - self._last_attempt < self._backoff():
            return
        self.hass.async_create_task(self.async_request_refresh())
