"""During Home Assistant startup, not-yet-ready source sensors must not
raise a false source-data problem, and the post-restart reseed refresh
must wait until startup finishes.

Source sensors from other integrations may still be initialising for a
short while after a restart. Flagging that as a problem, or refreshing
into it, produces a spurious alarm right after every reboot.
"""

from unittest.mock import MagicMock

from custom_components.weather_uploader.binary_sensor import SourceDataEntity
from custom_components.weather_uploader.coordinator import UploadCoordinator


def _problem_sensor(data_is_fresh, in_grace=False):
    coord = MagicMock()
    coord.entry.entry_id = "e1"
    coord.data = {"data": {}}
    coord.data_is_fresh = data_is_fresh
    coord.in_startup_grace = in_grace
    return SourceDataEntity(coord)


def test_problem_suppressed_during_grace():
    # During the startup grace, absent data is not yet a problem.
    sensor = _problem_sensor(data_is_fresh=False, in_grace=True)
    assert sensor.is_on is None


def test_problem_flagged_after_grace():
    sensor = _problem_sensor(data_is_fresh=False, in_grace=False)
    assert sensor.is_on is True


def test_no_problem_with_fresh_data():
    sensor = _problem_sensor(data_is_fresh=True, in_grace=False)
    assert sensor.is_on is False


def _grace_coordinator(mapped, reported):

    c = UploadCoordinator.__new__(UploadCoordinator)
    c._map = dict(mapped)
    c._reported_once = set(reported)
    c._startup_grace_deadline = None
    return c


def test_grace_active_until_all_sensors_report():
    c = _grace_coordinator(
        {"temperature": "sensor.t", "humidity": "sensor.h"}, {"sensor.t"}
    )
    # one of two mapped sensors has reported -> still in grace
    assert c.in_startup_grace is True


def test_grace_ends_when_all_sensors_reported():
    c = _grace_coordinator(
        {"temperature": "sensor.t", "humidity": "sensor.h"},
        {"sensor.t", "sensor.h"},
    )
    assert c.in_startup_grace is False


def test_grace_ends_after_timeout(monkeypatch):
    """A never-reporting sensor stops suppressing once the deadline passes."""
    import custom_components.weather_uploader.coordinator as mod

    c = _grace_coordinator({"temperature": "sensor.t"}, set())  # never reports
    base = 1000.0
    monkeypatch.setattr(mod.time, "monotonic", lambda: base)
    assert c.in_startup_grace is True  # deadline set now
    # jump past the timeout
    monkeypatch.setattr(
        mod.time, "monotonic", lambda: base + mod.STARTUP_GRACE_TIMEOUT + 1
    )
    assert c.in_startup_grace is False


def test_grace_inactive_with_nothing_mapped():
    c = _grace_coordinator({}, set())
    assert c.in_startup_grace is False


def _reseed_coordinator():
    c = UploadCoordinator.__new__(UploadCoordinator)
    c._reseed_refresh_unsub = None
    c._reseed_started_unsub = None
    c.hass = MagicMock()
    c.tasks = []
    c.hass.async_create_task = lambda coro: (c.tasks.append(1), coro.close())
    c.async_request_refresh = MagicMock()
    return c


def test_reseed_refresh_uses_async_at_started(monkeypatch):
    """The reseed refresh is handed to async_at_started, which handles the
    boot-ordering (fires now if started, else waits for STARTED).

    A plain CoreState check could not do this: config-entry setup runs
    while the core state is still not_running, so the refresh would fire
    into the sensor-initialisation window on every reboot.
    """
    import custom_components.weather_uploader.coordinator as mod

    captured = {}

    def fake_at_started(hass, cb):
        captured["cb"] = cb
        return MagicMock()  # unsub

    monkeypatch.setattr(mod, "async_at_started", fake_at_started)

    c = _reseed_coordinator()
    c._fire_reseed_refresh(None)
    # handed off to async_at_started, not fired directly
    assert "cb" in captured
    assert c.tasks == []
    # when the started callback runs, the refresh is dispatched
    captured["cb"](c.hass)
    assert len(c.tasks) == 1


def test_reseed_started_unsub_cancelled_on_shutdown(monkeypatch):
    """A pending started-callback is cancelled if the entry unloads first."""
    import custom_components.weather_uploader.coordinator as mod

    unsub = MagicMock()
    monkeypatch.setattr(mod, "async_at_started", lambda hass, cb: unsub)

    c = _reseed_coordinator()
    # async_shutdown needs the debounce unsub attr and the base method
    import asyncio
    from unittest.mock import AsyncMock

    c._fire_reseed_refresh(None)
    assert c._reseed_started_unsub is unsub

    # Patch the base async_shutdown to a no-op and confirm our cancel runs.
    monkeypatch.setattr(
        UploadCoordinator.__mro__[1], "async_shutdown", AsyncMock()
    )
    asyncio.run(c.async_shutdown())
    unsub.assert_called_once()
    assert c._reseed_started_unsub is None


def test_reseed_refresh_cancels_prior_at_started_registration(monkeypatch):
    """A second debounce during boot must not leak the first at_started
    listener.

    If a prior debounce already fired and registered an at_started
    callback, a later sensor restoring re-arms and fires the debounce
    again. That second _fire_reseed_refresh must cancel the earlier
    registration before making a new one, so only one listener is live.
    """
    import custom_components.weather_uploader.coordinator as mod

    unsubs = []

    def fake_at_started(hass, cb):
        u = MagicMock()
        unsubs.append(u)
        return u

    monkeypatch.setattr(mod, "async_at_started", fake_at_started)

    c = _reseed_coordinator()
    c._fire_reseed_refresh(None)  # first registration
    c._fire_reseed_refresh(None)  # second: must cancel the first

    assert len(unsubs) == 2
    unsubs[0].assert_called_once()  # first was cancelled
    unsubs[1].assert_not_called()  # second is the live one
    assert c._reseed_started_unsub is unsubs[1]
