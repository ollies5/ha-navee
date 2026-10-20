"""Choice settings for NAVEE scooters."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
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
        NaveeSelect(coordinator, key)
        for key, control in coordinator.controls().items()
        if control.kind is Kind.CHOICE
    )


class NaveeSelect(NaveeControlEntity, SelectEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: NaveeCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        assert self._control.options is not None
        self._options = self._control.options
        self._attr_options = list(self._options.values())

    @property
    def current_option(self) -> str | None:
        return self._options.get(self.current) if self.current is not None else None

    async def async_select_option(self, option: str) -> None:
        value = next(v for v, name in self._options.items() if name == option)
        await self._async_set(value)
