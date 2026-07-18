"""PWSWeather (AerisWeather) uploader."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .base import BaseUploader, c_to_f, hpa_to_inhg, mm_to_in, ms_to_mph


class PWSWeatherUploader(BaseUploader):
    """Upload to PWSWeather.

    The API is a Weather Underground derivative. Credentials travel as
    query parameters.
    """

    name = "PWSWeather"
    url = "https://pwsupdate.pwsweather.com/api/v1/submitwx"

    def build_params(self, data: dict[str, float]) -> dict[str, Any]:
        """Map normalized data onto PWSWeather parameters."""
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
            "rainin": conv(data, "rain_hourly", mm_to_in),
            "dailyrainin": conv(data, "rain_daily", mm_to_in),
            "monthlyrainin": conv(data, "rain_monthly", mm_to_in),
            "yearlyrainin": conv(data, "rain_yearly", mm_to_in),
            "solarradiation": conv(data, "solar_radiation"),
            "UV": conv(data, "uv_index"),
            "soiltempf": conv(data, "soil_temperature", c_to_f),
            "soilmoisture": conv(data, "soil_moisture"),
        }
