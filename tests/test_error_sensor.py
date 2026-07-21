"""Tests for the per-network last-error sensor and error classification.

The sensor's state is a short, stable code so the recorder keeps a
usable history of intermittent failures (e.g. a DNS timeout). The full
message and the error time are attributes.
"""

import socket
from datetime import UTC, datetime
from unittest.mock import MagicMock

import aiohttp

from custom_components.weather_uploader.sensor import STATE_OK, UploadErrorSensor
from custom_components.weather_uploader.uploaders import build_uploader
from custom_components.weather_uploader.uploaders.base import BaseUploader


def test_classify_timeout():
    assert BaseUploader.classify_client_error(aiohttp.ServerTimeoutError()) == (
        "timeout"
    )


def test_classify_generic_client_error():
    assert BaseUploader.classify_client_error(aiohttp.ClientError()) == ("client_error")


def test_classify_dns_error():
    key = type("K", (), {"host": "x", "port": 443, "ssl": None})()
    err = aiohttp.ClientConnectorError(key, socket.gaierror(-2, "no name"))
    assert BaseUploader.classify_client_error(err) == "dns"


def test_record_error_sets_code_message_time():
    up = build_uploader(None, "windy", {"station_id": "s", "key": "SECRET"})
    up.record_error("dns", "host x: SECRET in url")
    assert up.last_error_code == "dns"
    assert "SECRET" not in up.last_error  # still redacted
    assert up.last_error_time is not None


def test_record_error_folds_http_status():
    up = build_uploader(None, "windy", {"station_id": "s", "key": "k"})
    up.record_error("http_error", "HTTP 503: busy", status=503)
    assert up.last_error_code == "http_503"


def test_clear_error_resets_code_keeps_time():
    up = build_uploader(None, "windy", {"station_id": "s", "key": "k"})
    up.record_error("timeout", "timeout")
    when = up.last_error_time
    up.clear_error()
    assert up.last_error_code is None
    assert up.last_error is None
    # The time of the last problem is kept, so history shows when it was.
    assert up.last_error_time == when


def _sensor(code, message, error_time):
    coordinator = MagicMock()
    coordinator.entry.entry_id = "abc"
    coordinator.data = {
        "error_codes": {"Windy": code},
        "errors": {"Windy": message},
        "error_times": {"Windy": error_time},
    }
    return UploadErrorSensor(coordinator, "Windy")


def test_sensor_state_is_the_code():
    when = datetime(2026, 7, 20, 14, 30, tzinfo=UTC)
    sensor = _sensor("dns", "Cannot connect: DNS timeout", when)
    assert sensor.native_value == "dns"
    attrs = sensor.extra_state_attributes
    assert attrs["last_error"] == "Cannot connect: DNS timeout"
    assert attrs["last_error_time"] == "2026-07-20T14:30:00+00:00"


def test_sensor_state_ok_when_no_error():
    sensor = _sensor(None, None, None)
    assert sensor.native_value == STATE_OK
    assert sensor.extra_state_attributes["last_error"] is None


# --- Connect vs read timeout classification, and one-shot retry --------


def test_classify_connect_timeout():
    """A connection-phase timeout (DNS + handshake) is its own code."""
    assert (
        BaseUploader.classify_client_error(aiohttp.ConnectionTimeoutError())
        == "connect_timeout"
    )


def test_classify_read_timeout():
    """A read-phase timeout is distinct from a connection one."""
    assert (
        BaseUploader.classify_client_error(aiohttp.SocketTimeoutError())
        == "read_timeout"
    )


def test_classify_generic_server_timeout():
    """A bare server timeout stays the generic code."""
    assert BaseUploader.classify_client_error(aiohttp.ServerTimeoutError()) == "timeout"


async def test_uploads_are_staggered(monkeypatch):
    """Networks upload sequentially, spaced by the stagger interval.

    Concurrent dispatch made every network resolve DNS at once, which
    could overwhelm a constrained resolver; they are now spaced out.
    """
    import custom_components.weather_uploader.coordinator as mod
    from custom_components.weather_uploader.coordinator import (
        UPLOAD_STAGGER_SECONDS,
        UploadCoordinator,
    )

    order: list[str] = []
    sleeps: list[float] = []

    class FakeUp:
        def __init__(self, name):
            self.name = name
            self.min_interval = 60
            self.last_payload = {}
            self.last_error = None
            self.last_error_code = None
            self.last_error_time = None

        def is_due(self):
            return True

        def mark_sent(self):
            pass

        def measurement_count(self, _data):
            return 1

        async def send(self, _data):
            order.append(self.name)
            return True

    coordinator = UploadCoordinator.__new__(UploadCoordinator)
    coordinator.uploaders = [FakeUp("A"), FakeUp("B"), FakeUp("C")]
    coordinator.data = None
    coordinator.read_sensors = lambda: {"temperature": 20.0}

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)
    result = await coordinator._async_update_data()

    assert order == ["A", "B", "C"]
    # N-1 gaps, no trailing sleep after the last network.
    assert sleeps == [UPLOAD_STAGGER_SECONDS, UPLOAD_STAGGER_SECONDS]
    assert result["results"] == {"A": True, "B": True, "C": True}


async def test_shortest_period_networks_upload_first(monkeypatch):
    """Networks with the tightest minimum interval upload first.

    The stagger shifts each successive network later in the cycle. A
    short-period network (60 s) has little slack, so if it were sent
    after a long-period one its next-cycle send could fall under its own
    floor. Sorting by ascending minimum interval keeps each short-period
    network at the same early slot every cycle, so its send-to-send gap
    stays at its full interval.
    """
    import custom_components.weather_uploader.coordinator as mod
    from custom_components.weather_uploader.coordinator import UploadCoordinator

    order: list[int] = []

    class FakeUp:
        def __init__(self, name, min_interval):
            self.name = name
            self.min_interval = min_interval
            self.last_payload = {}
            self.last_error = None
            self.last_error_code = None
            self.last_error_time = None

        def is_due(self):
            return True

        def mark_sent(self):
            pass

        def measurement_count(self, _data):
            return 1

        async def send(self, _data):
            order.append(self.min_interval)
            return True

    coordinator = UploadCoordinator.__new__(UploadCoordinator)
    # Long-period networks deliberately listed first in config order, to
    # prove the sort reorders rather than relying on insertion order.
    coordinator.uploaders = [
        FakeUp("CWOP", 300),
        FakeUp("Windy", 300),
        FakeUp("WOW-BE", 60),
        FakeUp("WU", 60),
    ]
    coordinator.data = None
    coordinator.read_sensors = lambda: {"temperature": 20.0}

    async def fake_sleep(_seconds):
        pass

    monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)
    await coordinator._async_update_data()

    assert order == sorted(order)  # ascending by min_interval
    assert order[0] == 60 and order[-1] == 300
