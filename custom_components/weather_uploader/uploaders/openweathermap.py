"""OpenWeatherMap station measurements uploader.

Uses the Weather Stations API 3.0 (beta). Verified against
https://openweathermap.org/api/stations on 2026-07-18.

- Endpoint: ``POST https://api.openweathermap.org/data/3.0/measurements``
  (note: NOT ``/data/3.0/stations/measurements`` -- that path 404s).
- Body is a JSON array of measurement objects, so several samples can
  be sent at once; this integration sends one per call.
- Success is HTTP 204.
- The API key travels as the ``appid`` query parameter.

Units are the documented ones, and they are metric -- NOT the Kelvin
and Pascals that OpenWeatherMap's *read* endpoints (current weather,
One Call) default to. The measurements write endpoint takes:

- ``temperature`` / ``dew_point``: degrees Celsius
- ``pressure``: hectopascals
- ``wind_speed`` / ``wind_gust``: m/s
- ``rain_1h`` / ``rain_24h``: millimetres
- ``visibility_distance``: kilometres

The documented request example (``"temperature": 18.7``,
``"pressure": 1021``) is decisive: those are Celsius and hPa, not
Kelvin and Pascals.

The station itself is created during configuration (see
``owm_station.py``), which calls ``POST /data/3.0/stations`` and stores
the internal ID it returns. This uploader only sends measurements to
that station.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp

from .base import TIMEOUT, BaseUploader

_LOGGER = logging.getLogger(__name__)


class OpenWeatherMapUploader(BaseUploader):
    """Upload to the OpenWeatherMap station measurements endpoint.

    The internal sensor units already match what this endpoint wants
    for most fields, so most values pass straight through.
    """

    #: Normalized reading keys this network accepts. Drives the
    #: measurement count reported by the status sensor.
    SUPPORTED_READINGS: frozenset[str] = frozenset(
        {
            "dewpoint",
            "humidity",
            "pressure_relative",
            "rain_24h",
            "rain_hourly",
            "temperature",
            "visibility",
            "wind_direction",
            "wind_gust",
            "wind_speed",
        }
    )

    name = "OpenWeatherMap"
    url = "https://api.openweathermap.org/data/3.0/measurements"

    def build_params(self, data: dict[str, float]) -> dict[str, Any]:
        """Map normalized data onto an OWM measurement object.

        The endpoint is metric, matching this integration's internal
        units, so temperature, pressure, and wind pass through without
        conversion.
        """
        conv = self.conv
        visibility = data.get("visibility")
        return {
            "station_id": self._id,
            "dt": int(time.time()),
            "temperature": conv(data, "temperature", digits=2),
            "dew_point": conv(data, "dewpoint", digits=2),
            "humidity": conv(data, "humidity"),
            "pressure": conv(data, "pressure_relative", digits=2),
            "wind_speed": conv(data, "wind_speed", digits=2),
            "wind_gust": conv(data, "wind_gust", digits=2),
            "wind_deg": conv(data, "wind_direction"),
            "rain_1h": conv(data, "rain_hourly", digits=2),
            "rain_24h": conv(data, "rain_24h", digits=2),
            "visibility_distance": None if visibility is None else round(visibility, 2),
        }

    async def send(self, data: dict[str, float]) -> bool:
        """POST a single-element measurement batch to OWM."""
        payload = self._prune(self.build_params(data))
        self._last_payload = self._redact_payload(payload)
        try:
            async with self._session.post(
                self.url,
                params={"appid": self._key},
                json=[payload],
                headers={"Content-Type": "application/json"},
                timeout=TIMEOUT,
            ) as response:
                body = (await response.text())[:200]
                # The spec documents exactly 204 for a successful
                # measurement dispatch. 200 and 201 are the update- and
                # create-station codes and do not apply here; accepting
                # them would only mask a misrouted request.
                if response.status == 204:
                    self.clear_error()
                    return True
                self.record_error(
                    "http_error",
                    f"HTTP {response.status}: {body}",
                    status=response.status,
                )
                _LOGGER.warning(
                    "OpenWeatherMap upload failed (HTTP %s): %s",
                    response.status,
                    body,
                )
                return False
        except aiohttp.ClientError as err:
            self.record_error(self.classify_client_error(err), str(err))
            _LOGGER.warning("OpenWeatherMap upload error: %s", err)
            return False
        except TimeoutError:
            self.record_error("timeout", "timeout")
            _LOGGER.warning("OpenWeatherMap upload timed out")
            return False
