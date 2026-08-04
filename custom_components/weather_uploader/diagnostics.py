"""Diagnostics for the Weather Network Uploader.

Home Assistant's "Download diagnostics" button dumps a redacted snapshot
of the integration's state -- config, per-network results and errors,
throttle timing, and the sensor mapping -- so a user reporting a problem
can attach it to an issue without hand-copying logs. Credentials and
coordinates are redacted, matching how the rest of the integration
treats them.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_KEY, CONF_LATITUDE, CONF_LONGITUDE
from .coordinator import UploadCoordinator

# Fields to redact from the config-entry dump. Credentials must never
# appear; coordinates are treated as sensitive throughout the
# integration (rounded pre-fill, masked in the CWOP log), so they are
# redacted here too.
TO_REDACT = {CONF_KEY, CONF_LATITUDE, CONF_LONGITUDE}


def _isoformat(value: Any) -> Any:
    """Render a datetime as ISO 8601; pass other values through."""
    return value.isoformat() if hasattr(value, "isoformat") else value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return a redacted diagnostics snapshot for the config entry."""
    coordinator: UploadCoordinator = entry.runtime_data
    data = coordinator.data or {}

    # Per-network status. Payloads and raw error messages are omitted:
    # the payload can carry coordinates (CWOP) and the message is already
    # summarised by its code, so the code plus timings are the useful,
    # low-risk fields for a bug report.
    networks: dict[str, dict[str, Any]] = {}
    for uploader in coordinator.uploaders:
        name = uploader.name
        networks[name] = {
            "min_interval": uploader.min_interval,
            "result": data.get("results", {}).get(name),
            "error_code": data.get("error_codes", {}).get(name),
            "last_error_time": _isoformat(data.get("error_times", {}).get(name)),
            "last_success": _isoformat(data.get("success_times", {}).get(name)),
            "measurements_sent": data.get("counts", {}).get(name),
        }

    return {
        "entry": {
            "data": async_redact_data(entry.data, TO_REDACT),
            "options": async_redact_data(entry.options, TO_REDACT),
        },
        "coordinator": {
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "max_sensor_age": coordinator.max_sensor_age,
            "mapped_fields": sorted(coordinator._map),
            "last_update_success": coordinator.last_update_success,
        },
        "networks": networks,
    }
