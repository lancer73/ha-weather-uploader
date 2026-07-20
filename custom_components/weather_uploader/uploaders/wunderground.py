"""Weather Underground PWS uploader."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .base import BaseUploader, c_to_f, hpa_to_inhg, km_to_mi, mm_to_in, ms_to_mph


class WundergroundUploader(BaseUploader):
    """Upload to the Weather Underground personal weather station API.

    Credentials travel as query parameters over TLS. The station key
    will appear in Weather Underground's own access logs.
    """

    #: Normalized reading keys this network accepts. Drives the
    #: measurement count reported by the status sensor.
    SUPPORTED_READINGS: frozenset[str] = frozenset(
        {
            "dewpoint",
            "humidity",
            "indoor_humidity",
            "indoor_temperature",
            "leaf_wetness",
            "pm10",
            "pm25",
            "pressure_relative",
            "rain_daily",
            "rain_hourly",
            "rain_monthly",
            "rain_weekly",
            "rain_yearly",
            "soil_moisture",
            "soil_temperature",
            "solar_radiation",
            "temperature",
            "uv_index",
            "visibility",
            "wind_direction",
            "wind_gust",
            "wind_gust_direction",
            "wind_speed",
        }
    )

    name = "Weather Underground"
    url = (
        "https://weatherstation.wunderground.com"
        "/weatherstation/updateweatherstation.php"
    )

    def build_params(self, data: dict[str, float]) -> dict[str, Any]:
        """Map normalized data onto WU parameters."""
        conv = self.conv
        return {
            "ID": self._id,
            "PASSWORD": self._key,
            "dateutc": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
            "action": "updateraw",
            "softwaretype": "HomeAssistant",
            "tempf": conv(data, "temperature", c_to_f),
            "dewptf": conv(data, "dewpoint", c_to_f),
            "humidity": conv(data, "humidity"),
            "baromin": conv(data, "pressure_relative", hpa_to_inhg),
            "windspeedmph": conv(data, "wind_speed", ms_to_mph),
            "windgustmph": conv(data, "wind_gust", ms_to_mph),
            "winddir": conv(data, "wind_direction"),
            "windgustdir": conv(data, "wind_gust_direction"),
            "rainin": conv(data, "rain_hourly", mm_to_in),
            "dailyrainin": conv(data, "rain_daily", mm_to_in),
            "weeklyrainin": conv(data, "rain_weekly", mm_to_in),
            "monthlyrainin": conv(data, "rain_monthly", mm_to_in),
            "yearlyrainin": conv(data, "rain_yearly", mm_to_in),
            "solarradiation": conv(data, "solar_radiation"),
            "UV": conv(data, "uv_index"),
            "indoortempf": conv(data, "indoor_temperature", c_to_f),
            "indoorhumidity": conv(data, "indoor_humidity"),
            "soiltempf": conv(data, "soil_temperature", c_to_f),
            "soilmoisture": conv(data, "soil_moisture"),
            "leafwetness": conv(data, "leaf_wetness"),
            "AqPM2.5": conv(data, "pm25"),
            "AqPM10": conv(data, "pm10"),
            "visibility": conv(data, "visibility", km_to_mi, digits=2),
        }
