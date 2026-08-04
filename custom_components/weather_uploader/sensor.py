"""Sensor platform: per-network status sensors.

Each network gets two sensors:

- a last-error status sensor whose state is a short, stable code for the
  last upload result (``ok`` on success, or ``timeout``, ``dns``,
  ``http_500`` ...), so the recorder keeps a durable, graphable trail of
  intermittent failures; and
- a last-successful-upload timestamp sensor, answering "when did this
  network last actually accept data" -- distinct from the last-error
  time, and useful for alerting on a network that has gone quiet.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

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
    """Set up the per-network status sensors."""
    coordinator: UploadCoordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for uploader in coordinator.uploaders:
        entities.append(UploadErrorSensor(coordinator, uploader.name))
        entities.append(LastSuccessSensor(coordinator, uploader.name))
    async_add_entities(entities)


class _BaseNetworkSensor(
    CoordinatorEntity[UploadCoordinator], RestoreEntity, SensorEntity
):
    """Shared device wiring for a per-network sensor.

    Coordinator state (last error, last success) lives only in memory and
    is rebuilt each cycle, so after a restart it starts empty and every
    per-network sensor would read unknown until the first fresh cycle.
    To bridge that gap, each sensor restores its last state on startup
    and seeds it back into the coordinator's data, so it shows the real
    pre-restart value immediately; the next cycle overwrites it normally.
    """

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: UploadCoordinator, service_name: str, suffix: str
    ) -> None:
        """Initialise the sensor for one network."""
        super().__init__(coordinator)
        self._service_name = service_name
        self._attr_unique_id = (
            f"{coordinator.entry.entry_id}_{service_name}_{suffix}".replace(
                " ", "_"
            ).lower()
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name="Weather Network Uploader",
            manufacturer="lancer73",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        """Restore the last state and seed it into the coordinator.

        Only seeds when the coordinator has not already produced a value
        for this network (a fresh cycle before this runs must always
        win). Restoring into shared coordinator data means all sensors
        keep reading from one source of truth.
        """
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in (None, "unknown", "unavailable"):
            self._restore(last)
        # The restored last-success/last-error times let the coordinator
        # place each throttle from the real last attempt instead of
        # waiting a full interval after this restart.
        self.coordinator.reseed_throttles_from_restored_state()

    def _restore(self, last_state: Any) -> None:
        """Seed the coordinator with a restored state. Overridden per sensor."""

    @callback
    def _handle_coordinator_update(self) -> None:
        """Write the new state when the coordinator refreshes."""
        self.async_write_ha_state()


class UploadErrorSensor(_BaseNetworkSensor):
    """The last upload result for one network, as a recordable state.

    State is a short code: ``ok`` on success, otherwise a stable error
    code (``timeout``, ``dns``, ``connection``, ``tls``, ``http_<n>`` ...).
    The message and last-error time are attributes.
    """

    # A plain enumerated text state. It is low-cardinality by design, so
    # it records well; not a measurement, so no state_class.

    def __init__(self, coordinator: UploadCoordinator, service_name: str) -> None:
        """Initialise the error sensor for one network."""
        super().__init__(coordinator, service_name, "last_error")
        # Translated name composed from the network via a placeholder, so
        # the per-network sensor names follow the UI language instead of
        # being hard-coded English.
        self._attr_translation_key = "last_error"
        self._attr_translation_placeholders = {"network": service_name}

    def _restore(self, last_state: Any) -> None:
        """Seed the restored error code, message, and time into the coordinator.

        The state is the short code; ``ok`` maps back to no error (None).
        The message and time come from the restored attributes. The
        matching uploader is seeded too, so the first successful cycle
        after a restart does not overwrite the restored last-error time
        with the fresh object's ``None``.
        """
        data = self.coordinator.data or {}
        name = self._service_name
        if name in data.get("error_codes", {}):
            return  # a fresh cycle already ran; it wins

        code = None if last_state.state == STATE_OK else last_state.state
        message = last_state.attributes.get(ATTR_LAST_ERROR)
        error_time_raw = last_state.attributes.get(ATTR_LAST_ERROR_TIME)
        error_time = (
            dt_util.parse_datetime(error_time_raw) if error_time_raw else None
        )
        self.coordinator.data = {
            **data,
            "error_codes": {**data.get("error_codes", {}), name: code},
            "errors": {**data.get("errors", {}), name: message},
            "error_times": {**data.get("error_times", {}), name: error_time},
        }
        # Seed the uploader itself so the next cycle reads the restored
        # values back instead of overwriting them with a fresh None.
        for uploader in self.coordinator.uploaders:
            if uploader.name == name:
                uploader.restore_error_state(
                    code=code, message=message, error_time=error_time
                )
                break

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


class LastSuccessSensor(_BaseNetworkSensor):
    """When this network last actually accepted an upload.

    A timestamp sensor (Home Assistant renders it as relative time, e.g.
    "3 minutes ago"). Distinct from the last-error time: this answers
    "when did it last work", so an automation can alert when a network
    has not succeeded for too long even if it is not currently erroring.
    The value persists across later failures and is ``None`` until the
    first success.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: UploadCoordinator, service_name: str) -> None:
        """Initialise the last-success sensor for one network."""
        super().__init__(coordinator, service_name, "last_success")
        self._attr_translation_key = "last_success"
        self._attr_translation_placeholders = {"network": service_name}

    def _restore(self, last_state: Any) -> None:
        """Seed the restored success timestamp into the coordinator."""
        restored = dt_util.parse_datetime(last_state.state)
        if restored is None:
            return
        data = self.coordinator.data or {}
        success_times = dict(data.get("success_times", {}))
        # A fresh cycle wins: only seed if nothing is there yet.
        if self._service_name not in success_times:
            success_times[self._service_name] = restored
            self.coordinator.data = {**data, "success_times": success_times}

    @property
    def native_value(self) -> datetime | None:
        """Return the time of the last successful upload, or None."""
        data = self.coordinator.data or {}
        return data.get("success_times", {}).get(self._service_name)
