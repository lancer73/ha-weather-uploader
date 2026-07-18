"""Binary sensors reporting upload status and source data health."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_IMPLAUSIBLE_SENSORS,
    ATTR_LAST_ERROR,
    ATTR_LAST_PAYLOAD,
    ATTR_MISSING_SENSORS,
    ATTR_SENSOR_COUNT,
    ATTR_STALE_SENSORS,
    DOMAIN,
)
from .coordinator import UploadCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one status entity per network, plus a data health entity."""
    coordinator: UploadCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[BinarySensorEntity] = [
        UploadStatusEntity(coordinator, uploader.name)
        for uploader in coordinator.uploaders
    ]
    entities.append(SourceDataEntity(coordinator))
    async_add_entities(entities)


class _BaseEntity(CoordinatorEntity[UploadCoordinator], BinarySensorEntity):
    """Shared device grouping for this integration's entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: UploadCoordinator, suffix: str) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{suffix}".replace(
            " ", "_"
        ).lower()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.entry_id)},
            name="Weather Network Uploader",
            manufacturer="lancer73",
            entry_type="service",
        )


class UploadStatusEntity(_BaseEntity):
    """Whether the most recent upload to one network succeeded.

    This answers "is the network accepting our data", which is not the
    same question as "is our data any good". A dead weather station
    produces perfectly successful uploads of stale readings, so this
    entity stays on while :class:`SourceDataEntity` goes off. Both are
    needed to tell the two failures apart.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: UploadCoordinator, service_name: str) -> None:
        """Initialise the status entity."""
        super().__init__(coordinator, service_name)
        self._service_name = service_name
        self._attr_name = f"{service_name} upload"

    @property
    def is_on(self) -> bool | None:
        """Return True when the last upload succeeded.

        None before the first attempt, and while a network is being
        skipped by its own rate limit and has never yet been tried.
        """
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("results", {}).get(self._service_name)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the last error and the payload that was sent.

        The payload holds sensor values only. Credentials are added
        inside each uploader after this dict is built, so they cannot
        surface here, in the states API, or in a template.
        """
        data = self.coordinator.data or {}
        payload = data.get("data", {})
        return {
            ATTR_LAST_ERROR: data.get("errors", {}).get(self._service_name),
            ATTR_SENSOR_COUNT: len(payload),
            ATTR_LAST_PAYLOAD: payload,
        }


class SourceDataEntity(_BaseEntity):
    """Whether the mapped Home Assistant sensors are usable and current.

    Exists because upload success is a misleading health signal on its
    own. If a weather station stops reporting, Home Assistant retains
    its last value, every upload keeps succeeding, and every status
    entity stays green while the published observations are hours old.

    This entity goes to ``on`` (problem) when nothing publishable
    remains, and names the stale, missing, and implausible fields in
    its attributes so the cause is visible without reading the log.
    An implausible field is one whose value fell outside the sane range
    for that measurement -- usually a wrong sensor mapping or a unit
    mismatch.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_name = "Source data problem"

    def __init__(self, coordinator: UploadCoordinator) -> None:
        """Initialise the data health entity."""
        super().__init__(coordinator, "source_data")

    @property
    def is_on(self) -> bool | None:
        """Return True when there is no fresh data left to publish."""
        if self.coordinator.data is None:
            return None
        return not self.coordinator.data_is_fresh

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Name the fields that are stale or unusable, and why."""
        data = self.coordinator.data or {}
        return {
            ATTR_SENSOR_COUNT: len(data.get("data", {})),
            ATTR_STALE_SENSORS: sorted(self.coordinator.stale_sensors),
            ATTR_MISSING_SENSORS: sorted(self.coordinator.missing_sensors),
            ATTR_IMPLAUSIBLE_SENSORS: sorted(self.coordinator.implausible_sensors),
            "max_sensor_age": self.coordinator.max_sensor_age,
        }
