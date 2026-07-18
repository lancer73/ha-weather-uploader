"""OpenWeatherMap station measurements uploader."""

from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp

from .base import TIMEOUT, BaseUploader

_LOGGER = logging.getLogger(__name__)

KELVIN_OFFSET = 273.15


class OpenWeatherMapUploader(BaseUploader):
    """Upload to the OpenWeatherMap station measurements endpoint.

    This integration does not register stations. Create the station via
    the OWM API first and use the returned station id here.

    OWM wants Kelvin and Pascals. The API key travels as a query
    parameter.
    """

    name = "OpenWeatherMap"
    url = "https://api.openweathermap.org/data/3.0/stations/measurements"

    def build_params(self, data: dict[str, float]) -> dict[str, Any]:
        """Map normalized data onto an OWM measurement object."""
        conv = self.conv
        temperature = data.get("temperature")
        dewpoint = data.get("dewpoint")
        pressure = data.get("pressure_relative")
        return {
            "station_id": self._id,
            "dt": int(time.time()),
            "temperature": (
                None if temperature is None else round(temperature + KELVIN_OFFSET, 2)
            ),
            "dew_point": (
                None if dewpoint is None else round(dewpoint + KELVIN_OFFSET, 2)
            ),
            "humidity": conv(data, "humidity"),
            "pressure": None if pressure is None else round(pressure * 100, 1),
            "wind_speed": conv(data, "wind_speed"),
            "wind_gust": conv(data, "wind_gust"),
            "wind_deg": conv(data, "wind_direction"),
            "rain_1h": conv(data, "rain_hourly"),
            "visibility_distance": (
                None
                if data.get("visibility") is None
                else round(data["visibility"] * 1000)
            ),
        }

    async def send(self, data: dict[str, float]) -> bool:
        """POST a measurement batch to OWM."""
        payload = self._prune(self.build_params(data))
        try:
            async with self._session.post(
                self.url,
                params={"appid": self._key},
                json=[payload],
                timeout=TIMEOUT,
            ) as response:
                body = (await response.text())[:200]
                if response.status not in (200, 204):
                    self.last_error = f"HTTP {response.status}: {body}"
                    _LOGGER.warning(
                        "OpenWeatherMap upload failed (HTTP %s): %s",
                        response.status,
                        body,
                    )
                    return False
                self.last_error = None
                return True
        except aiohttp.ClientError as err:
            self.last_error = str(err)
            _LOGGER.warning("OpenWeatherMap upload error: %s", err)
            return False
        except TimeoutError:
            self.last_error = "timeout"
            _LOGGER.warning("OpenWeatherMap upload timed out")
            return False
