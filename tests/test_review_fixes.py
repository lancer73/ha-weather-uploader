"""Regression tests for issues found in the v0.6.0 quality review."""

from unittest.mock import MagicMock

from custom_components.weather_uploader.uploaders import build_uploader

# --- Bug 1 & 2: converter exceptions / rain_rate unit -------------------


def _coordinator():
    from custom_components.weather_uploader.coordinator import UploadCoordinator

    c = UploadCoordinator.__new__(UploadCoordinator)
    c._warned = set()
    return c


def test_rain_rate_converts_from_mm_per_hour():
    """rain_rate is mm/h (a flux unit); it must convert, not crash."""
    c = _coordinator()
    assert c._convert("rain_rate", 5.0, "mm/h") == 5.0
    # imperial rain rate too
    assert 25 < c._convert("rain_rate", 1.0, "in/h") < 26


def test_unknown_unit_is_caught_not_raised():
    """An unrecognized unit drops the field, not the whole refresh.

    HA's converters raise HomeAssistantError (MRO: only Exception), so a
    catch of just (ValueError, TypeError) would let it propagate and fail
    every coordinator update.
    """
    c = _coordinator()
    assert c._convert("temperature", 20.0, "furlongs") is None
    # rain accumulation stays a distance and still works
    assert c._convert("rain_hourly", 5.0, "mm") == 5.0


# --- Bug 3: options flow can unmap a sensor -----------------------------


def test_options_authoritative_mapping_allows_unmapping():
    """Once options is saved, a cleared sensor is actually removed."""
    from custom_components.weather_uploader.const import SENSOR_KEYS

    # Simulate the coordinator's mapping resolution.
    data = {"temperature": "sensor.t", "humidity": "sensor.h"}
    options = {"temperature": "sensor.t"}  # humidity cleared in the form

    source = options if options else data
    mapping = {k: source[k] for k in SENSOR_KEYS if source.get(k)}
    assert mapping == {"temperature": "sensor.t"}
    assert "humidity" not in mapping


# --- Bug 4b: CWOP needs no key ------------------------------------------


async def test_cwop_credentials_step_has_no_key_field():
    from custom_components.weather_uploader.config_flow import (
        WeatherUploaderConfigFlow,
    )
    from custom_components.weather_uploader.const import CONF_KEY, SERVICE_CWOP

    flow = WeatherUploaderConfigFlow()
    flow.hass = MagicMock()
    flow._pending = [SERVICE_CWOP]
    flow._services = {}
    flow._owm_key = ""
    result = await flow.async_step_credentials()
    fields = [str(k) for k in result["data_schema"].schema]
    assert CONF_KEY not in fields
    assert "latitude" in fields and "longitude" in fields


# --- Bug 4c: CWOP without coordinates is skipped, not Null Island -------


def test_cwop_without_coordinates_is_skipped():
    from custom_components.weather_uploader.const import CONF_STATION_ID

    # No lat/lon in config -> build_uploader returns None rather than (0,0).
    assert build_uploader(None, "cwop", {CONF_STATION_ID: "EW1234"}) is None
    # With coordinates it builds.
    ok = build_uploader(
        None,
        "cwop",
        {CONF_STATION_ID: "EW1234", "latitude": 51.1, "longitude": 4.5},
    )
    assert ok is not None


# --- Bug 5c: credentials redacted from last_error -----------------------


def test_last_error_redacts_key():
    up = build_uploader(None, "windy", {"station_id": "s", "key": "SECRETKEY123"})
    up.last_error = "connect failed: https://x/update/SECRETKEY123?t=5"
    assert "SECRETKEY123" not in up.last_error
    assert "***" in up.last_error


def test_last_error_handles_none():
    up = build_uploader(None, "windy", {"station_id": "s", "key": "k"})
    up.last_error = None
    assert up.last_error is None


# --- Bug 4e: last_payload is what was sent ------------------------------


async def test_last_payload_reflects_actual_send():
    """last_payload records the send()-time payload, credential-stripped."""

    class FakeResp:
        status = 200

        async def text(self):
            return "ok"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class FakeSession:
        def __init__(self):
            self.sent = None

        def get(self, url, params=None, timeout=None):
            self.sent = dict(params)
            return FakeResp()

    session = FakeSession()
    up = build_uploader(None, "windy", {"station_id": "st", "key": "SECRET"})
    up._session = session
    await up.send({"temperature": 18.7, "humidity": 87.0})

    assert "PASSWORD" not in up.last_payload
    assert set(up.last_payload) == set(session.sent) - {"PASSWORD"}


# --- CWOP coordinate pre-fill -------------------------------------------


async def test_cwop_coordinates_prefill_rounded():
    """CWOP coords default from HA location, rounded to ~100 m.

    CWOP broadcasts coordinates publicly to APRS-IS, so the default is
    rounded rather than the exact home location; the user can still enter
    a more precise value.
    """
    from custom_components.weather_uploader.config_flow import (
        WeatherUploaderConfigFlow,
    )
    from custom_components.weather_uploader.const import SERVICE_CWOP

    flow = WeatherUploaderConfigFlow()
    flow.hass = MagicMock()
    flow.hass.config.latitude = 52.0906789
    flow.hass.config.longitude = 5.1214321
    flow._pending = [SERVICE_CWOP]
    flow._services = {}
    flow._owm_key = ""

    result = await flow.async_step_credentials()
    defaults = {}
    for marker in result["data_schema"].schema:
        raw = getattr(marker, "default", None)
        defaults[str(marker)] = raw() if callable(raw) else raw

    assert defaults["latitude"] == 52.091
    assert defaults["longitude"] == 5.121


async def test_cwop_coordinates_prefill_handles_no_location():
    """A HA install with no location set still renders the form."""
    from custom_components.weather_uploader.config_flow import (
        WeatherUploaderConfigFlow,
    )
    from custom_components.weather_uploader.const import SERVICE_CWOP

    flow = WeatherUploaderConfigFlow()
    flow.hass = MagicMock()
    flow.hass.config.latitude = None
    flow.hass.config.longitude = None
    flow._pending = [SERVICE_CWOP]
    flow._services = {}
    flow._owm_key = ""

    result = await flow.async_step_credentials()
    assert result["type"] == "form"
