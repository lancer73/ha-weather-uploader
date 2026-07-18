"""Wetternetzwerk.pro uploader.

A German weather network that speaks the Weather Underground protocol:
same endpoint shape, same parameter names, same imperial units.

Verified 2026-07-17: the endpoint reaches credential validation and
returns ``{"error":"credentials","state":"error"}`` for a bad key, so
the protocol assumption holds.

The credential travels as a query parameter, so it lands in the
provider's access logs. That is inherent to the WU protocol.

The reference forwarder this was modelled on uses a 600 s interval for
this network. That figure is another operator's choice rather than a
published limit; it is treated as a floor.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .base import BaseUploader, c_to_f, hpa_to_inhg, mm_to_in, ms_to_mph


class WetternetzwerkUploader(BaseUploader):
    """Upload to Wetternetzwerk.pro via the Weather Underground protocol."""

    name = "Wetternetzwerk"
    url = "https://api.wetternetzwerk.pro/weatherstation/updateweatherstation.php"

    def build_params(self, data: dict[str, float]) -> dict[str, Any]:
        """Map normalized data onto WU-protocol parameters."""
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
            "solarradiation": conv(data, "solar_radiation"),
            "UV": conv(data, "uv_index"),
            "indoortempf": conv(data, "indoor_temperature", c_to_f),
            "indoorhumidity": conv(data, "indoor_humidity"),
            "soiltempf": conv(data, "soil_temperature", c_to_f),
            "soilmoisture": conv(data, "soil_moisture"),
        }
