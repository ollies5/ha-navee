"""The NAVEE scooter integration (local Bluetooth)."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import NaveeConfigEntry, NaveeCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.LOCK,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: NaveeConfigEntry) -> bool:
    """Set up a NAVEE scooter from a config entry.

    A sleeping scooter is normal: setup does not wait for it. Entities start from restored
    values and the first advertisement connects.
    """
    coordinator = NaveeCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(coordinator.async_start())
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NaveeConfigEntry) -> bool:
    """Unload a config entry and release the scooter's Bluetooth connection."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: NaveeConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
