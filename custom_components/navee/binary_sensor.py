"""Binary sensors for NAVEE scooters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NaveeConfigEntry, NaveeCoordinator
from .entity import NaveeEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class NaveeBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[NaveeCoordinator], bool | None]
    always_available: bool = False


BINARY_SENSORS: tuple[NaveeBinarySensorDescription, ...] = (
    NaveeBinarySensorDescription(
        key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda c: c.data.charging,
    ),
    NaveeBinarySensorDescription(
        key="warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda c: None if c.data.warning_code is None else c.data.warning_code != 0,
    ),
    NaveeBinarySensorDescription(
        key="battery_problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: None if c.data.battery_status is None else c.data.battery_status != 0,
    ),
    NaveeBinarySensorDescription(
        key="connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        # Reachable: connected now, or reached by the last poll when not keeping the link open.
        value_fn=lambda c: c.awake,
        always_available=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NaveeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(NaveeBinarySensor(coordinator, d) for d in BINARY_SENSORS)


class NaveeBinarySensor(NaveeEntity, BinarySensorEntity):
    entity_description: NaveeBinarySensorDescription

    def __init__(
        self, coordinator: NaveeCoordinator, description: NaveeBinarySensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        if self.entity_description.always_available:
            return True
        # Charging, warnings and battery status are only known while the scooter is reachable.
        return super().available and self.coordinator.awake

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator)
