"""Diagnostics for NAVEE scooters. The owner identity and serial are always redacted."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from .const import CONF_IDENTITY, CONF_SERIAL
from .coordinator import NaveeConfigEntry

TO_REDACT = {CONF_IDENTITY, CONF_SERIAL, CONF_ADDRESS, "serial"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NaveeConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    state = asdict(coordinator.client.state)
    settings = state.pop("settings")
    state = {k: v for k, v in state.items() if not k.startswith("_")}
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "model_id": coordinator.pid,
        "connected": coordinator.client.connected,
        "awake": coordinator.awake,
        "consecutive_connect_failures": coordinator._failures,
        "controls": sorted(coordinator.controls()),
        "state": async_redact_data(state, TO_REDACT),
        "vehicle_settings_hex": settings.hex() if settings else None,
    }
