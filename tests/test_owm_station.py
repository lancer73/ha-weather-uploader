"""Tests for OpenWeatherMap station creation and lookup.

OpenWeatherMap is the only supported network whose station must be
created through the API before uploads can work: the measurement
station_id is an internal identifier that does not exist until then.
These tests cover creation, idempotent reuse, and error surfacing
against the documented response shapes.
"""

import pytest

from custom_components.weather_uploader.uploaders.owm_station import (
    StationError,
    create_station,
    find_station_id,
)


class _Resp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _Session:
    """aiohttp-shaped stub scripting the GET (list) and POST (create)."""

    def __init__(self, get=None, post=None):
        self._get = get
        self._post = post
        self.posted = False

    def get(self, url, **kwargs):
        return self._get

    def post(self, url, **kwargs):
        self.posted = True
        self._last_post = kwargs
        return self._post


async def test_create_returns_internal_id_not_external():
    """The stored ID must be OWM's internal ID, not our external label."""
    created = {"ID": "583436dd9643a9000196b8d6", "external_id": "SF_TEST001"}
    session = _Session(get=_Resp(200, []), post=_Resp(201, created))
    station_id = await create_station(
        session, "key", "SF_TEST001", "SF", 37.76, -122.43, 150
    )
    assert station_id == "583436dd9643a9000196b8d6"
    body = session._last_post["json"]
    assert body["external_id"] == "SF_TEST001"
    assert body["altitude"] == 150


async def test_existing_station_is_reused_not_duplicated():
    """A matching external_id reuses the station; no second POST."""
    existing = [
        {"id": "AAA111", "external_id": "HA_Garden"},
        {"id": "BBB222", "external_id": "other"},
    ]
    session = _Session(get=_Resp(200, existing))
    station_id = await create_station(
        session, "key", "HA_Garden", "Garden", 51.0, 4.0, 12
    )
    assert station_id == "AAA111"
    assert session.posted is False


async def test_bad_key_raises_invalid_auth():
    """A 401 from the list endpoint surfaces as invalid_auth."""
    session = _Session(get=_Resp(401, {"code": 401000, "message": "Invalid API key"}))
    with pytest.raises(StationError) as err:
        await create_station(session, "badkey", "X", "X", 0.0, 0.0, 0.0)
    assert err.value.reason == "invalid_auth"


async def test_create_failure_raises_cannot_create():
    """A non-201 from POST surfaces as cannot_create with the message."""
    session = _Session(
        get=_Resp(200, []),
        post=_Resp(400, {"code": 400000, "message": "Error in input data"}),
    )
    with pytest.raises(StationError) as err:
        await create_station(session, "key", "X", "X", 0.0, 0.0, 0.0)
    assert err.value.reason == "cannot_create"


async def test_find_returns_none_when_absent():
    """Lookup returns None when no station matches the external_id."""
    session = _Session(get=_Resp(200, [{"id": "z", "external_id": "nope"}]))
    assert await find_station_id(session, "key", "HA_Garden") is None


# --- Config-flow pre-fill from HA location -------------------------------


async def test_owm_create_prefills_from_ha_location():
    """The station form defaults to Home Assistant's own location.

    HA already knows the home coordinates, elevation, and location name;
    the user should not have to retype them. They remain editable so a
    rounded or different location can be published.
    """
    from unittest.mock import MagicMock

    from custom_components.weather_uploader.config_flow import (
        WeatherUploaderConfigFlow,
    )
    from custom_components.weather_uploader.const import SERVICE_OPENWEATHERMAP

    flow = WeatherUploaderConfigFlow()
    flow.hass = MagicMock()
    flow.hass.config.latitude = 52.0907
    flow.hass.config.longitude = 5.1214
    flow.hass.config.elevation = 12
    flow.hass.config.location_name = "Utrecht Home"
    flow._pending = [SERVICE_OPENWEATHERMAP]
    flow._services = {}
    flow._owm_key = "key"

    result = await flow.async_step_owm_create()
    defaults = {}
    for marker in result["data_schema"].schema:
        raw = getattr(marker, "default", None)
        defaults[str(marker)] = raw() if callable(raw) else raw

    assert defaults["latitude"] == 52.0907
    assert defaults["longitude"] == 5.1214
    assert defaults["altitude"] == 12.0
    assert defaults["name"] == "Utrecht Home"


async def test_owm_create_prefill_fallbacks():
    """Empty location name and unset elevation fall back cleanly."""
    from unittest.mock import MagicMock

    from custom_components.weather_uploader.config_flow import (
        WeatherUploaderConfigFlow,
    )
    from custom_components.weather_uploader.const import SERVICE_OPENWEATHERMAP

    flow = WeatherUploaderConfigFlow()
    flow.hass = MagicMock()
    flow.hass.config.latitude = 0.0
    flow.hass.config.longitude = 0.0
    flow.hass.config.elevation = None
    flow.hass.config.location_name = ""
    flow._pending = [SERVICE_OPENWEATHERMAP]
    flow._services = {}
    flow._owm_key = "key"

    result = await flow.async_step_owm_create()
    defaults = {}
    for marker in result["data_schema"].schema:
        raw = getattr(marker, "default", None)
        defaults[str(marker)] = raw() if callable(raw) else raw

    assert defaults["name"] == "Home Assistant"
    assert defaults["altitude"] == 0.0
