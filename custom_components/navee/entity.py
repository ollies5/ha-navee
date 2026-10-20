"""Base entities for NAVEE scooters."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SERIAL, DOMAIN, MANUFACTURER, model_name
from .coordinator import NaveeCoordinator


class NaveeEntity(CoordinatorEntity[NaveeCoordinator]):
    """An entity belonging to one scooter.

    Available unless the configured scooter could not be verified. Entities showing live
    readings or offering controls also need the scooter reachable (see ``NaveeLiveEntity``).
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: NaveeCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_{key}"
        self._attr_translation_key = key
        state = coordinator.client.state
        pid = coordinator.pid
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            manufacturer=MANUFACTURER,
            model=model_name(pid),
            model_id=pid or None,
            serial_number=state.serial or coordinator.config_entry.data.get(CONF_SERIAL),
            sw_version=state.firmware.get("meter"),
            name=f"{MANUFACTURER} {model_name(pid)}",
        )


class NaveeLiveEntity(NaveeEntity):
    """Only meaningful while the scooter is reachable (awake and in range)."""

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.awake


class NaveeControlEntity(NaveeLiveEntity):
    """An entity backed by one named scooter control."""

    def __init__(self, coordinator: NaveeCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._control = coordinator.controls()[key]

    @property
    def current(self) -> int | None:
        return self._control.readback(self.coordinator.data.settings)

    @property
    def available(self) -> bool:
        return super().available and self.current is not None

    async def _async_set(self, value: int) -> None:
        await self.coordinator.async_set_control(self._control.key, value)
