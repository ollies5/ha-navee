"""Electronic lock for NAVEE scooters."""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity, LockState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import NaveeConfigEntry
from .entity import NaveeControlEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NaveeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([NaveeLock(entry.runtime_data, "lock")])


class NaveeLock(NaveeControlEntity, LockEntity, RestoreEntity):
    """Locking needs the scooter parked; unlocking only needs a connection.

    The scooter stays locked while asleep, so the last known state is kept (and restored after
    a restart). Commands while it is asleep fail with a clear error.
    """

    _restored: bool | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in (LockState.LOCKED, LockState.UNLOCKED):
            self._restored = last.state == LockState.LOCKED

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self.is_locked is not None

    @property
    def is_locked(self) -> bool | None:
        value = self.coordinator.data.lock_status
        return self._restored if value is None else value != 0

    async def async_lock(self, **kwargs: Any) -> None:
        await self._async_set(1)

    async def async_unlock(self, **kwargs: Any) -> None:
        await self._async_set(0)
