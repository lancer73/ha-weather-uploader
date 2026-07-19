"""Tests for uploader parameter mapping.

build_params is a pure function, so no HTTP mocking is needed here.
These tests never touch the live WOW-BE endpoint: it is rate limited
per IP and CI would trip it.
"""

import re
from datetime import UTC, datetime

import pytest

from custom_components.weather_uploader.const import (
    MIN_SERVICE_INTERVAL,
    SERVICE_CWOP,
    SERVICE_WINDY,
    SERVICE_WOW_BE,
)
from custom_components.weather_uploader.uploaders import build_uploader
from custom_components.weather_uploader.uploaders.base import (
    c_to_f,
    hpa_to_inhg,
    km_to_mi,
    mm_to_in,
    ms_to_mph,
)
from custom_components.weather_uploader.uploaders.cwop import (
    CwopUploader,
    build_packet,
    format_latitude,
    format_longitude,
)
from custom_components.weather_uploader.uploaders.openweathermap import (
    OpenWeatherMapUploader,
)
from custom_components.weather_uploader.uploaders.pwsweather import PWSWeatherUploader
from custom_components.weather_uploader.uploaders.windy import WindyUploader
from custom_components.weather_uploader.uploaders.wowbe import WowBeUploader
from custom_components.weather_uploader.uploaders.wunderground import (
    WundergroundUploader,
)


def test_unit_helpers():
    """Conversion helpers match known reference values."""
    assert c_to_f(0) == pytest.approx(32.0)
    assert c_to_f(100) == pytest.approx(212.0)
    assert ms_to_mph(1) == pytest.approx(2.236936)
    assert mm_to_in(25.4) == pytest.approx(1.0)
    assert hpa_to_inhg(1013.25) == pytest.approx(29.921, abs=1e-3)
    assert km_to_mi(1.609344) == pytest.approx(1.0)


def test_wunderground_mapping(sample_data):
    """WU receives imperial values and its credentials."""
    up = WundergroundUploader(None, "KSTATION1", "secret")
    p = up.build_params(sample_data)
    assert p["ID"] == "KSTATION1"
    assert p["PASSWORD"] == "secret"
    assert p["tempf"] == pytest.approx(68.0)
    assert p["baromin"] == pytest.approx(29.921, abs=1e-2)
    assert p["rainin"] == pytest.approx(0.1, abs=1e-3)
    assert p["action"] == "updateraw"


def test_wow_be_uses_weatherunderground_endpoint():
    """WOW-BE uses the WU protocol endpoint: most fields, key auth."""
    up = WowBeUploader(None, "s", "k")
    assert up.url == "https://wow.meteo.be/api/v2/send/weatherunderground"
    assert "automaticreading" not in up.url
    assert "ecowitt" not in up.url


def test_wow_be_required_fields_present(sample_data):
    """The spec marks ID, PASSWORD and dateutc as required."""
    up = WowBeUploader(None, "916094001", "key")
    p = up._prune(up.build_params(sample_data))
    for field in ("ID", "PASSWORD", "dateutc"):
        assert field in p


def test_wow_be_accepts_complex_password(sample_data):
    """PASSWORD is free-form: "PIN code or Password", one field."""
    up = WowBeUploader(None, "916094001", "C0mpl3x-P@ssw0rd!")
    p = up.build_params(sample_data)
    assert p["PASSWORD"] == "C0mpl3x-P@ssw0rd!"
    assert p["ID"] == "916094001"


def test_wow_be_dateutc_format_matches_server_rule(sample_data):
    """The server requires Y-m-d H:i:s and rejects an ISO-8601 offset."""
    up = WowBeUploader(None, "s", "k")
    dateutc = up.build_params(sample_data)["dateutc"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", dateutc)
    assert "T" not in dateutc
    assert "+" not in dateutc and not dateutc.endswith("Z")
    # and it must be UTC, not local
    parsed = datetime.strptime(dateutc, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    assert abs((parsed - datetime.now(UTC)).total_seconds()) < 60


def test_wow_be_rainin_is_rate_not_accumulation(sample_data):
    """WOW-BE rainin is an instantaneous rate in in/h."""
    up = WowBeUploader(None, "s", "k")
    p = up.build_params(sample_data)
    # rain_rate 5.08 mm/h -> 0.2 in/h; rain_hourly (2.54mm) would give 0.1
    assert p["rainin"] == pytest.approx(0.2, abs=1e-3)


def test_wow_be_sends_only_one_pressure_field(sample_data):
    """baromin is authoritative: sending both would discard absbaromin."""
    up = WowBeUploader(None, "s", "k")
    p = up._prune(up.build_params(sample_data))
    assert "baromin" in p
    assert "absbaromin" not in p
    assert p["baromin"] == pytest.approx(hpa_to_inhg(1013.25), abs=1e-3)


def test_wow_be_falls_back_to_absbaromin(sample_data):
    """With only absolute pressure, let the server derive the relative."""
    data = {k: v for k, v in sample_data.items() if k != "pressure_relative"}
    up = WowBeUploader(None, "s", "k")
    p = up._prune(up.build_params(data))
    assert "absbaromin" in p
    assert "baromin" not in p
    assert p["absbaromin"] == pytest.approx(hpa_to_inhg(1000.0), abs=1e-3)


def test_wow_be_sends_no_pressure_when_unmapped():
    """Neither pressure field appears when neither is mapped."""
    up = WowBeUploader(None, "s", "k")
    p = up._prune(up.build_params({"temperature": 20.0}))
    assert "baromin" not in p
    assert "absbaromin" not in p


def test_wow_be_visibility_stays_km(sample_data):
    """WOW-BE takes kilometres, not miles."""
    up = WowBeUploader(None, "s", "k")
    assert up.build_params(sample_data)["visibility"] == pytest.approx(16.09, abs=1e-2)


def test_wow_be_sends_uv(sample_data):
    """UV is why the WU protocol is preferred over the WOW protocol."""
    up = WowBeUploader(None, "s", "k")
    assert up.build_params(sample_data)["UV"] == 4.0


def test_windy_uses_v2_endpoint():
    """The legacy /pws/update endpoint is deprecated as of 2026-01."""
    up = WindyUploader(None, "station-1", "pw")
    assert up.url == "https://stations.windy.com/api/v2/observation/update"
    assert "/pws/update" not in up.url


def test_windy_params_are_all_documented(sample_data):
    """Every query parameter must be a name the v2 API accepts."""
    valid = {
        "id",
        "ID",
        "station",
        "si",
        "stationId",
        "time",
        "ts",
        "dateutc",
        "wind",
        "windspeedmph",
        "gust",
        "windgustmph",
        "winddir",
        "humidity",
        "rh",
        "dewpoint",
        "dewptf",
        "pressure",
        "mbar",
        "baromin",
        "uv",
        "UV",
        "solarradiation",
        "precip",
        "rainin",
        "hourlyrainin",
        "temp",
        "tempf",
        "softwaretype",
        "stationtype",
        "PASSWORD",
    }
    up = WindyUploader(None, "station-1", "pw")
    for key in up.build_params(sample_data):
        assert key in valid, f"undocumented Windy parameter: {key}"


def test_windy_sends_absolute_pressure_via_mbar(sample_data):
    """Pressure goes in as hPa through mbar, not Pa through pressure.

    The v2 mbar parameter takes hectopascals directly, avoiding the
    hPa->Pa conversion the pressure parameter would need. Windy wants
    station pressure, so pressure_absolute is mapped.
    """
    up = WindyUploader(None, "station-1", "pw")
    p = up.build_params(sample_data)
    assert p["mbar"] == pytest.approx(1000.0)  # sample_data absolute hPa
    assert "pressure" not in p
    assert p["temp"] == pytest.approx(20.0)


def test_windy_metric_passthrough(sample_data):
    """Metric values are sent under metric names, unconverted."""
    up = WindyUploader(None, "station-1", "pw")
    p = up.build_params(sample_data)
    assert p["wind"] == pytest.approx(sample_data["wind_speed"])
    assert p["precip"] == pytest.approx(sample_data["rain_hourly"])
    assert p["solarradiation"] == pytest.approx(sample_data["solar_radiation"])


def test_owm_endpoint_is_measurements_not_stations_measurements():
    """The send path is /data/3.0/measurements; /stations/ in it 404s."""
    up = OpenWeatherMapUploader(None, "st-1", "apikey")
    assert up.url == "https://api.openweathermap.org/data/3.0/measurements"
    assert "stations/measurements" not in up.url


def test_owm_measurements_are_metric_not_si():
    """The station measurements endpoint takes Celsius and hPa.

    This is the opposite of OpenWeatherMap's read endpoints, which
    default to Kelvin and Pascals. The documented request example
    (temperature 18.7, pressure 1021) is Celsius and hPa.
    """
    up = OpenWeatherMapUploader(None, "st-1", "apikey")
    data = {
        "temperature": 18.7,
        "pressure_relative": 1021.0,
        "dewpoint": 16.0,
        "wind_speed": 1.2,
        "rain_hourly": 2.0,
        "visibility": 10.0,
    }
    p = up.build_params(data)
    assert p["temperature"] == pytest.approx(18.7)  # Celsius, not 291.85 K
    assert p["pressure"] == pytest.approx(1021.0)  # hPa, not 102100 Pa
    assert p["dew_point"] == pytest.approx(16.0)
    assert p["wind_speed"] == pytest.approx(1.2)
    assert p["rain_1h"] == pytest.approx(2.0)  # mm
    assert p["visibility_distance"] == pytest.approx(10.0)  # km, not 10000 m


def test_pwsweather_mapping(sample_data):
    """PWSWeather receives imperial values."""
    up = PWSWeatherUploader(None, "ST1", "pw")
    p = up.build_params(sample_data)
    assert p["tempf"] == pytest.approx(68.0)
    assert p["solarradiation"] == 450.0


@pytest.mark.parametrize(
    "uploader",
    [
        WundergroundUploader(None, "a", "b"),
        WowBeUploader(None, "a", "b"),
        PWSWeatherUploader(None, "a", "b"),
        WindyUploader(None, "a", "b"),
        OpenWeatherMapUploader(None, "a", "b"),
    ],
)
def test_empty_data_prunes_to_credentials_only(uploader):
    """With no sensor data, no measurement params are emitted."""
    pruned = uploader._prune(uploader.build_params({}))
    for field in ("tempf", "temp", "temperature", "humidity", "rh"):
        assert field not in pruned or pruned.get(field) is not None


def test_uploader_without_min_interval_is_always_due():
    """A zero interval means no throttling."""
    up = WowBeUploader(None, "s", "k")
    assert up.min_interval == 0
    assert up.is_due()
    up.mark_sent()
    assert up.is_due()


def test_uploader_throttles_until_interval_elapses():
    """A network is skipped until its own minimum has passed."""
    up = WowBeUploader(None, "s", "k", min_interval=300)
    up.last_sent = 1000.0
    assert not up.is_due(now=1000.0)
    assert not up.is_due(now=1299.0)
    assert up.is_due(now=1300.0)
    assert up.is_due(now=5000.0)


def test_throttled_uploader_waits_after_construction():
    """A restart rebuilds uploaders; the first send must still wait.

    Home Assistant rebuilds every uploader on restart. If a throttled
    uploader were due immediately, a restart shortly after a send would
    upload again at once and trip the provider's rate limit (Windy
    returns 429 within its 5-minute window). The throttle is therefore
    seeded at construction, so the first send waits min_interval.
    """
    up = WowBeUploader(None, "s", "k", min_interval=300)
    assert up.last_sent is not None
    # Not due until an interval has passed from construction.
    start = up.last_sent
    assert not up.is_due(now=start)
    assert not up.is_due(now=start + 299)
    assert up.is_due(now=start + 300)


def test_unthrottled_uploader_is_due_at_construction():
    """A zero-interval uploader has no seed and sends immediately."""
    up = WowBeUploader(None, "s", "k")  # min_interval defaults to 0
    assert up.last_sent is None
    assert up.is_due()


def test_throttle_counts_attempts_not_successes():
    """A failed attempt still consumed the provider's rate budget."""
    up = WowBeUploader(None, "s", "k", min_interval=300)
    up.mark_sent()  # as the coordinator does, regardless of outcome
    assert up.last_sent is not None
    assert not up.is_due(now=up.last_sent + 1)


def test_factory_applies_per_service_intervals():
    """Windy is throttled harder than WOW-BE by default."""
    cfg = {"station_id": "x", "key": "k"}
    wow = build_uploader(None, SERVICE_WOW_BE, cfg)
    windy = build_uploader(None, SERVICE_WINDY, cfg)
    assert wow.min_interval == MIN_SERVICE_INTERVAL[SERVICE_WOW_BE]
    assert windy.min_interval == MIN_SERVICE_INTERVAL[SERVICE_WINDY]
    assert windy.min_interval > wow.min_interval


def test_prune_drops_none():
    """_prune removes unset fields entirely rather than sending blanks."""
    up = WundergroundUploader(None, "a", "b")
    assert up._prune({"x": 1, "y": None}) == {"x": 1}


# --- CWOP (native APRS) -------------------------------------------------

# The worked example from NOAA's own FAQ at http://wxqa.com/faq.html
NOAA_EXAMPLE = "@060151z3316.04N/09631.96W_120/005g010t021r000p000P000h75b10322"
NOAA_LAT = 33 + 16.04 / 60
NOAA_LON = -(96 + 31.96 / 60)


def test_aprs_coordinates_match_noaa_format():
    """APRS uses ddmm.hh with mandatory leading zeros, not decimals."""
    assert format_latitude(NOAA_LAT) == "3316.04N"
    assert format_longitude(NOAA_LON) == "09631.96W"
    assert format_latitude(-33.5) == "3330.00S"
    assert format_longitude(4.5) == "00430.00E"


def test_cwop_packet_reproduces_noaa_example():
    """Byte-for-byte against the packet NOAA documents."""
    data = {
        "wind_direction": 120.0,
        "wind_speed": 5 / 2.236936,
        "wind_gust": 10 / 2.236936,
        "temperature": (21 - 32) * 5 / 9,
        "rain_hourly": 0.0,
        "rain_24h": 0.0,
        "rain_daily": 0.0,
        "humidity": 75.0,
        "pressure_relative": 1032.2,
    }
    packet = build_packet(
        "EW9876", NOAA_LAT, NOAA_LON, data, datetime(2026, 7, 6, 1, 51, tzinfo=UTC)
    )
    assert packet == f"EW9876>APRS,TCPIP*:{NOAA_EXAMPLE}"


def test_cwop_missing_required_fields_become_dots():
    """The first four fields are positional and must always be present."""
    packet = build_packet(
        "EW1", NOAA_LAT, NOAA_LON, {}, datetime(2026, 7, 6, 1, 51, tzinfo=UTC)
    )
    assert packet.endswith("_.../...g...t...")


@pytest.mark.parametrize(
    ("celsius", "expected"),
    [(-40.0, "t-40"), (-20.0, "t-04"), (-12.0, "t010"), (45.0, "t113")],
)
def test_cwop_temperature_encoding(celsius, expected):
    """Fahrenheit in three characters, negatives included."""
    packet = build_packet(
        "EW1", 0.0, 0.0, {"temperature": celsius}, datetime(2026, 1, 1, tzinfo=UTC)
    )
    assert expected in packet


def test_cwop_humidity_100_encodes_as_h00():
    """The field is two digits, so 100% is represented as h00."""
    packet = build_packet(
        "EW1", 0.0, 0.0, {"humidity": 100.0}, datetime(2026, 1, 1, tzinfo=UTC)
    )
    assert "h00" in packet


def test_cwop_pressure_is_tenths_of_millibar():
    """1013.2 hPa becomes b10132."""
    packet = build_packet(
        "EW1", 0.0, 0.0, {"pressure_relative": 1013.2}, datetime(2026, 1, 1, tzinfo=UTC)
    )
    assert "b10132" in packet


def test_cwop_uses_native_aprs_not_a_bridge():
    """No third-party HTTP relay: connect to CWOP directly."""
    up = CwopUploader(None, "EW9876", latitude=51.0, longitude=4.0)
    assert up.host == "cwop.aprs.net"
    assert up.port == 14580
    assert "cwop.rest" not in up.url
    assert not up.url.startswith("http")


def test_cwop_sends_no_credential(sample_data):
    """CWOP non-ham stations use the fixed passcode -1; keys are ignored."""
    up = CwopUploader(None, "EW9876", "ignored-secret", latitude=51.0, longitude=4.0)
    packet = up.build_params(sample_data)["packet"]
    assert "ignored-secret" not in packet


def test_cwop_interval_matches_noaa_rule():
    """NOAA asks for no more than one packet every five minutes."""
    assert MIN_SERVICE_INTERVAL[SERVICE_CWOP] == 300


def test_geo_services_need_coordinates():
    """CWOP cannot publish without lat/lon; others do not need it."""
    from custom_components.weather_uploader.const import GEO_SERVICES

    assert SERVICE_CWOP in GEO_SERVICES
    assert SERVICE_WOW_BE not in GEO_SERVICES
    assert SERVICE_WINDY not in GEO_SERVICES
