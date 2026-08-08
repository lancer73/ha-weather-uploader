"""Restored state must survive a restart until a fresh cycle overwrites it.

Coordinator status is in-memory only, so without restore every
per-network sensor would read unknown after a Home Assistant restart
until the next successful cycle. Each sensor restores its last state and
seeds it back into the coordinator; a fresh cycle must always win.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from custom_components.weather_uploader.sensor import (
    STATE_OK,
    LastSuccessSensor,
    UploadErrorSensor,
)


def _sensor(cls, name, coordinator_data):
    coordinator = MagicMock()
    coordinator.entry.entry_id = "e1"
    coordinator.data = coordinator_data
    return cls(coordinator, name)


async def _restore_from(sensor, state):
    """Drive the restore path without the framework base's side effects."""
    sensor.async_get_last_state = AsyncMock(return_value=state)
    last = await sensor.async_get_last_state()
    if last is not None and last.state not in (None, "unknown", "unavailable"):
        sensor._restore(last)


async def test_last_success_restores_after_reboot():
    sensor = _sensor(LastSuccessSensor, "WOW-BE", {})  # empty = post-reboot
    state = MagicMock()
    state.state = "2026-07-31T10:00:00+00:00"
    state.attributes = {}
    await _restore_from(sensor, state)
    assert sensor.native_value == datetime(2026, 7, 31, 10, 0, tzinfo=UTC)


async def test_fresh_success_cycle_wins_over_restore():
    fresh = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
    sensor = _sensor(LastSuccessSensor, "WOW-BE", {"success_times": {"WOW-BE": fresh}})
    state = MagicMock()
    state.state = "2026-07-31T10:00:00+00:00"  # older, from before restart
    state.attributes = {}
    await _restore_from(sensor, state)
    assert sensor.native_value == fresh


async def test_error_sensor_restores_code_and_time():
    sensor = _sensor(UploadErrorSensor, "WOW-BE", {})
    state = MagicMock()
    state.state = "dns"
    state.attributes = {
        "last_error": "Cannot connect: DNS timeout",
        "last_error_time": "2026-07-31T09:00:00+00:00",
    }
    await _restore_from(sensor, state)
    assert sensor.native_value == "dns"
    attrs = sensor.extra_state_attributes
    assert attrs["last_error"] == "Cannot connect: DNS timeout"
    assert attrs["last_error_time"] == "2026-07-31T09:00:00+00:00"


async def test_error_sensor_restores_ok_as_no_error():
    sensor = _sensor(UploadErrorSensor, "WOW-BE", {})
    state = MagicMock()
    state.state = STATE_OK
    state.attributes = {"last_error": None, "last_error_time": None}
    await _restore_from(sensor, state)
    assert sensor.native_value == STATE_OK


async def test_fresh_error_cycle_wins_over_restore():
    sensor = _sensor(
        UploadErrorSensor, "WOW-BE", {"error_codes": {"WOW-BE": "http_500"}}
    )
    state = MagicMock()
    state.state = "dns"  # older restored code
    state.attributes = {"last_error": "x", "last_error_time": None}
    await _restore_from(sensor, state)
    assert sensor.native_value == "http_500"  # fresh cycle wins


async def test_restore_of_the_two_sensors_does_not_clobber():
    """The error and success sensors seed different keys; both survive."""
    coordinator = MagicMock()
    coordinator.entry.entry_id = "e1"
    coordinator.data = {}
    err = UploadErrorSensor(coordinator, "WOW-BE")
    suc = LastSuccessSensor(coordinator, "WOW-BE")

    err_state = MagicMock()
    err_state.state = "dns"
    err_state.attributes = {
        "last_error": "DNS timeout",
        "last_error_time": "2026-07-31T09:00:00+00:00",
    }
    suc_state = MagicMock()
    suc_state.state = "2026-07-31T10:00:00+00:00"
    suc_state.attributes = {}

    await _restore_from(err, err_state)
    await _restore_from(suc, suc_state)

    assert err.native_value == "dns"
    assert suc.native_value == datetime(2026, 7, 31, 10, 0, tzinfo=UTC)


# --- Restart throttle re-seeding ----------------------------------------


def test_seed_from_last_attempt_waits_remaining_interval():
    """A recent attempt still waits out the remainder of its interval."""
    from custom_components.weather_uploader.uploaders import build_uploader

    up = build_uploader(None, "windy", {"station_id": "s", "key": "k"})
    up.min_interval = 300
    up.seed_from_last_attempt(250)  # 250s ago, 50s left
    assert up.is_due() is False
    up.seed_from_last_attempt(350)  # older than the interval
    assert up.is_due() is True


def test_seed_from_last_attempt_clamps_bad_clock():
    """A clock jump during downtime must not break the seed."""
    from custom_components.weather_uploader.uploaders import build_uploader

    up = build_uploader(None, "windy", {"station_id": "s", "key": "k"})
    up.min_interval = 300
    up.seed_from_last_attempt(-100)  # backwards clock -> clamp to 0
    assert up.is_due() is False
    up.seed_from_last_attempt(10**9)  # forwards clock -> clamp to interval
    assert up.is_due() is True


def test_seed_from_last_attempt_noop_when_unthrottled():
    from custom_components.weather_uploader.uploaders import build_uploader

    up = build_uploader(None, "windy", {"station_id": "s", "key": "k"})
    up.min_interval = 0
    up.seed_from_last_attempt(10)
    assert up.is_due() is True


def _reseed_coordinator(uploaders, success_times, error_times):
    from custom_components.weather_uploader.coordinator import UploadCoordinator

    c = UploadCoordinator.__new__(UploadCoordinator)
    c.uploaders = uploaders
    c.data = {"success_times": success_times, "error_times": error_times}
    c._reseed_refresh_unsub = None
    c._reseed_started_unsub = None
    c.hass = MagicMock()
    return c


class _SeedUp:
    def __init__(self, name):
        self.name = name
        self.min_interval = 300
        self.seeded: float | None = None

    def seed_from_last_attempt(self, seconds):
        self.seeded = seconds


def test_reseed_uses_most_recent_attempt():
    """The throttle is placed from the later of success/error (the attempt)."""
    from datetime import datetime, timedelta

    now = datetime.now(UTC)
    up = _SeedUp("WOW-BE")
    coord = _reseed_coordinator(
        [up],
        {"WOW-BE": now - timedelta(seconds=200)},
        {"WOW-BE": now - timedelta(seconds=400)},
    )
    coord.reseed_throttles_from_restored_state()
    # most recent attempt is the success at 200s, not the error at 400s
    assert 199 <= up.seeded <= 201


def test_reseed_recent_failure_keeps_throttle():
    """A recent failed attempt (e.g. 429) keeps the network throttled.

    Seeding from last success alone would upload immediately and re-hit
    the rate limit; using the later error time prevents that.
    """
    from datetime import datetime, timedelta

    now = datetime.now(UTC)
    up = _SeedUp("Windy")
    coord = _reseed_coordinator(
        [up],
        {"Windy": now - timedelta(seconds=300)},  # last success 5 min ago
        {"Windy": now - timedelta(seconds=5)},  # 429 just before restart
    )
    coord.reseed_throttles_from_restored_state()
    assert 4 <= up.seeded <= 6  # throttled from the recent failure


def test_reseed_debounces_refresh_until_last_restore(monkeypatch):
    """The refresh is re-armed on each restore, firing once after the last.

    The first (partial) restore must not fire an immediate refresh -- it
    has incomplete last-attempt times. Each subsequent reseed cancels the
    pending one and re-arms, so the refresh lands only after the final
    restore has updated the throttle state.
    """
    import custom_components.weather_uploader.coordinator as mod

    scheduled = []
    cancels = []

    def fake_call_later(hass, delay, cb):
        token = object()

        def unsub():
            cancels.append(token)

        scheduled.append((token, cb))
        return unsub

    monkeypatch.setattr(mod, "async_call_later", fake_call_later)

    now = datetime.now(UTC)
    up = _SeedUp("WOW-BE")
    coord = _reseed_coordinator([up], {"WOW-BE": now - timedelta(seconds=100)}, {})

    # First restore arms the refresh.
    coord.reseed_throttles_from_restored_state()
    assert len(scheduled) == 1
    assert cancels == []

    # Second restore cancels the first and re-arms.
    coord.reseed_throttles_from_restored_state()
    assert len(scheduled) == 2
    assert len(cancels) == 1  # the first was cancelled

    # Only the last scheduled callback runs; it hands the refresh to
    # async_at_started (which fires now if started, else on STARTED).
    at_started = []
    monkeypatch.setattr(
        mod, "async_at_started", lambda hass, cb: at_started.append(cb) or MagicMock()
    )
    created = []
    coord.hass.async_create_task = lambda coro: (created.append(1), coro.close())
    coord.async_request_refresh = MagicMock()
    scheduled[-1][1](None)  # fire the debounce timer callback
    assert len(at_started) == 1  # handed off, not fired directly
    at_started[0](coord.hass)  # simulate "started"
    assert len(created) == 1


def test_restored_error_state_survives_first_success_cycle():
    """A restored last-error time must not be wiped by the first cycle.

    The uploader is rebuilt empty on restart; restoring only the
    coordinator dict would let the first cycle write last_error_time=None
    over the restored value. Seeding the uploader keeps it.
    """
    from custom_components.weather_uploader.uploaders import build_uploader

    up = build_uploader(None, "windy", {"station_id": "s", "key": "k"})
    when = datetime(2026, 7, 31, 2, 4, 41, tzinfo=UTC)
    up.restore_error_state(
        code="dns", message="Cannot connect: DNS timeout", error_time=when
    )
    assert up.last_error_code == "dns"
    assert up.last_error_time == when

    # A subsequent success clears code/message but keeps the time, and a
    # fresh cycle reading uploader.last_error_time now sees the restored
    # value rather than None.
    up.clear_error()
    assert up.last_error_time == when


def test_restore_error_state_does_not_clobber_fresh_error():
    """If a real error was already recorded, restore must not overwrite it."""
    from custom_components.weather_uploader.uploaders import build_uploader

    up = build_uploader(None, "windy", {"station_id": "s", "key": "k"})
    up.record_error("http_500", "Server error", status=500)
    fresh_time = up.last_error_time
    up.restore_error_state(code="dns", message="old", error_time=None)
    assert up.last_error_code == "http_500"
    assert up.last_error_time == fresh_time
