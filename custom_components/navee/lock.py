"""Electronic lock for NAVEE scooters."""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NaveeConfigEntry
from .entity import NaveeControlEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NaveeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([NaveeLock(entry.runtime_data, "lock")])


class NaveeLock(NaveeControlEntity, LockEntity):
    """Locking needs the scooter parked; unlocking only needs a connection."""

    @property
    def is_locked(self) -> bool | None:
        value = self.coordinator.data.lock_status
        return None if value is None else value != 0

    async def async_lock(self, **kwargs: Any) -> None:
        await self._async_set(1)

    async def async_unlock(self, **kwargs: Any) -> None:
        await self._async_set(0)
