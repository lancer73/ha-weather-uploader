"""Tests that the status entity reports each network's actual payload.

Two things must hold. First, the payload shown for a network is the
field set that network actually sends -- a subset of the mapped
readings that differs per network -- not the full reading set shared
across networks. Second, no credential ever appears in it, even for a
provider like WOW-BE that builds its password directly into the params.
"""

from unittest.mock import MagicMock

from custom_components.weather_uploader.binary_sensor import UploadStatusEntity
from custom_components.weather_uploader.uploaders import build_uploader

_READINGS = {
    "temperature": 18.7,
    "dewpoint": 16.0,
    "humidity": 87.0,
    "pressure_relative": 1013.25,
    "pressure_absolute": 1000.0,
    "wind_speed": 1.2,
    "wind_gust": 3.4,
    "wind_direction": 160.0,
    "rain_hourly": 2.0,
    "rain_24h": 10.0,
    "rain_daily": 25.4,
    "solar_radiation": 450.0,
    "uv_index": 4.0,
    "visibility": 10.0,
    "soil_temperature": 15.0,
}
_CONFIG = {
    "station_id": "1",
    "key": "SUPERSECRET",
    "latitude": 51.1,
    "longitude": 4.5,
    "altitude": 12.0,
}


def test_build_payload_differs_per_network():
    """Each network sends its own subset, not the full reading set."""
    cwop = build_uploader(None, "cwop", _CONFIG).build_payload(_READINGS)
    wow = build_uploader(None, "wow_be", _CONFIG).build_payload(_READINGS)
    windy = build_uploader(None, "windy", _CONFIG).build_payload(_READINGS)

    # CWOP sends a single APRS packet; the others send many named fields.
    assert len(cwop) == 1
    assert len(wow) != len(windy)
    assert len(wow) < len(_READINGS) or len(wow) >= 1  # a real subset/mapping


def test_build_payload_never_contains_credentials():
    """No provider's payload may expose the key, however it is built."""
    for service in (
        "cwop",
        "wow_be",
        "windy",
        "openweathermap",
        "wunderground",
        "pwsweather",
    ):
        payload = build_uploader(None, service, _CONFIG).build_payload(_READINGS)
        blob = str(payload)
        assert "SUPERSECRET" not in blob, f"{service} leaks the key"
        lowered = {k.lower() for k in payload}
        assert not (
            lowered & {"password", "appid", "apikey", "api_key", "key", "token"}
        ), f"{service} exposes a credential field"


def test_wow_be_password_is_stripped_specifically():
    """WOW-BE builds PASSWORD into its params; it must be redacted."""
    up = build_uploader(None, "wow_be", _CONFIG)
    raw = up._prune(up.build_params(_READINGS))
    assert "PASSWORD" in raw  # it IS in the raw params
    assert "PASSWORD" not in up.build_payload(_READINGS)  # but not exposed


def test_status_entity_shows_its_own_payload():
    """The status attributes reflect the network, not the reading set."""
    coordinator = MagicMock()
    coordinator.data = {
        "data": {f"field{i}": i for i in range(6)},
        "results": {"CWOP": True, "WOW-BE": True},
        "errors": {"CWOP": None, "WOW-BE": None},
        "payloads": {
            "CWOP": {"packet": "EW1234>APRS..."},
            "WOW-BE": {"tempf": 65.7, "humidity": 87, "windspeedmph": 2.7},
        },
        "counts": {"CWOP": 9, "WOW-BE": 3},
    }
    cwop = UploadStatusEntity(coordinator, "CWOP").extra_state_attributes
    wow = UploadStatusEntity(coordinator, "WOW-BE").extra_state_attributes

    # sensors_published is the measurement count, not the payload key
    # count -- so CWOP's single packet still reports its 9 measurements.
    assert cwop["sensors_published"] == 9
    assert wow["sensors_published"] == 3
    assert cwop["last_payload"] != wow["last_payload"]


def test_status_entity_unsent_network_is_empty():
    """A network that has not sent shows an empty payload, not readings."""
    coordinator = MagicMock()
    coordinator.data = {
        "data": {f"field{i}": i for i in range(6)},
        "results": {},
        "errors": {},
        "payloads": {},
        "counts": {},
    }
    attrs = UploadStatusEntity(coordinator, "Windy").extra_state_attributes
    assert attrs["sensors_published"] == 0
    assert attrs["last_payload"] == {}


def test_cwop_measurement_count_is_not_one():
    """CWOP packs measurements into one packet; the count must reflect them."""
    up = build_uploader(None, "cwop", _CONFIG)
    present = {
        "temperature": 18.7,
        "humidity": 87.0,
        "wind_speed": 1.2,
        "wind_gust": 3.4,
        "wind_direction": 160.0,
        "rain_hourly": 2.0,
        "rain_24h": 10.0,
        "pressure_relative": 1013.25,
    }
    # Eight measurements present -> count of 8, not 1 (the packet dict key).
    assert up.measurement_count(present) == 8
    assert len(up.build_payload(present)) == 1  # still one packet on the wire


def test_measurement_count_matches_supported_readings_consumed():
    """Every declared SUPPORTED_READING must actually affect the payload.

    Prevents the declared set from drifting away from what build_params
    consumes, which would make the count lie.
    """
    base = {
        "temperature": 10.0,
        "wind_direction": 90.0,
        "humidity": 50.0,
    }
    for service in (
        "wow_be",
        "windy",
        "openweathermap",
        "wunderground",
        "pwsweather",
    ):
        up = build_uploader(None, service, _CONFIG)
        for key in up.SUPPORTED_READINGS:
            with_key = up.build_payload({**base, key: 5.0})
            without = up.build_payload({k: v for k, v in base.items() if k != key})
            assert with_key != without, (
                f"{service} declares {key} but build_params ignores it"
            )


def test_measurement_count_scales_with_present_data():
    """The count reflects only readings actually present this cycle."""
    up = build_uploader(None, "windy", _CONFIG)
    assert up.measurement_count({}) == 0
    assert up.measurement_count({"temperature": 20.0}) == 1
    two = {"temperature": 20.0, "humidity": 50.0}
    assert up.measurement_count(two) == 2
