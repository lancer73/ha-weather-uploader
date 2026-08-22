"""Weathercloud uploader.

Weathercloud's API takes integer-scaled metric values (most fields are
multiplied by 10) as GET query parameters. Two quirks drive the shape of
this uploader:

* Values are scaled integers, not the floats the other HTTP uploaders
  send -- e.g. 20.5 C is sent as ``205``, 1013.0 hPa as ``10130``, and
  wind speed in m/s x 10. A wrong scale uploads plausible-but-wrong data,
  so the conversions here are the part most worth verifying live.
* The HTTP status is always ``200``; the real result code is in the
  response body (``200`` success, ``401`` bad credentials, ``429`` rate
  limited, ...). So success is decided from the body, not the status
  line -- the base ``send`` would treat every rejection as a success.

Credentials: the device Weathercloud ID (WID) maps to ``station_id`` and
the device Key to ``key``. Both travel as query parameters over TLS and
will appear in Weathercloud's own access logs.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .base import _BODY_LOG_LIMIT, TIMEOUT, BaseUploader

_LOGGER = logging.getLogger(__name__)


def _scaled(value: float | None, factor: int) -> int | None:
    """Scale a value by ``factor`` and round to an int, or None if unset."""
    if value is None:
        return None
    return round(value * factor)


class WeathercloudUploader(BaseUploader):
    """Upload to the Weathercloud API (v01)."""

    #: Normalized reading keys this network accepts. Drives the
    #: measurement count reported by the status sensor.
    SUPPORTED_READINGS: frozenset[str] = frozenset(
        {
            "dewpoint",
            "humidity",
            "indoor_humidity",
            "indoor_temperature",
            "pressure_relative",
            "rain_daily",
            "rain_rate",
            "solar_radiation",
            "temperature",
            "uv_index",
            "wind_direction",
            "wind_gust",
            "wind_speed",
        }
    )

    name = "Weathercloud"
    url = "https://api.weathercloud.net/v01/set"

    def build_params(self, data: dict[str, float]) -> dict[str, Any]:
        """Map normalized data onto Weathercloud's scaled parameters.

        Query-parameter form (``set?wid=..&key=..&temp=..``) is used so
        the request rides the shared session like the other GET
        uploaders. Units follow the API doc: temperatures and pressure
        and rain x10, wind speed m/s x10, humidity and wind direction
        unscaled.
        """
        get = data.get
        return {
            "wid": self._id,
            "key": self._key,
            "temp": _scaled(get("temperature"), 10),
            "tempin": _scaled(get("indoor_temperature"), 10),
            "dew": _scaled(get("dewpoint"), 10),
            "hum": _scaled(get("humidity"), 1),
            "humin": _scaled(get("indoor_humidity"), 1),
            "bar": _scaled(get("pressure_relative"), 10),
            "wspd": _scaled(get("wind_speed"), 10),
            "wspdhi": _scaled(get("wind_gust"), 10),
            "wdir": _scaled(get("wind_direction"), 1),
            "rain": _scaled(get("rain_daily"), 10),
            "rainrate": _scaled(get("rain_rate"), 10),
            "solarrad": _scaled(get("solar_radiation"), 10),
            "uvi": _scaled(get("uv_index"), 10),
            # soil_moisture is deliberately omitted: our internal value is
            # volumetric water content in percent, but Weathercloud's
            # `soilmoist` field expects soil tension in centibars. These
            # are different physical quantities with no fixed conversion
            # (it depends on the soil water-retention curve), so sending
            # our percentage would publish a wrong tension reading.
        }

    async def send(self, data: dict[str, float]) -> bool:
        """Send an observation, judging success from the response body.

        Weathercloud always answers HTTP 200 and carries the real status
        code in the body, so the body -- not the status line -- decides
        success. A body of ``200`` is success; anything else is recorded
        with a ``wc_<code>`` error code (e.g. ``wc_429`` rate limited,
        ``wc_401`` bad credentials).
        """
        params = self._prune(self.build_params(data))
        self._last_payload = self._redact_payload(params)
        try:
            async with self._session.get(
                self.url, params=params, timeout=TIMEOUT
            ) as response:
                body = (await response.text())[:_BODY_LOG_LIMIT]
                code = body.strip()
                if code != "200":
                    self.record_error(
                        f"wc_{code}" if code else "http_error",
                        f"Weathercloud responded {code or response.status}",
                    )
                    _LOGGER.warning(
                        "%s upload rejected (body %s)", self.name, code
                    )
                    return False
                self.clear_error()
                _LOGGER.debug("%s upload OK", self.name)
                return True
        except aiohttp.ClientError as err:
            self.record_error(self.classify_client_error(err), str(err))
            _LOGGER.warning("%s upload error: %s", self.name, err)
            return False
        except TimeoutError as err:
            self.record_error("timeout", f"timeout: {err}")
            _LOGGER.warning("%s upload timed out", self.name)
            return False
