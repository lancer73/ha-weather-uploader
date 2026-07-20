"""Windy.com personal weather station uploader.

Uses the Windy Stations API v2, verified against
https://stations.windy.com/api-reference on 2026-07-18.

The legacy endpoint this integration first shipped against
(``POST https://stations.windy.com/pws/update/{key}`` with a JSON
body) is deprecated: Windy's documentation states the new API is
effective January 2026 and the legacy API is no longer supported. This
uploader targets the current endpoint instead.

Contract
========

- ``GET https://stations.windy.com/api/v2/observation/update``
- All values travel as query-string parameters, not a JSON body.
- Authentication is the station password, sent as the ``PASSWORD``
  query parameter. (A Bearer ``Authorization`` header is also accepted;
  the query parameter is simpler and is what the WU-compatible clients
  use.)
- The endpoint is Weather Underground compatible: it accepts both
  metric parameters (``temp``, ``dewpoint``, ``mbar``, ``wind``) and
  the imperial WU aliases (``tempf``, ``dewptf``, ``baromin``,
  ``windspeedmph``). This integration holds metric internally, so it
  sends the metric names.
- Success is HTTP 200. The documented failure codes are handled in
  :meth:`send`: 400 (bad password or payload), 401 (no password), 409
  (duplicate), and 429 (rate limited, with a ``retry_after`` timestamp).
- Data may be sent at most once every 5 minutes. That is a documented
  limit, so ``MIN_SERVICE_INTERVAL`` for Windy is a real figure rather
  than a guess.

Pressure
========

Pressure is sent via ``mbar`` (hectopascals), which the API takes
directly -- avoiding the error-prone hPa->Pa conversion the ``pressure``
parameter (Pascals) would require.

The API's ``pressure``/``mbar`` field carries no sea-level qualifier,
unlike the WU ``baromin`` field. This uploader sends *absolute* (station)
pressure, matching how Windy's own PWS clients report. Map
``pressure_absolute`` for this network; if only sea-level pressure is
available it will still be accepted, but will read slightly high.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .base import TIMEOUT, BaseUploader

_LOGGER = logging.getLogger(__name__)


class WindyUploader(BaseUploader):
    """Upload to Windy via the v2 WU-compatible observation endpoint."""

    #: Normalized reading keys this network accepts. Drives the
    #: measurement count reported by the status sensor.
    SUPPORTED_READINGS: frozenset[str] = frozenset(
        {
            "dewpoint",
            "humidity",
            "pressure_absolute",
            "rain_hourly",
            "solar_radiation",
            "temperature",
            "uv_index",
            "wind_direction",
            "wind_gust",
            "wind_speed",
        }
    )

    name = "Windy"
    url = "https://stations.windy.com/api/v2/observation/update"

    def build_params(self, data: dict[str, float]) -> dict[str, Any]:
        """Map normalized data onto Windy v2 query parameters.

        Units already match the API (Celsius, hPa via ``mbar``, m/s,
        millimetres, W/m2), so values pass through without conversion.
        """
        conv = self.conv
        return {
            "station": self._id,
            "temp": conv(data, "temperature"),
            "dewpoint": conv(data, "dewpoint"),
            "humidity": conv(data, "humidity"),
            "mbar": conv(data, "pressure_absolute"),
            "wind": conv(data, "wind_speed"),
            "gust": conv(data, "wind_gust"),
            "winddir": conv(data, "wind_direction"),
            "precip": conv(data, "rain_hourly"),
            "uv": conv(data, "uv_index"),
            "solarradiation": conv(data, "solar_radiation"),
        }

    async def send(self, data: dict[str, float]) -> bool:
        """Send one observation as a GET with query parameters.

        The password rides in the query string as the API requires. It
        is therefore kept out of ``last_error`` and the logs, which
        record only the endpoint and status.
        """
        params = self._prune(self.build_params(data))
        self._last_payload = self._redact_payload(params)
        params["PASSWORD"] = self._key
        try:
            async with self._session.get(
                self.url, params=params, timeout=TIMEOUT
            ) as response:
                body = (await response.text())[:200]
                if response.status == 200:
                    self.clear_error()
                    _LOGGER.debug("Windy upload OK")
                    return True

                # Map the documented failure codes to actionable text.
                reason = {
                    400: "rejected: bad password or payload",
                    401: "no station password supplied",
                    409: "duplicate observation",
                    429: "rate limited (max once per 5 minutes)",
                }.get(response.status, body)
                self.record_error(
                    "http_error",
                    f"HTTP {response.status}: {reason}",
                    status=response.status,
                )
                _LOGGER.warning(
                    "Windy upload failed (HTTP %s): %s", response.status, reason
                )
                return False
        except aiohttp.ClientError as err:
            self.record_error(self.classify_client_error(err), str(err))
            _LOGGER.warning("Windy upload error: %s", err)
            return False
        except TimeoutError:
            self.record_error("timeout", "timeout")
            _LOGGER.warning("Windy upload timed out")
            return False
