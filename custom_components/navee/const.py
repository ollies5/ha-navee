"""Constants for the NAVEE integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "navee"

CONF_IDENTITY = "identity"
CONF_ROLE = "role"
CONF_SERIAL = "serial"
CONF_MODEL_ID = "model_id"

CONF_POLL_INTERVAL = "poll_interval"
CONF_KEEP_CONNECTED = "keep_connected"

DEFAULT_POLL_INTERVAL = 30
MIN_POLL_INTERVAL = 10
MAX_POLL_INTERVAL = 3600
DEFAULT_KEEP_CONNECTED = True

# Pushed ride reports can arrive several times a second; entity state updates are throttled.
PUSH_UPDATE_INTERVAL = timedelta(seconds=1)

MANUFACTURER = "NAVEE"
UT5_MAX_PID = "2538"
MODEL_NAMES = {UT5_MAX_PID: "UT5 MAX"}


def model_name(pid: str | None) -> str:
    """Display name for a model ID."""
    if not pid:
        return "Scooter"
    return MODEL_NAMES.get(pid, f"Model {pid}")
