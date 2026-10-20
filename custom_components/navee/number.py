"""Numeric settings for NAVEE scooters."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controls import Kind
from .coordinator import NaveeConfigEntry, NaveeCoordinator
from .entity import NaveeControlEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NaveeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        NaveeNumber(coordinator, key)
        for key, control in coordinator.controls().items()
        if control.kind is Kind.SLIDER
    )


class NaveeNumber(NaveeControlEntity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1

    def __init__(self, coordinator: NaveeCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_native_min_value = self._control.minimum
        self._attr_native_max_value = self._control.maximum
        # Whole km/h steps as the scooter takes them; no unit conversion to mph.
        self._attr_native_unit_of_measurement = self._control.unit

    @property
    def native_value(self) -> float | None:
        return self.current

    async def async_set_native_value(self, value: float) -> None:
        await self._async_set(round(value))
