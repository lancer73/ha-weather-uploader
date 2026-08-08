"""Data coordinator for the Weather Network Uploader."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Final

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
from homeassistant.core import (
    CALLBACK_TYPE,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.start import async_at_started
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

# Seconds to wait between one network's upload and the next. The uploads
# used to fire concurrently, which made every network resolve DNS and
# connect at the same instant; on a constrained resolver that burst can
# cause DNS timeouts (CWOP makes it worse -- it does an uncached lookup
# on a raw socket every time, unlike the shared, keep-alive HTTP
# session). Spacing them out keeps well within the 60 s minimum poll
# interval even with every network enabled.
UPLOAD_STAGGER_SECONDS: Final = 5

# Latency margin added to the dispatch window when computing the due-check
# tolerance, covering the network round-trip between a poll tick and when
# the send actually completes and stamps last_sent.
DUE_TOLERANCE_MARGIN: Final = 2

# The post-restart reseed refresh is debounced this many seconds and
# re-armed as each status sensor restores, so it runs once after the last
# one rather than on the first, partial restore. Measured from the last
# restore, not the first, so a generous value costs only a few seconds
# before the first post-restart upload (harmless against 60-300s upload
# intervals) while widening the margin for all sensors to finish
# restoring on a slow startup.
# How long after startup to keep suppressing the source-data problem
# flag while waiting for every mapped sensor to report its first usable
# value. A backstop only: the grace normally ends as soon as all mapped
# sensors have reported, well within this. Long enough to cover a slow
# source integration initialising after homeassistant_started, short
# enough that a genuinely dead sensor is still flagged promptly.
STARTUP_GRACE_TIMEOUT: Final = 120

RESEED_REFRESH_DELAY: Final = 5

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

    Known limitation (accepted, not guarded): a source sensor that
    restores its own state on a Home Assistant restart gets a fresh
    ``last_reported`` at startup, so for up to ``max_sensor_age`` after a
    restart a dead station can look freshly reported and its stale value
    may be published. Guarding this (e.g. against HA's start time) risks
    suppressing valid data after every reboot, a worse failure than the
    narrow window it would close, so it is documented rather than fixed.
    See the "first hour after a restart" note in the README.
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
        # "No setup function defined" error. Needs HA >= 2024.8, and the
        # integration's declared floor is now 2024.11 (for aiohttp's
        # ClientConnectorDNSError; see classify_client_error).
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
            key: mapping_source[key] for key in SENSOR_KEYS if mapping_source.get(key)
        }
        self.max_sensor_age = int(
            settings_source.get(CONF_MAX_SENSOR_AGE, DEFAULT_MAX_SENSOR_AGE)
        )
        self._warned: set[str] = set()
        # Pending debounce for the post-reseed refresh. Re-armed on each
        # sensor restore so the refresh fires once, after every network's
        # success/error pair has restored -- not on the first, partial one.
        self._reseed_refresh_unsub: CALLBACK_TYPE | None = None
        # Unsub for the "run once started" callback that the debounced
        # refresh hands off to (via async_at_started). Stored so a reload
        # before startup cancels it instead of leaving it to fire on a
        # shut-down coordinator.
        self._reseed_started_unsub: CALLBACK_TYPE | None = None
        # Populated by read_sensors() on every cycle, for diagnostics.
        self.stale_sensors: list[str] = []
        self.missing_sensors: list[str] = []
        self.implausible_sensors: list[str] = []
        # Startup grace: mapped entities that have produced a usable value
        # at least once since start. Until every mapped entity has (or the
        # grace deadline passes), the source-data problem flag is
        # suppressed, so a source integration that initialises a little
        # after homeassistant_started does not raise a brief false alarm.
        self._reported_once: set[str] = set()
        self._startup_grace_deadline: float | None = None

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

            # The entity has produced a usable (non-unavailable) state, so
            # it counts as initialised for the startup grace, even if it
            # later fails numeric or plausibility checks -- those are real
            # data problems, not "not ready yet".
            self._reported_once.add(entity_id)

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

        due = [
            uploader
            for uploader in self.uploaders
            if uploader.is_due(tolerance=self._due_tolerance())
        ]
        if not due:
            _LOGGER.debug("No network due this tick")
            return self._carry_forward(data)

        # Upload the shortest-period networks first. The stagger delays
        # each successive network by UPLOAD_STAGGER_SECONDS, which shifts
        # its actual send time later within the cycle. A network with a
        # tight minimum interval (e.g. 60 s) has little slack, so if it
        # were sent late its next-cycle send could fall just under its
        # own floor; a long-period network (e.g. 300 s) has ample slack
        # to absorb the offset. Ordering by ascending minimum interval
        # gives the least slack to the networks that can spare it most.
        due.sort(key=lambda uploader: uploader.min_interval)

        # Send to each due network in turn, spaced by UPLOAD_STAGGER_SECONDS,
        # rather than all at once. Concurrent dispatch made every network
        # resolve DNS and connect simultaneously, which could overwhelm a
        # constrained resolver and cause DNS timeouts. Sequential order is
        # preserved so outcomes line up with `due` below.
        outcomes: list[bool | Exception] = []
        attempt_times: dict[str, Any] = {}
        for index, uploader in enumerate(due):
            if index > 0:
                await asyncio.sleep(UPLOAD_STAGGER_SECONDS)
            try:
                outcomes.append(await uploader.send(data))
            except Exception as err:
                # Capture per-network failures so one crash does not abort
                # the rest of the cycle. CancelledError (a BaseException,
                # not caught here) propagates, so a shutdown or reload
                # cancels the cycle cleanly instead of sleeping on.
                outcomes.append(err)
            # Stamp the attempt time here, right after the send, not in
            # the results loop below: with several staggered networks that
            # loop runs up to (n-1) * stagger seconds later, which would
            # backdate every network's success to the same late instant.
            attempt_times[uploader.name] = dt_util.utcnow()

        previous = self.data or {}
        results: dict[str, bool] = dict(previous.get("results", {}))
        errors: dict[str, str | None] = dict(previous.get("errors", {}))
        payloads: dict[str, dict[str, Any]] = dict(previous.get("payloads", {}))
        counts: dict[str, int] = dict(previous.get("counts", {}))
        error_codes: dict[str, str | None] = dict(previous.get("error_codes", {}))
        error_times: dict[str, Any] = dict(previous.get("error_times", {}))
        success_times: dict[str, Any] = dict(previous.get("success_times", {}))

        for uploader, outcome in zip(due, outcomes, strict=True):
            # A failed attempt still consumed the provider's budget, so
            # throttle on attempt rather than on success.
            uploader.mark_sent()
            # An unexpected exception (one send() did not handle itself)
            # must be recorded on the uploader before its error fields are
            # read below, so the code, message, and time stay consistent
            # with each other and the message goes through the uploader's
            # credential redaction -- rather than the raw str landing in
            # recorded attributes while the code/time stay stale.
            if isinstance(outcome, Exception):
                _LOGGER.error("%s raised unexpectedly: %s", uploader.name, outcome)
                uploader.record_error("exception", str(outcome))
            # Record what this network actually sent -- the payload
            # captured during send(), not a rebuild (which would recompute
            # timestamps and differ from what went on the wire). Already
            # credential-redacted by the uploader.
            payloads[uploader.name] = uploader.last_payload
            # And how many weather measurements that represents, counted
            # consistently across networks (see measurement_count).
            counts[uploader.name] = uploader.measurement_count(data)
            error_codes[uploader.name] = uploader.last_error_code
            error_times[uploader.name] = uploader.last_error_time
            if isinstance(outcome, Exception):
                results[uploader.name] = False
                errors[uploader.name] = uploader.last_error
            else:
                results[uploader.name] = outcome
                errors[uploader.name] = uploader.last_error
                if outcome:
                    # Record when this network last actually accepted
                    # data, distinct from the last-error time. Left in
                    # place across later failures, so "when did this last
                    # work" survives a run of errors.
                    success_times[uploader.name] = attempt_times[uploader.name]

        return {
            "data": data,
            "results": results,
            "errors": errors,
            "payloads": payloads,
            "counts": counts,
            "error_codes": error_codes,
            "error_times": error_times,
            "success_times": success_times,
        }

    @callback
    def reseed_throttles_from_restored_state(self) -> None:
        """Re-seed each network's throttle from its restored last attempt.

        Runs as the status sensors restore their last-success and
        last-error times into ``self.data``. The last *attempt* for a
        network is the later of those two (an attempt ends as one or the
        other), and throttling gates on attempts -- a failed attempt
        still spent the provider's rate budget. So seeding from the last
        attempt skips the wasteful full-interval wait after a restart
        while still honouring a rate limit that a recent failed attempt
        implies. If neither time was restored, the conservative
        construction-time seed stands.

        The two sensors per network restore separately, so this may run
        more than once; seeding is deterministic (it recomputes the same
        max each time), so re-running is harmless and simply incorporates
        whichever time arrived later. The follow-up refresh is debounced
        and re-armed on each call, so it fires once, after every
        network's success/error pair has restored -- never on the first,
        partial restore, which would seed from an incomplete last-attempt
        time and could upload on stale throttle state.
        """
        data = self.data or {}
        success_times = data.get("success_times", {})
        error_times = data.get("error_times", {})
        now = dt_util.utcnow()
        reseeded_any = False

        for uploader in self.uploaders:
            times = [
                t
                for t in (
                    success_times.get(uploader.name),
                    error_times.get(uploader.name),
                )
                if t is not None
            ]
            if not times:
                continue
            last_attempt = max(times)
            seconds_since = (now - last_attempt).total_seconds()
            uploader.seed_from_last_attempt(seconds_since)
            reseeded_any = True

        if reseeded_any:
            # Re-arm: cancel any pending refresh and schedule a new one a
            # moment out. Each sensor restore pushes it back, so it lands
            # only after the last one has updated the throttle state.
            if self._reseed_refresh_unsub is not None:
                self._reseed_refresh_unsub()
            self._reseed_refresh_unsub = async_call_later(
                self.hass,
                RESEED_REFRESH_DELAY,
                self._fire_reseed_refresh,
            )

    @callback
    def _fire_reseed_refresh(self, _now: Any) -> None:
        """Dispatch the debounced post-reseed refresh once started.

        Config-entry setup (and entity restore) runs during boot while
        the core state is still ``not_running`` -- ``starting`` is only
        set later, inside ``async_start``. So a plain state check here
        would not defer on a reboot, and the refresh would fire into the
        window where source sensors are still initialising. ``async_at_
        started`` handles both cases correctly: it runs the callback
        immediately if Home Assistant has already started, and otherwise
        waits for the started event. The unsub is stored so a reload
        before startup cancels the pending callback.
        """
        self._reseed_refresh_unsub = None
        # Cancel any earlier registration first: if a prior debounce
        # already fired during boot and registered an at_started callback,
        # a later sensor restoring (>debounce after it) would otherwise
        # overwrite the unsub and leak that listener, leaving two to fire
        # at STARTED. The refresh debouncer would collapse the duplicate
        # refresh, but the stray callback should not exist at all.
        if self._reseed_started_unsub is not None:
            self._reseed_started_unsub()
        self._reseed_started_unsub = async_at_started(
            self.hass, self._reseed_refresh_when_started
        )

    @callback
    def _reseed_refresh_when_started(self, _hass: HomeAssistant) -> None:
        """Run the reseed refresh now that Home Assistant has started."""
        self._reseed_started_unsub = None
        self.hass.async_create_task(self.async_request_refresh())

    async def async_shutdown(self) -> None:
        """Cancel any pending reseed refresh, then shut down normally."""
        if self._reseed_refresh_unsub is not None:
            self._reseed_refresh_unsub()
            self._reseed_refresh_unsub = None
        if self._reseed_started_unsub is not None:
            self._reseed_started_unsub()
            self._reseed_started_unsub = None
        await super().async_shutdown()

    def _due_tolerance(self) -> float:
        """Seconds of shortfall to forgive when checking if a network is due.

        Sends are dispatched a moment after each poll tick -- staggered by
        ``UPLOAD_STAGGER_SECONDS`` per network, plus network latency -- so
        ``last_sent`` lands slightly after the tick. A network whose
        ``min_interval`` equals the poll interval would then read as
        fractionally not-due on every tick and fire only every other one
        (the reported 2-minute cadence on a 1-minute poll). The tolerance
        forgives exactly that dispatch offset.

        It is sized to the worst-case dispatch window -- the last network's
        stagger slot plus a small latency margin -- so every network fires
        on schedule regardless of its slot. It is then capped at half the
        poll interval, so even a poll interval shorter than the dispatch
        window can never let the tolerance approach a real rate limit: a
        network still cannot send twice in one cycle, so the true
        send-to-send gap stays at least the poll interval.
        """
        throttled = sum(1 for u in self.uploaders if u.min_interval > 0)
        if throttled <= 0:
            return 0.0
        window = (throttled - 1) * UPLOAD_STAGGER_SECONDS + DUE_TOLERANCE_MARGIN
        interval = getattr(self, "update_interval", None)
        poll = interval.total_seconds() if interval else 0.0
        if poll > 0:
            return min(window, poll / 2)
        return window

    def _carry_forward(self, data: dict[str, float]) -> dict[str, Any]:
        """Return prior statuses unchanged, with new sensor data."""
        previous = self.data or {}
        return {
            "data": data,
            "results": dict(previous.get("results", {})),
            "errors": dict(previous.get("errors", {})),
            "payloads": dict(previous.get("payloads", {})),
            "counts": dict(previous.get("counts", {})),
            "error_codes": dict(previous.get("error_codes", {})),
            "error_times": dict(previous.get("error_times", {})),
            "success_times": dict(previous.get("success_times", {})),
        }

    @property
    def in_startup_grace(self) -> bool:
        """True while still waiting for mapped sensors to first report.

        After a restart a source integration may become available a little
        after ``homeassistant_started``. During that gap absent data is
        expected, not a problem. The grace ends as soon as every mapped
        sensor has produced one usable value, or when
        ``STARTUP_GRACE_TIMEOUT`` passes -- whichever comes first -- so a
        genuinely dead sensor is still flagged, just not instantly on
        boot. With nothing mapped there is nothing to wait for.
        """
        if not self._map:
            return False
        mapped_entity_ids = set(self._map.values())
        if mapped_entity_ids <= self._reported_once:
            return False  # every mapped sensor has reported at least once
        now = time.monotonic()
        if self._startup_grace_deadline is None:
            self._startup_grace_deadline = now + STARTUP_GRACE_TIMEOUT
        return now < self._startup_grace_deadline

    @property
    def data_is_fresh(self) -> bool:
        """Return True when at least one mapped sensor is current.

        Distinct from upload success: a network can accept our data
        happily while that data is a dead station's last reading.
        """
        if not self._map:
            return False
        return bool(self.data and self.data.get("data"))
