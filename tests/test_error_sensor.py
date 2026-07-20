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
