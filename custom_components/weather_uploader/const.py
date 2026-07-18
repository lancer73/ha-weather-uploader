"""Constants for the Weather Network Uploader integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "weather_uploader"

CONF_INTERVAL: Final = "interval"
CONF_SERVICES: Final = "services"
CONF_STATION_ID: Final = "station_id"
CONF_KEY: Final = "key"

# The coordinator polls sensors at this cadence. Each network is then
# throttled independently against its own MIN_SERVICE_INTERVAL, so this
# is a sampling rate rather than a per-network send rate. 60s matches
# RMI's recommendation for WOW-BE; slower networks simply skip ticks.
DEFAULT_INTERVAL: Final = 60
MIN_INTERVAL: Final = 60

# Normalized internal units. Every uploader converts *from* these.
#   temperature / dewpoint / soil / indoor : degrees Celsius
#   pressure_absolute / pressure_relative  : hPa
#   wind_speed / wind_gust                 : m/s
#   wind_direction / wind_gust_direction   : degrees (0-360)
#   rain_*                                 : mm (rain_rate is mm/h)
#     rain_hourly: past 60 minutes; rain_24h: past 24 hours (rolling);
#     rain_daily: since local midnight. CWOP/MADIS ingests only the
#     first two.
#   solar_radiation                        : W/m2
#   uv_index                               : index
#   illuminance                            : lux
#   humidity / soil_moisture / leaf_wetness: percent
#   pm25 / pm10                            : ug/m3
#   co2                                    : ppm
#   lightning_count                        : count
#   lightning_distance / visibility        : km
#   cloud_base                             : m
SENSOR_KEYS: Final[list[str]] = [
    "temperature",
    "dewpoint",
    "humidity",
    "pressure_absolute",
    "pressure_relative",
    "wind_speed",
    "wind_gust",
    "wind_direction",
    "wind_gust_direction",
    "rain_rate",
    "rain_hourly",
    "rain_24h",
    "rain_daily",
    "rain_weekly",
    "rain_monthly",
    "rain_yearly",
    "solar_radiation",
    "uv_index",
    "illuminance",
    "indoor_temperature",
    "indoor_humidity",
    "soil_temperature",
    "soil_moisture",
    "leaf_wetness",
    "pm25",
    "pm10",
    "co2",
    "lightning_count",
    "lightning_distance",
    "visibility",
    "cloud_base",
]

SERVICE_WUNDERGROUND: Final = "wunderground"
SERVICE_WOW_BE: Final = "wow_be"
SERVICE_PWSWEATHER: Final = "pwsweather"
SERVICE_WINDY: Final = "windy"
SERVICE_OPENWEATHERMAP: Final = "openweathermap"
SERVICE_CWOP: Final = "cwop"
SERVICE_WETTERNETZWERK: Final = "wetternetzwerk"
SERVICE_METEO_SERVICES: Final = "meteo_services"

SERVICES: Final[list[str]] = [
    SERVICE_WOW_BE,
    SERVICE_WUNDERGROUND,
    SERVICE_CWOP,
    SERVICE_PWSWEATHER,
    SERVICE_WINDY,
    SERVICE_OPENWEATHERMAP,
    SERVICE_WETTERNETZWERK,
    SERVICE_METEO_SERVICES,
]

# Networks that need the station's coordinates on every observation.
# The config flow only asks for them when one of these is selected:
# precise home coordinates are sensitive and should not be collected
# for networks that never receive them.
GEO_SERVICES: Final[frozenset[str]] = frozenset({SERVICE_CWOP, SERVICE_METEO_SERVICES})

# Networks whose only identifier is a station id, with no credential.
# The config flow says so plainly rather than showing a password field
# that implies a secret exists.
UNAUTHENTICATED_SERVICES: Final[frozenset[str]] = frozenset({SERVICE_METEO_SERVICES})

CONF_LATITUDE: Final = "latitude"
CONF_LONGITUDE: Final = "longitude"
CONF_ALTITUDE: Final = "altitude"

WOW_BE_HOST: Final = "https://wow.meteo.be"

# Minimum seconds between sends, per network. The coordinator skips a
# network on a tick when its last successful send is more recent than
# this, so a fast global interval cannot trip a slow provider's limit.
#
# WOW-BE: RMI recommends 60s and rate-limits at 20/min/site.
# Windy:  documents roughly a 5 minute minimum.
# CWOP:   NOAA asks for no faster than one packet every 5 minutes
#         (wxqa.com/faq.html, Q1). This one is a published rule.
# The others: values used by comparable multi-network forwarders; not
#   verified against published limits, so they are floors rather than
#   optimums.
MIN_SERVICE_INTERVAL: Final[dict[str, int]] = {
    SERVICE_WOW_BE: 60,
    SERVICE_WUNDERGROUND: 60,
    SERVICE_CWOP: 300,
    SERVICE_PWSWEATHER: 300,
    SERVICE_WINDY: 300,
    SERVICE_OPENWEATHERMAP: 60,
    SERVICE_WETTERNETZWERK: 600,
    SERVICE_METEO_SERVICES: 300,
}

# A mapped entity whose state has not changed for longer than this is
# treated as stale and dropped, rather than republished as if current.
# Home Assistant keeps the last value of a sensor that silently stops
# reporting -- a dead battery station looks identical to a calm one --
# so without this a failed station publishes fiction indefinitely.
#
# 3600s is deliberately generous: a genuinely static reading (rain
# totals overnight, a windless day) must not be discarded. State
# machine restarts refresh last_updated, so this is a floor on
# detection latency, not a guarantee.
DEFAULT_MAX_SENSOR_AGE: Final = 3600
CONF_MAX_SENSOR_AGE: Final = "max_sensor_age"

ATTR_LAST_PAYLOAD: Final = "last_payload"
ATTR_LAST_ERROR: Final = "last_error"
ATTR_LAST_SUCCESS: Final = "last_success"
ATTR_STALE_SENSORS: Final = "stale_sensors"
ATTR_MISSING_SENSORS: Final = "missing_sensors"
ATTR_SENSOR_COUNT: Final = "sensors_published"
