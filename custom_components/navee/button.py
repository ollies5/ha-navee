"""Buttons for NAVEE scooters."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import UT5_MAX_PID
from .coordinator import NaveeConfigEntry
from .entity import NaveeEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NaveeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    if coordinator.pid == UT5_MAX_PID:
        async_add_entities([NaveeSyncClockButton(coordinator, "sync_clock")])


class NaveeSyncClockButton(NaveeEntity, ButtonEntity):
    """Set the scooter clock to Home Assistant's local time (no readback exists)."""

    _attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        await self.coordinator.async_sync_clock()
