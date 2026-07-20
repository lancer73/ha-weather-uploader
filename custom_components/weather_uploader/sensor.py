"""Sensor platform: a per-network last-error status sensor.

Each network gets one text sensor whose state is a short, stable code
for its last upload result (``ok`` on success, or ``timeout``, ``dns``,
``http_500`` and so on). Because this is the entity *state* -- not an
attribute -- the recorder keeps a history of it, so intermittent
failures such as a DNS timeout leave a durable trail you can graph and
correlate. The full message and the time of the last error are exposed
as attributes for detail.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_LAST_ERROR,
    ATTR_LAST_ERROR_TIME,
    DOMAIN,
)
from .coordinator import UploadCoordinator

# The state when the last send succeeded. A stable, low-cardinality set
# of states (ok + a handful of error codes) keeps the recorder history
# meaningful.
STATE_OK = "ok"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one error-status sensor per network."""
    coordinator: UploadCoordinator = entry.runtime_data
    async_add_entities(
        UploadErrorSensor(coordinator, uploader.name)
        for uploader in coordinator.uploaders
    )


class UploadErrorSensor(CoordinatorEntity[UploadCoordinator], SensorEntity):
    """The last upload result for one network, as a recordable state.

    State is a short code: ``ok`` on success, otherwise a stable error
    code (``timeout``, ``dns``, ``connection``, ``tls``, ``http_<n>`` ...).
    The message and last-error time are attributes.
    """

    _attr_has_entity_name = True
    # A plain enumerated text state. It is low-cardinality by design, so
    # it records well; not a measurement, so no state_class.

    def __init__(self, coordinator: UploadCoordinator, service_name: str) -> None:
        """Initialise the sensor for one network."""
        super().__init__(coordinator)
        self._service_name = service_name
        self._attr_name = f"{service_name} last error"
        self._attr_unique_id = (
            f"{coordinator.entry.entry_id}_{service_name}_last_error".replace(
                " ", "_"
            ).lower()
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name="Weather Network Uploader",
            manufacturer="lancer73",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> str:
        """Return the short code for this network's last upload result."""
        data = self.coordinator.data or {}
        code = data.get("error_codes", {}).get(self._service_name)
        return code if code else STATE_OK

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the full message and the time of the last error.

        The message is already credential-redacted by the uploader. The
        time is that of the most recent error, left in place after a
        later success so the trail of when problems happened is visible.
        """
        data = self.coordinator.data or {}
        error_time = data.get("error_times", {}).get(self._service_name)
        return {
            ATTR_LAST_ERROR: data.get("errors", {}).get(self._service_name),
            ATTR_LAST_ERROR_TIME: error_time.isoformat() if error_time else None,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Write the new state when the coordinator refreshes."""
        self.async_write_ha_state()
