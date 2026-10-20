"""Config flow for NAVEE scooters."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.components import bluetooth
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from . import identity as identity_parser
from . import protocol
from .client import NaveeAuthError, NaveeClient, NaveeError, NaveeIdentityError
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
    MANUFACTURER,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
    model_name,
)

_LOGGER = logging.getLogger(__name__)

CONF_SHARED_USER = "shared_user"


def is_navee(info: bluetooth.BluetoothServiceInfoBleak) -> bool:
    """Same filter the Volt app scans with: the NAVEE service or a NAVEE name."""
    return protocol.SERVICE_UUID in info.service_uuids or "navee" in (info.name or "").lower()


IDENTITY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_IDENTITY): TextSelector(TextSelectorConfig(multiline=True)),
        vol.Optional(CONF_SHARED_USER, default=False): BooleanSelector(),
    }
)


class NaveeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pair Home Assistant with a NAVEE scooter using the owner's identity."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery: bluetooth.BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, bluetooth.BluetoothServiceInfoBleak] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> NaveeOptionsFlow:
        return NaveeOptionsFlow()

    async def async_step_bluetooth(
        self, discovery_info: bluetooth.BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": self._name(discovery_info)}
        return await self.async_step_identity()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            self._discovery = self._discovered[address]
            return await self.async_step_identity()

        current = self._async_current_ids(include_ignore=False)
        for info in bluetooth.async_discovered_service_info(self.hass, connectable=True):
            if info.address not in current and is_navee(info):
                self._discovered[info.address] = info
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            address: f"{self._name(info)} ({address})"
                            for address, info in self._discovered.items()
                        }
                    )
                }
            ),
        )

    async def async_step_identity(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._discovery is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._validate(self._discovery.address, user_input, errors)
            if result is not None:
                owner, serial = result
                pid = protocol.serial_pid(serial)
                return self.async_create_entry(
                    title=f"{MANUFACTURER} {model_name(pid)}",
                    data={
                        CONF_ADDRESS: self._discovery.address,
                        CONF_IDENTITY: owner.identity,
                        CONF_ROLE: owner.role,
                        CONF_SERIAL: serial,
                        CONF_MODEL_ID: pid,
                    },
                )
        return self.async_show_form(
            step_id="identity",
            data_schema=self.add_suggested_values_to_schema(IDENTITY_SCHEMA, user_input),
            errors=errors,
            description_placeholders={"name": self._name(self._discovery)},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._validate(
                entry.data[CONF_ADDRESS], user_input, errors, entry.data.get(CONF_SERIAL)
            )
            if result is not None:
                owner, _serial = result
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_IDENTITY: owner.identity, CONF_ROLE: owner.role},
                )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=IDENTITY_SCHEMA, errors=errors
        )

    async def _validate(
        self,
        address: str,
        user_input: dict[str, Any],
        errors: dict[str, str],
        expected_serial: str | None = None,
    ) -> tuple[identity_parser.OwnerIdentity, str] | None:
        try:
            owner = identity_parser.parse(user_input[CONF_IDENTITY])
        except identity_parser.IdentityError as err:
            errors[CONF_IDENTITY] = (
                "ambiguous_identity" if str(err) == "ambiguous" else "invalid_identity"
            )
            return None
        if user_input.get(CONF_SHARED_USER):
            owner = identity_parser.OwnerIdentity(owner.identity, protocol.ROLE_SHARED)

        device = bluetooth.async_ble_device_from_address(self.hass, address, connectable=True)
        if device is None:
            errors["base"] = "not_in_range"
            return None
        client = NaveeClient(owner.identity, owner.role, expected_serial)
        try:
            await client.connect(device)
            serial = client.state.serial
        except NaveeAuthError:
            errors["base"] = "auth_rejected"
            return None
        except NaveeIdentityError:
            errors["base"] = "wrong_scooter"
            return None
        except NaveeError as err:
            _LOGGER.debug("Connection to %s failed: %s", address, err)
            errors["base"] = "cannot_connect"
            return None
        finally:
            await client.disconnect()
        assert serial is not None
        return owner, serial

    @staticmethod
    def _name(info: bluetooth.BluetoothServiceInfoBleak) -> str:
        pid = protocol.advertised_pid(info.manufacturer_data)
        if pid and pid.startswith("2538"):
            return f"{MANUFACTURER} UT5 MAX"
        return info.name or f"{MANUFACTURER} scooter"


class NaveeOptionsFlow(OptionsFlow):
    """Polling and connection behaviour."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            user_input[CONF_POLL_INTERVAL] = int(user_input[CONF_POLL_INTERVAL])
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POLL_INTERVAL,
                        default=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_POLL_INTERVAL,
                            max=MAX_POLL_INTERVAL,
                            step=1,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_KEEP_CONNECTED,
                        default=options.get(CONF_KEEP_CONNECTED, DEFAULT_KEEP_CONNECTED),
                    ): BooleanSelector(),
                }
            ),
        )
