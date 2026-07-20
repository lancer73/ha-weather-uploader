"""Data coordinator for the Weather Network Uploader."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfLength,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfVolumetricFlux,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import (
    DistanceConverter,
    PressureConverter,
    SpeedConverter,
    TemperatureConverter,
)

from .const import (
    CONF_MAX_SENSOR_AGE,
    DEFAULT_MAX_SENSOR_AGE,
    DOMAIN,
    PLAUSIBLE_RANGE,
    SENSOR_KEYS,
)
from .uploaders import BaseUploader

_LOGGER = logging.getLogger(__name__)

_INVALID_STATES = {STATE_UNKNOWN, STATE_UNAVAILABLE, "", "none", "None"}


def _reported_at(state: State) -> datetime:
    """Return when an entity last reported, changed or not.

    This must be ``last_reported``, never ``last_updated``.

    Home Assistant's state machine discards a write when neither the
    state nor its attributes changed: it refreshes ``last_reported``,
    fires ``state_reported``, and returns without touching
    ``last_updated``. So ``last_updated`` answers "when did this value
    last change", which is not the question a staleness check asks.

    The distinction is not academic for weather data, where holding a
    constant value is the normal case:

    - a rain sensor reads 0.0 for days between showers
    - solar radiation and UV read 0.0 every night
    - wind speed reads 0.0 on a calm night

    All of those have an old ``last_updated`` while being perfectly
    healthy. Judging them by it would drop rain from nearly every
    payload and drop solar and UV every single night.

    Worse, ``last_updated`` cannot tell a healthy dry rain sensor from
    a dead station -- both show an old timestamp. Only
    ``last_reported`` separates them: the healthy sensor reported
    seconds ago, the dead one did not.

    ``last_reported`` arrived in Home Assistant 2024.4. The manifest
    requires a newer version than that, but the fallback keeps this
    honest rather than raising AttributeError on an old core.
    """
    return getattr(state, "last_reported", None) or state.last_updated


def _is_plausible(key: str, value: float) -> bool:
    """Return True when a converted value is within the field's range.

    The value is already in internal units here, so the bounds in
    PLAUSIBLE_RANGE apply directly. A field with no range entry, or a
    None bound, is unconstrained on that side. The ranges are wide by
    design: they exist to catch a mis-mapping or a wrong unit (a
    pressure of 101325 where hPa is expected, humidity feeding a
    temperature field), not to judge real weather.
    """
    bounds = PLAUSIBLE_RANGE.get(key)
    if bounds is None:
        return True
    low, high = bounds
    if low is not None and value < low:
        return False
    return not (high is not None and value > high)


# Target unit per sensor key. Keys absent here are passed through as-is.
_CONVERSIONS: dict[str, tuple[Any, str]] = {
    "temperature": (TemperatureConverter, UnitOfTemperature.CELSIUS),
    "dewpoint": (TemperatureConverter, UnitOfTemperature.CELSIUS),
    "indoor_temperature": (TemperatureConverter, UnitOfTemperature.CELSIUS),
    "soil_temperature": (TemperatureConverter, UnitOfTemperature.CELSIUS),
    "pressure_absolute": (PressureConverter, UnitOfPressure.HPA),
    "pressure_relative": (PressureConverter, UnitOfPressure.HPA),
    "wind_speed": (SpeedConverter, UnitOfSpeed.METERS_PER_SECOND),
    "wind_gust": (SpeedConverter, UnitOfSpeed.METERS_PER_SECOND),
    "visibility": (DistanceConverter, UnitOfLength.KILOMETERS),
    "lightning_distance": (DistanceConverter, UnitOfLength.KILOMETERS),
    "cloud_base": (DistanceConverter, UnitOfLength.METERS),
    "rain_rate": (SpeedConverter, UnitOfVolumetricFlux.MILLIMETERS_PER_HOUR),
    "rain_hourly": (DistanceConverter, UnitOfLength.MILLIMETERS),
    "rain_24h": (DistanceConverter, UnitOfLength.MILLIMETERS),
    "rain_daily": (DistanceConverter, UnitOfLength.MILLIMETERS),
    "rain_weekly": (DistanceConverter, UnitOfLength.MILLIMETERS),
    "rain_monthly": (DistanceConverter, UnitOfLength.MILLIMETERS),
    "rain_yearly": (DistanceConverter, UnitOfLength.MILLIMETERS),
}


class UploadCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Read mapped sensors on an interval and push to every uploader."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        uploaders: list[BaseUploader],
        interval: int,
    ) -> None:
        """Initialise the coordinator."""
        # config_entry is required: recent Home Assistant refuses
        # async_config_entry_first_refresh() unless the coordinator is
        # linked to its entry. Omitting it fails setup with a misleading
        # "No setup function defined" error. Needs HA >= 2024.8.
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )
        # self.config_entry is set by the base class above; keep a plain
        # alias for readability in entities that reference it.
        self.entry = entry
        self.uploaders = uploaders
        # entry.data holds the initial mapping and settings AND the
        # networks/credentials. entry.options, once the user saves the
        # settings form, holds the mapping and settings only. A plain
        # {**data, **options} union cannot express "unmap a sensor": a
        # key cleared in the form is absent from options, so the union
        # falls back to the value still in data. So once options has been
        # saved, treat it as authoritative for the mapping and settings,
        # while the networks always come from data.
        if entry.options:
            mapping_source = entry.options
            settings_source = entry.options
        else:
            mapping_source = entry.data
            settings_source = entry.data
        self._map: dict[str, str] = {
            key: mapping_source[key]
            for key in SENSOR_KEYS
            if mapping_source.get(key)
        }
        self.max_sensor_age = int(
            settings_source.get(CONF_MAX_SENSOR_AGE, DEFAULT_MAX_SENSOR_AGE)
        )
        self._warned: set[str] = set()
        # Populated by read_sensors() on every cycle, for diagnostics.
        self.stale_sensors: list[str] = []
        self.missing_sensors: list[str] = []
        self.implausible_sensors: list[str] = []

    def read_sensors(self) -> dict[str, float]:
        """Collect, validate, and normalize every mapped sensor value.

        Four ways a mapped entity can fail, all handled here rather
        than left for a provider to reject:

        - it no longer exists (renamed, or its integration removed)
        - it is explicitly unknown or unavailable
        - its state is not a number
        - **it has stopped reporting**

        The last is the dangerous one. Home Assistant retains the last
        value of a sensor that silently stops reporting, so a station
        with a dead battery is indistinguishable from a station
        reporting an unchanging value. Without an age check we would
        republish that reading as a current observation forever, and
        every upload would succeed while doing it.

        Staleness is judged on ``last_reported``, not ``last_updated``
        -- see :func:`_reported_at` for why that distinction decides
        whether this check protects users or silently deletes their
        rain data.
        """
        result: dict[str, float] = {}
        stale: list[str] = []
        missing: list[str] = []
        implausible: list[str] = []
        now = dt_util.utcnow()

        for key, entity_id in self._map.items():
            state = self.hass.states.get(entity_id)
            if state is None:
                missing.append(key)
                if entity_id not in self._warned:
                    _LOGGER.warning(
                        "Mapped entity %s (%s) does not exist; skipping it",
                        entity_id,
                        key,
                    )
                    self._warned.add(entity_id)
                continue

            if state.state in _INVALID_STATES:
                missing.append(key)
                continue

            try:
                value = float(state.state)
            except (TypeError, ValueError):
                missing.append(key)
                if entity_id not in self._warned:
                    _LOGGER.warning(
                        "Mapped entity %s (%s) is not numeric: %s",
                        entity_id,
                        key,
                        state.state,
                    )
                    self._warned.add(entity_id)
                continue

            if self.max_sensor_age > 0:
                age = (now - _reported_at(state)).total_seconds()
                if age > self.max_sensor_age:
                    stale.append(key)
                    _LOGGER.debug(
                        "Mapped entity %s (%s) last reported %.0fs ago, "
                        "exceeding the %ds limit; not publishing it",
                        entity_id,
                        key,
                        age,
                        self.max_sensor_age,
                    )
                    continue

            value = self._convert(
                key, value, state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
            )
            if value is None:
                missing.append(key)
                continue

            if not _is_plausible(key, value):
                implausible.append(key)
                _LOGGER.warning(
                    "Mapped entity %s (%s) reported %.4g, outside the "
                    "plausible range for this field; not publishing it. "
                    "This usually means the wrong sensor is mapped, or its "
                    "unit differs from what Home Assistant reports.",
                    entity_id,
                    key,
                    value,
                )
                continue

            result[key] = value

        self.stale_sensors = stale
        self.missing_sensors = missing
        self.implausible_sensors = implausible
        if stale:
            _LOGGER.warning(
                "Not publishing %d stale reading(s): %s. Their entities have "
                "not updated in over %ds, so the values are no longer "
                "current observations.",
                len(stale),
                ", ".join(sorted(stale)),
                self.max_sensor_age,
            )
        return result

    def _convert(self, key: str, value: float, unit: str | None) -> float | None:
        """Convert a value to the integration's internal unit."""
        conversion = _CONVERSIONS.get(key)
        if conversion is None or unit is None:
            return value
        converter, target = conversion
        if unit == target:
            return value
        try:
            return converter.convert(value, unit, target)
        except (ValueError, TypeError, HomeAssistantError) as err:
            # HA's unit converters raise HomeAssistantError (e.g.
            # UnitConversionError) for an unrecognized unit, and its MRO
            # is only Exception -- not ValueError -- so it must be caught
            # explicitly. Missing it would let one sensor with an
            # unexpected unit fail the entire coordinator refresh every
            # tick, for all networks, instead of dropping just that field.
            if key not in self._warned:
                _LOGGER.warning(
                    "Cannot convert %s from %s to %s: %s", key, unit, target, err
                )
                self._warned.add(key)
            return None

    async def _async_update_data(self) -> dict[str, Any]:
        """Read sensors and upload to every network that is due.

        The poll cadence is global, but each network throttles itself
        against its own minimum interval. A network that is not due is
        skipped for this tick and keeps its previous status, so a fast
        poll cannot trip a slow provider's rate limit.
        """
        data = self.read_sensors()
        if not data:
            if self.stale_sensors:
                _LOGGER.warning(
                    "Every mapped sensor is stale; publishing nothing. "
                    "Check whether the weather station is still reporting."
                )
            else:
                _LOGGER.debug("No usable sensor data, skipping upload cycle")
            return self._carry_forward({})

        due = [uploader for uploader in self.uploaders if uploader.is_due()]
        if not due:
            _LOGGER.debug("No network due this tick")
            return self._carry_forward(data)

        outcomes = await asyncio.gather(
            *(uploader.send(data) for uploader in due),
            return_exceptions=True,
        )

        previous = self.data or {}
        results: dict[str, bool] = dict(previous.get("results", {}))
        errors: dict[str, str | None] = dict(previous.get("errors", {}))
        payloads: dict[str, dict[str, Any]] = dict(previous.get("payloads", {}))
        counts: dict[str, int] = dict(previous.get("counts", {}))

        for uploader, outcome in zip(due, outcomes, strict=True):
            # A failed attempt still consumed the provider's budget, so
            # throttle on attempt rather than on success.
            uploader.mark_sent()
            # Record what this network actually sent -- the payload
            # captured during send(), not a rebuild (which would recompute
            # timestamps and differ from what went on the wire). Already
            # credential-redacted by the uploader.
            payloads[uploader.name] = uploader.last_payload
            # And how many weather measurements that represents, counted
            # consistently across networks (see measurement_count).
            counts[uploader.name] = uploader.measurement_count(data)
            if isinstance(outcome, BaseException):
                _LOGGER.error("%s raised unexpectedly: %s", uploader.name, outcome)
                results[uploader.name] = False
                errors[uploader.name] = str(outcome)
            else:
                results[uploader.name] = outcome
                errors[uploader.name] = uploader.last_error

        return {
            "data": data,
            "results": results,
            "errors": errors,
            "payloads": payloads,
            "counts": counts,
        }

    def _carry_forward(self, data: dict[str, float]) -> dict[str, Any]:
        """Return prior statuses unchanged, with new sensor data."""
        previous = self.data or {}
        return {
            "data": data,
            "results": dict(previous.get("results", {})),
            "errors": dict(previous.get("errors", {})),
            "payloads": dict(previous.get("payloads", {})),
            "counts": dict(previous.get("counts", {})),
        }

    @property
    def data_is_fresh(self) -> bool:
        """Return True when at least one mapped sensor is current.

        Distinct from upload success: a network can accept our data
        happily while that data is a dead station's last reading.
        """
        if not self._map:
            return False
        return bool(self.data and self.data.get("data"))
