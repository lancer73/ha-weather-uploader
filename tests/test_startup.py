"""During Home Assistant startup, not-yet-ready source sensors must not
raise a false source-data problem, and the post-restart reseed refresh
must wait until startup finishes.

Source sensors from other integrations may still be initialising for a
short while after a restart. Flagging that as a problem, or refreshing
into it, produces a spurious alarm right after every reboot.
"""

from unittest.mock import MagicMock

from homeassistant.core import CoreState

from custom_components.weather_uploader.binary_sensor import SourceDataEntity
from custom_components.weather_uploader.coordinator import UploadCoordinator


def _problem_sensor(hass_state, data_is_fresh, in_grace=False):
    coord = MagicMock()
    coord.entry.entry_id = "e1"
    coord.data = {"data": {}}
    coord.data_is_fresh = data_is_fresh
    coord.in_startup_grace = in_grace
    coord.hass.state = hass_state
    return SourceDataEntity(coord)


def test_problem_suppressed_during_startup():
    sensor = _problem_sensor(CoreState.starting, data_is_fresh=False)
    assert sensor.is_on is None  # not a problem yet, just not ready


def test_problem_flagged_when_running():
    sensor = _problem_sensor(CoreState.running, data_is_fresh=False)
    assert sensor.is_on is True


def test_no_problem_when_running_with_fresh_data():
    sensor = _problem_sensor(CoreState.running, data_is_fresh=True)
    assert sensor.is_on is False


def test_problem_suppressed_during_post_startup_grace():
    """Even when running, the flag is held while grace is active."""
    sensor = _problem_sensor(
        CoreState.running, data_is_fresh=False, in_grace=True
    )
    assert sensor.is_on is None


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


def _reseed_coordinator(hass_state):
    c = UploadCoordinator.__new__(UploadCoordinator)
    c._reseed_refresh_unsub = None
    c.hass = MagicMock()
    c.hass.state = hass_state
    c.tasks = []
    c.listeners = []
    c.hass.async_create_task = lambda coro: (c.tasks.append(1), coro.close())
    c.hass.bus.async_listen_once = lambda ev, cb: c.listeners.append((ev, cb))
    c.async_request_refresh = MagicMock()
    return c


def test_reseed_refresh_defers_during_startup():
    c = _reseed_coordinator(CoreState.starting)
    c._fire_reseed_refresh(None)
    # deferred: no refresh yet, a started-event listener registered
    assert c.tasks == []
    assert len(c.listeners) == 1
    assert c.listeners[0][0] == "homeassistant_started"
    # firing the started event runs the refresh
    c.listeners[0][1](MagicMock())
    assert len(c.tasks) == 1


def test_reseed_refresh_immediate_when_running():
    c = _reseed_coordinator(CoreState.running)
    c._fire_reseed_refresh(None)
    assert len(c.tasks) == 1
    assert c.listeners == []
