"""Diagnostics must expose useful status without leaking secrets."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock

from custom_components.weather_uploader import diagnostics


def _entry(coordinator, data, options=None):
    entry = MagicMock()
    entry.runtime_data = coordinator
    entry.data = data
    entry.options = options or {}
    return entry


def _coordinator(uploaders, data):
    c = MagicMock()
    c.uploaders = uploaders
    c.data = data
    c.update_interval = MagicMock()
    c.update_interval.total_seconds = lambda: 60
    c.max_sensor_age = 3600
    c._map = {"temperature": "sensor.t"}
    c.last_update_success = True
    return c


def _run(entry):
    return asyncio.run(
        diagnostics.async_get_config_entry_diagnostics(MagicMock(), entry)
    )


def test_diagnostics_redacts_credentials():
    coord = _coordinator([], {})
    entry = _entry(
        coord,
        {"services": {"wow_be": {"station_id": "1", "key": "TOPSECRET"}}},
    )
    import json

    assert "TOPSECRET" not in json.dumps(_run(entry))


def test_diagnostics_redacts_coordinates_including_nested_cwop():
    coord = _coordinator([], {})
    entry = _entry(
        coord,
        {
            "latitude": 52.0906,
            "longitude": 5.1214,
            "services": {"cwop": {"station_id": "EW1", "latitude": 52.0906}},
        },
    )
    import json

    blob = json.dumps(_run(entry))
    assert "52.0906" not in blob
    assert "5.1214" not in blob


def test_diagnostics_includes_network_status():
    ts = datetime(2026, 7, 31, 10, 0, tzinfo=UTC)
    uploader = type("U", (), {"name": "WOW-BE", "min_interval": 60})()
    coord = _coordinator(
        [uploader],
        {
            "results": {"WOW-BE": True},
            "error_codes": {"WOW-BE": None},
            "error_times": {"WOW-BE": None},
            "success_times": {"WOW-BE": ts},
            "counts": {"WOW-BE": 8},
        },
    )
    diag = _run(_entry(coord, {"services": {}}))
    net = diag["networks"]["WOW-BE"]
    assert net["result"] is True
    assert net["last_success"] == "2026-07-31T10:00:00+00:00"
    assert net["measurements_sent"] == 8
    assert diag["coordinator"]["mapped_fields"] == ["temperature"]
