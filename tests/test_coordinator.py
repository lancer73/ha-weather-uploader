"""Tests for sensor reading, validation, and staleness rejection.

Two failures pull in opposite directions here, and the whole design of
the staleness check is about telling them apart:

1. A weather station dies. Home Assistant keeps its last value, so the
   reading stays a valid float forever. Publishing it as a current
   observation is fiction.
2. A rain sensor reads 0.0 for a week because it has not rained. The
   sensor is perfectly healthy. Dropping its reading is data loss.

Under ``last_updated`` these are indistinguishable -- both show an old
timestamp. Only ``last_reported`` separates them.
"""

from datetime import timedelta

import pytest
from homeassistant.util import dt as dt_util

from custom_components.weather_uploader.const import (
    CONF_MAX_SENSOR_AGE,
    DEFAULT_MAX_SENSOR_AGE,
)
from custom_components.weather_uploader.coordinator import _reported_at


class FakeState:
    """Stand-in for a State, with the two timestamps set independently."""

    def __init__(self, state, unit=None, updated_ago=0, reported_ago=0):
        self.state = state
        self.attributes = {"unit_of_measurement": unit} if unit else {}
        self.last_updated = dt_util.utcnow() - timedelta(seconds=updated_ago)
        self.last_reported = dt_util.utcnow() - timedelta(seconds=reported_ago)


class LegacyState:
    """A State from before Home Assistant 2024.4, without last_reported."""

    def __init__(self, state, updated_ago=0):
        self.state = state
        self.attributes = {}
        self.last_updated = dt_util.utcnow() - timedelta(seconds=updated_ago)


def _age(state):
    return (dt_util.utcnow() - _reported_at(state)).total_seconds()


def _is_stale(state, max_age=DEFAULT_MAX_SENSOR_AGE):
    return max_age > 0 and _age(state) > max_age


def test_reported_at_prefers_last_reported():
    """last_updated tracks value changes; last_reported tracks writes."""
    state = FakeState("0.0", updated_ago=172_800, reported_ago=30)
    assert _reported_at(state) == state.last_reported
    assert _reported_at(state) != state.last_updated


def test_reported_at_falls_back_on_legacy_state():
    """An older core has no last_reported; do not raise."""
    state = LegacyState("20.1", updated_ago=30)
    assert _reported_at(state) == state.last_updated


def test_dry_rain_sensor_is_not_stale():
    """The regression this function exists to prevent.

    A rain sensor reporting 0.0 every minute through a dry week has a
    last_updated of days ago and a last_reported of seconds ago. It is
    healthy, and its reading must still be published -- 0.0 mm is a
    real observation, not a missing one.
    """
    rain = FakeState("0.0", "mm", updated_ago=172_800, reported_ago=30)
    assert not _is_stale(rain)


@pytest.mark.parametrize(
    "key",
    ["solar_radiation", "uv_index", "wind_speed", "rain_hourly", "rain_daily"],
)
def test_fields_that_legitimately_sit_at_zero(key):
    """Solar and UV read 0.0 every night; wind does on a calm one.

    Judging these by last_updated would drop them from every payload
    overnight, every night.
    """
    state = FakeState("0.0", updated_ago=40_000, reported_ago=60)
    assert not _is_stale(state), f"{key} wrongly dropped while healthy"


def test_dead_station_is_stale():
    """The failure the check exists to catch is still caught."""
    dead = FakeState("0.0", "mm", updated_ago=172_800, reported_ago=172_800)
    assert _is_stale(dead)


def test_last_updated_cannot_distinguish_the_two_cases():
    """Why last_reported is required rather than merely nicer.

    A healthy dry rain sensor and a dead station have identical
    last_updated values. Any check built on it must be wrong about one
    of them.
    """
    healthy = FakeState("0.0", "mm", updated_ago=172_800, reported_ago=30)
    dead = FakeState("0.0", "mm", updated_ago=172_800, reported_ago=172_800)

    assert healthy.last_updated.replace(microsecond=0) == dead.last_updated.replace(
        microsecond=0
    )
    assert not _is_stale(healthy)
    assert _is_stale(dead)


@pytest.mark.parametrize(
    ("reported_ago", "stale"),
    [(0, False), (60, False), (3599, False), (7200, True), (86400, True)],
)
def test_staleness_boundary(reported_ago, stale):
    """Readings older than the limit are dropped, not published."""
    state = FakeState("20.1", "°C", updated_ago=reported_ago, reported_ago=reported_ago)
    assert _is_stale(state) == stale


def test_stale_value_is_still_a_valid_float():
    """A stale reading passes every other check; only age betrays it."""
    state = FakeState("20.1", "°C", updated_ago=50_000, reported_ago=50_000)
    assert state.state not in ("unknown", "unavailable")
    assert float(state.state) == 20.1
    assert _is_stale(state)


def test_zero_disables_the_check():
    """Opt-out is offered, so 0 must mean 'never stale'."""
    ancient = FakeState("20.1", "°C", updated_ago=999_999, reported_ago=999_999)
    assert not _is_stale(ancient, max_age=0)


def test_default_max_age_is_generous():
    """A slow-reporting station must not be mistaken for a dead one."""
    assert DEFAULT_MAX_SENSOR_AGE == 3600


def test_max_sensor_age_is_configurable():
    """The option key exists for the options flow to write."""
    assert CONF_MAX_SENSOR_AGE == "max_sensor_age"


# --- Runtime plausibility -----------------------------------------------

from custom_components.weather_uploader.const import PLAUSIBLE_RANGE  # noqa: E402
from custom_components.weather_uploader.coordinator import _is_plausible  # noqa: E402


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("humidity", 50.0, True),
        ("humidity", 150.0, False),  # impossible relative humidity
        ("humidity", -5.0, False),
        ("temperature", 20.0, True),
        ("temperature", 55.0, True),  # hot but real
        ("temperature", 200.0, False),  # e.g. a humidity sensor mis-mapped
        ("pressure_relative", 1013.0, True),
        ("pressure_relative", 101325.0, False),  # Pascals where hPa expected
        ("wind_direction", 180.0, True),
        ("wind_direction", 400.0, False),
    ],
)
def test_plausibility_bounds(key, value, expected):
    """Values outside a field's sane range are rejected."""
    assert _is_plausible(key, value) is expected


def test_plausibility_allows_unbounded_fields():
    """Cumulative totals have no upper bound and must not be clipped."""
    assert _is_plausible("rain_yearly", 999_999.0) is True


def test_only_cumulative_totals_are_unbounded():
    """Every field except cumulative rain totals has a range."""
    from custom_components.weather_uploader.const import SENSOR_KEYS

    missing = [k for k in SENSOR_KEYS if k not in PLAUSIBLE_RANGE]
    assert set(missing) <= {"rain_weekly", "rain_monthly", "rain_yearly"}


def test_pascals_leak_is_caught():
    """The headline mis-unit case: 101325 Pa into an hPa field."""
    assert not _is_plausible("pressure_relative", 101325.0)
    assert _is_plausible("pressure_relative", 1013.25)
