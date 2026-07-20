"""WOW-BE (RMI/KMI Belgium) uploader.

WOW-BE is the platform the RMI built after the Met Office announced the
retirement of WOW. It is not the Met Office codebase: despite a shared
parameter vocabulary, the endpoint, transport, and several field
semantics differ.

This uploader uses WOW-BE's **Weather Underground protocol** endpoint.
WOW-BE offers three protocols and this is the best fit:

- ``/send/weatherunderground`` -- key-authenticated, 16 measurement
  fields. Used here.
- ``/send/wow`` -- key-authenticated, 15 measurement fields. Identical
  security properties, but lacks ``UV``.
- ``/send/ecowitt`` -- **no authentication.** Identifies the station by
  an MD5 of its MAC address, which is a public identifier rather than a
  secret. Not implemented, deliberately; see README.

Verified against https://wow.meteo.be/docs/api/ (OpenAPI 3.1, v2.0) and
the live endpoint on 2026-07-16:

- ``POST /api/v2/send/weatherunderground``, JSON body. The legacy Met
  Office ``GET /automaticreading`` path does not exist on this host.
- Required fields: ``ID``, ``PASSWORD``, ``dateutc``.
- ``PASSWORD`` is documented as "Authentication Key (PIN code or
  Password)" -- one field, either credential style, no mode to select.
- ``dateutc`` must match ``Y-m-d H:i:s`` and is interpreted as UTC. The
  server rejects an ISO-8601 offset form with an explicit message; the
  spec's ``format: date-time`` is misleading.
- ``rainin`` is an instantaneous rain **rate** in inches/hour. The
  legacy WOW-UK protocol used the same name for an hourly accumulation.
- ``visibility`` is **kilometres**, not miles.
- ``baromin`` is relative (sea-level) pressure; ``absbaromin`` is
  absolute (station) pressure. See :meth:`build_params` for the
  precedence rule.
- Responses: 200, 403 (invalid site credentials), 422 (validation),
  429 (rate limited: 20/min/site, 600/min/IP).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import aiohttp

from .base import TIMEOUT, BaseUploader, c_to_f, hpa_to_inhg, mm_to_in, ms_to_mph

_LOGGER = logging.getLogger(__name__)

DEFAULT_HOST = "https://wow.meteo.be"

#: The server validates dateutc against exactly this format and rejects
#: an ISO-8601 offset. Do not "modernise" this to isoformat().
DATEUTC_FORMAT = "%Y-%m-%d %H:%M:%S"


class WowBeUploader(BaseUploader):
    """Upload to WOW-BE via its Weather Underground protocol endpoint.

    The credential travels in a JSON request body rather than a query
    string, so it stays out of the server's access logs and out of any
    intercepting proxy's URL logging. That is a real improvement over
    every other provider this integration supports.
    """

    #: Normalized reading keys this network accepts. Drives the
    #: measurement count reported by the status sensor.
    SUPPORTED_READINGS: frozenset[str] = frozenset(
        {
            "dewpoint",
            "humidity",
            "pressure_absolute",
            "pressure_relative",
            "rain_daily",
            "rain_rate",
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

    name = "WOW-BE"

    def __init__(
        self,
        session: aiohttp.ClientSession,
        station_id: str | None,
        key: str,
        host: str = DEFAULT_HOST,
        min_interval: int = 0,
    ) -> None:
        """Initialise the uploader."""
        super().__init__(session, station_id, key, min_interval)
        self.host = host.rstrip("/")
        self.url = f"{self.host}/api/v2/send/weatherunderground"

    def build_params(self, data: dict[str, float]) -> dict[str, Any]:
        """Map normalized data onto the WOW-BE JSON body.

        Pressure follows WOW-BE's documented precedence: ``baromin`` is
        authoritative, and when both fields are present the server uses
        it and discards ``absbaromin``. Sending both is therefore a
        no-op for the absolute value, so this sends exactly one:

        - ``pressure_relative`` mapped -> send ``baromin``. The
          sea-level reduction is ours.
        - only ``pressure_absolute`` mapped -> send ``absbaromin`` and
          let the server derive the relative value using the altitude
          recorded at station registration.

        Note ``rain_rate`` feeds ``rainin``, not ``rain_hourly``.
        Mapping an accumulation there would publish a plausible but
        wrong rate.
        """
        conv = self.conv
        params: dict[str, Any] = {
            "ID": self._id,
            "PASSWORD": self._key,
            "dateutc": datetime.now(UTC).strftime(DATEUTC_FORMAT),
            "action": "updateraw",
            "softwaretype": "HomeAssistant",
            "tempf": conv(data, "temperature", c_to_f),
            "dewptf": conv(data, "dewpoint", c_to_f),
            "humidity": conv(data, "humidity"),
            "windspeedmph": conv(data, "wind_speed", ms_to_mph),
            "windgustmph": conv(data, "wind_gust", ms_to_mph),
            "winddir": conv(data, "wind_direction"),
            "windgustdir": conv(data, "wind_gust_direction"),
            "rainin": conv(data, "rain_rate", mm_to_in),
            "dailyrainin": conv(data, "rain_daily", mm_to_in),
            "solarradiation": conv(data, "solar_radiation"),
            "UV": conv(data, "uv_index"),
            "soiltempf": conv(data, "soil_temperature", c_to_f),
            "soilmoisture": conv(data, "soil_moisture"),
            "visibility": conv(data, "visibility", digits=2),
        }

        relative = conv(data, "pressure_relative", hpa_to_inhg)
        if relative is not None:
            params["baromin"] = relative
        else:
            params["absbaromin"] = conv(data, "pressure_absolute", hpa_to_inhg)

        return params

    async def send(self, data: dict[str, float]) -> bool:
        """POST an observation to WOW-BE."""
        payload = self._prune(self.build_params(data))
        self._last_payload = self._redact_payload(payload)
        try:
            async with self._session.post(
                self.url,
                json=payload,
                headers={"Accept": "application/json"},
                timeout=TIMEOUT,
            ) as response:
                body = (await response.text())[:200]
                if response.status == 200:
                    self.last_error = None
                    return True
                if response.status == 403:
                    self.last_error = "invalid site credentials (HTTP 403)"
                elif response.status == 422:
                    self.last_error = f"validation failed (HTTP 422): {body}"
                elif response.status == 429:
                    self.last_error = "rate limited (HTTP 429)"
                else:
                    self.last_error = f"HTTP {response.status}: {body}"
                _LOGGER.warning("%s upload failed: %s", self.name, self.last_error)
                return False
        except aiohttp.ClientError as err:
            self.last_error = str(err)
            _LOGGER.warning("%s upload error: %s", self.name, err)
            return False
        except TimeoutError:
            self.last_error = "timeout"
            _LOGGER.warning("%s upload timed out", self.name)
            return False
