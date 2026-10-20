"""Config flow and setup against a simulated scooter."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest
from bleak.backends.device import BLEDevice
from habluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER, ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.navee import protocol
from custom_components.navee.const import CONF_IDENTITY, CONF_ROLE, CONF_SERIAL, DOMAIN

from .fake_scooter import IDENTITY, SERIAL, FakeBleakClient, FakeScooter

ADDRESS = "AA:BB:CC:DD:EE:FF"
DEVICE = BLEDevice(ADDRESS, "NAVEE", None)
INFO = BluetoothServiceInfoBleak(
    name="NAVEE",
    address=ADDRESS,
    rssi=-60,
    manufacturer_data={0xEA05: bytes((0x09, 1, 2, 3))},
    service_data={},
    service_uuids=[protocol.SERVICE_UUID],
    source="local",
    device=DEVICE,
    advertisement=None,
    connectable=True,
    time=0,
    tx_power=None,
)


@pytest.fixture(autouse=True)
def bluetooth_ready(mock_bluetooth: None) -> None:
    """Bluetooth is set up for every test."""


@pytest.fixture
def scooter() -> Generator[FakeScooter]:
    scooter = FakeScooter()

    async def establish(*_args, **_kwargs):
        return FakeBleakClient(scooter)

    with (
        patch("custom_components.navee.client.establish_connection", establish),
        patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address",
            return_value=DEVICE,
        ),
        patch(
            "homeassistant.components.bluetooth.async_discovered_service_info",
            return_value=[INFO],
        ),
    ):
        yield scooter


async def test_user_flow(hass: HomeAssistant, scooter: FakeScooter) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    assert result["step_id"] == "identity"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_IDENTITY: "not an identity"}
    )
    assert result["errors"] == {CONF_IDENTITY: "invalid_identity"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_IDENTITY: "111111111111"}
    )
    assert result["errors"] == {"base": "auth_rejected"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_IDENTITY: f"identity: {IDENTITY.lower()}\nrole: 0\n"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "NAVEE UT5 MAX"
    assert result["data"] == {
        CONF_ADDRESS: ADDRESS,
        CONF_IDENTITY: IDENTITY,
        CONF_ROLE: 0,
        CONF_SERIAL: SERIAL,
        "model_id": "2538",
    }
    await hass.async_block_till_done()


async def test_bluetooth_discovery(hass: HomeAssistant, scooter: FakeScooter) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=INFO
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "identity"
    assert result["description_placeholders"] == {"name": "NAVEE UT5 MAX"}


async def setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={
            CONF_ADDRESS: ADDRESS,
            CONF_IDENTITY: IDENTITY,
            CONF_ROLE: 0,
            CONF_SERIAL: SERIAL,
            "model_id": "2538",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_entities_and_controls(hass: HomeAssistant, scooter: FakeScooter) -> None:
    entry = await setup_entry(hass)
    assert entry.state is ConfigEntryState.LOADED

    assert hass.states.get("sensor.navee_ut5_max_battery").state == "67"
    assert hass.states.get("binary_sensor.navee_ut5_max_charging").state == "on"
    assert hass.states.get("lock.navee_ut5_max_lock").state == "unlocked"
    assert hass.states.get("select.navee_ut5_max_charge_limit").state == "100"
    assert hass.states.get("switch.navee_ut5_max_power_assist").state == "off"

    with pytest.raises(HomeAssistantError, match="motion report"):
        await hass.services.async_call(
            "lock", "lock", {ATTR_ENTITY_ID: "lock.navee_ut5_max_lock"}, blocking=True
        )

    scooter.push_motion()
    await hass.async_block_till_done()
    await hass.services.async_call(
        "lock", "lock", {ATTR_ENTITY_ID: "lock.navee_ut5_max_lock"}, blocking=True
    )
    assert scooter.settings[2] == 1
    assert hass.states.get("lock.navee_ut5_max_lock").state == "locked"

    scooter.push_motion()
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: "select.navee_ut5_max_charge_limit", "option": "85"},
        blocking=True,
    )
    assert scooter.settings[31] == 85
    assert hass.states.get("select.navee_ut5_max_charge_limit").state == "85"

    client = entry.runtime_data.client
    assert client.connected
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not client.connected


async def test_other_model_gets_lock_only(hass: HomeAssistant, scooter: FakeScooter) -> None:
    scooter.serial = "N2443DE0000000001"
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_IDENTITY: IDENTITY, CONF_ROLE: 0},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("lock.navee_model_2443_lock") is not None
    assert hass.states.get("sensor.navee_model_2443_battery").state == "67"
    assert not hass.states.async_entity_ids("switch")
    assert not hass.states.async_entity_ids("select")


async def test_rejected_identity_starts_reauth(hass: HomeAssistant, scooter: FakeScooter) -> None:
    scooter.identity = bytes.fromhex("111111111111")
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        data={CONF_ADDRESS: ADDRESS, CONF_IDENTITY: IDENTITY, CONF_ROLE: 0, CONF_SERIAL: SERIAL},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]
