"""Sensors for NAVEE scooters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfPower,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NaveeConfigEntry
from .entity import NaveeEntity
from .telemetry import FIRMWARE_PARTS, UT5_MAX_DRIVING_MODES, NaveeState

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class NaveeSensorDescription(SensorEntityDescription):
    value_fn: Callable[[NaveeState], float | int | str | None]
    live: bool = False
    """Unavailable while the scooter is unreachable. Other readings keep their last value."""


SENSORS: tuple[NaveeSensorDescription, ...] = (
    NaveeSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.battery_percent,
    ),
    NaveeSensorDescription(
        key="voltage",
        live=True,
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda s: s.voltage_v,
    ),
    NaveeSensorDescription(
        key="current",
        live=True,
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda s: s.current_a,
    ),
    NaveeSensorDescription(
        key="power",
        live=True,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda s: s.power_w,
    ),
    NaveeSensorDescription(
        key="battery_temperature",
        live=True,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.temperature_c,
    ),
    NaveeSensorDescription(
        key="battery_health",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.battery_health_percent,
    ),
    NaveeSensorDescription(
        key="charge_cycles",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.charge_cycles,
    ),
    NaveeSensorDescription(
        key="rated_capacity",
        native_unit_of_measurement="Ah",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.rated_capacity_ah,
    ),
    NaveeSensorDescription(
        key="speed",
        live=True,
        device_class=SensorDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda s: s.speed_kmh,
    ),
    NaveeSensorDescription(
        key="remaining_range",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.remaining_range_km,
    ),
    NaveeSensorDescription(
        key="trip_distance",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda s: s.trip_km,
    ),
    NaveeSensorDescription(
        key="trip_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.trip_minutes,
    ),
    NaveeSensorDescription(
        key="trip_max_speed",
        device_class=SensorDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.max_speed_kmh,
    ),
    NaveeSensorDescription(
        key="trip_average_speed",
        device_class=SensorDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.average_speed_kmh,
    ),
    NaveeSensorDescription(
        key="odometer",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=lambda s: s.odometer_km,
    ),
    NaveeSensorDescription(
        key="driving_mode",
        live=True,
        device_class=SensorDeviceClass.ENUM,
        options=[*UT5_MAX_DRIVING_MODES.values()],
        value_fn=lambda s: (
            label if (label := s.driving_mode_label()) in UT5_MAX_DRIVING_MODES.values() else None
        ),
    ),
    NaveeSensorDescription(
        key="driving_mode_code",
        live=True,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.driving_mode,
    ),
    NaveeSensorDescription(
        key="warning_code",
        live=True,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.warning_code,
    ),
    NaveeSensorDescription(
        key="charging_state_code",
        live=True,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.charging_state,
    ),
    *(
        NaveeSensorDescription(
            key=f"{part}_firmware",
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            value_fn=lambda s, part=part: s.firmware.get(part),
        )
        for part in FIRMWARE_PARTS
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NaveeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(NaveeSensor(coordinator, description) for description in SENSORS)


class NaveeSensor(NaveeEntity, RestoreSensor):
    """Retained readings show their last known value while the scooter sleeps,
    including after a Home Assistant restart."""

    entity_description: NaveeSensorDescription

    def __init__(self, coordinator, description: NaveeSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._restored: float | int | str | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if not self.entity_description.live and (data := await self.async_get_last_sensor_data()):
            self._restored = data.native_value

    @property
    def available(self) -> bool:
        if self.entity_description.live:
            return super().available and self.coordinator.awake
        return super().available

    @property
    def native_value(self) -> float | int | str | None:
        value = self.entity_description.value_fn(self.coordinator.data)
        if value is None and not self.entity_description.live:
            return self._restored
        return value
