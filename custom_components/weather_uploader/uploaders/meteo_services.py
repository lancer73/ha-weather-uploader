"""Meteo-Services (weather365.net) uploader.

A German network run by s.t.o.r.k. Media GmbH that folds contributed
observations into local forecast models.

Protocol per
https://meteo-services.com/wettersatelliten-und-wetterradar/wetternetzwerk-mitmachen.html
(the operator's own "for experts / programmers" section):

- ``POST`` form-encoded to ``channel1.meteo-services.com/stations/index.php``.
- Metric throughout: degrees Celsius, hPa, m/s, mm.
- ``datum`` is ``YYYYMMDDHHmm`` in UTC, plus ``utcstamp`` as a Unix
  epoch. Both are sent.
- Requires the station's latitude, longitude, and altitude on every
  request.

SECURITY -- this network has no authentication
==============================================

The only identifier is ``stationid``, issued by email at registration.
There is no key, password, or token: anyone who learns or guesses a
station id can publish observations as that station, and there is
nothing to rotate afterwards.

Unlike WOW-BE's Ecowitt protocol -- which this integration declines to
implement because an authenticated alternative exists on the same
network -- there is no authenticated way to reach Meteo-Services. The
choice is to participate on these terms or not at all. It is therefore
offered, and the config flow and README say plainly what the trade is.

The server returns HTTP 200 with an empty body regardless of outcome,
so a successful send here means "accepted the request", not "stored the
observation". The status entity cannot distinguish those.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from typing import Any

import aiohttp

from .base import TIMEOUT, BaseUploader

_LOGGER = logging.getLogger(__name__)


def dewpoint_celsius(temperature: float, humidity: float) -> float:
    """Magnus-Tetens dewpoint, matching the operator's reference code."""
    rh = min(100.0, max(0.01, humidity))
    a, b = 17.62, 243.12
    gamma = math.log(rh / 100.0) + (a * temperature) / (b + temperature)
    return (b * gamma) / (a - gamma)


def cloudbase_metres(temperature: float, dewpoint: float) -> float:
    """Espy's estimate of the lifted condensation level."""
    return max(0.0, (temperature - dewpoint) * 125.0)


def wind_chill_celsius(temperature: float, wind_speed: float) -> float:
    """JAG/TI wind chill. Only valid for cold, windy conditions.

    Returns the air temperature unchanged outside the formula's domain
    rather than extrapolating it somewhere it does not apply.
    """
    if temperature > 10.0 or wind_speed < 1.3:
        return temperature
    kmh = wind_speed * 3.6
    factor = kmh**0.16
    return 13.12 + 0.6215 * temperature - 11.37 * factor + 0.3965 * temperature * factor


class MeteoServicesUploader(BaseUploader):
    """Upload to the Meteo-Services network.

    ``station_id`` is the only identifier; the ``key`` field is unused
    because the protocol has no credential.
    """

    name = "Meteo-Services"
    url = "https://channel1.meteo-services.com/stations/index.php"

    def __init__(
        self,
        session: aiohttp.ClientSession,
        station_id: str | None,
        key: str = "",
        min_interval: int = 0,
        latitude: float = 0.0,
        longitude: float = 0.0,
        altitude: float = 0.0,
    ) -> None:
        """Initialise the uploader."""
        super().__init__(session, station_id, key, min_interval)
        self.latitude = latitude
        self.longitude = longitude
        self.altitude = altitude

    def build_params(self, data: dict[str, float]) -> dict[str, Any]:
        """Map normalized data onto Meteo-Services form fields.

        Values are already in this network's units, so most fields pass
        straight through. Dewpoint, cloud base, and wind chill are
        derived only when their inputs are mapped, and only using
        formulae the operator's own documentation names.

        Evapotranspiration and humidex are accepted by the API but not
        sent: deriving them well needs inputs this integration does not
        collect, and a plausible-looking wrong value is worse than an
        absent one.
        """
        conv = self.conv
        now = datetime.now(UTC)

        params: dict[str, Any] = {
            "stationid": self._id,
            "lat": round(self.latitude, 4),
            "long": round(self.longitude, 4),
            "alt": round(self.altitude, 1),
            "datum": now.strftime("%Y%m%d%H%M"),
            "utcstamp": int(now.timestamp()),
            "t2m": conv(data, "temperature", digits=1),
            "relhum": conv(data, "humidity", digits=0),
            "press": conv(data, "pressure_relative", digits=2),
            "windspeed": conv(data, "wind_speed", digits=1),
            "windgust": conv(data, "wind_gust", digits=1),
            "winddir": conv(data, "wind_direction", digits=0),
            "radi": conv(data, "solar_radiation", digits=1),
            "uvi": conv(data, "uv_index", digits=1),
            "rainrate": conv(data, "rain_rate", digits=2),
            "rainh": conv(data, "rain_hourly", digits=2),
            "raind": conv(data, "rain_daily", digits=2),
            "soiltemp": conv(data, "soil_temperature", digits=1),
            "t005m": conv(data, "soil_temperature", digits=1),
            "soilmoisture": conv(data, "soil_moisture", digits=1),
            "leafwetness": conv(data, "leaf_wetness", digits=1),
        }

        temperature = data.get("temperature")
        humidity = data.get("humidity")

        dewpoint = data.get("dewpoint")
        if dewpoint is None and temperature is not None and humidity is not None:
            dewpoint = dewpoint_celsius(temperature, humidity)
        if dewpoint is not None:
            params["dew2m"] = round(dewpoint, 1)
            if temperature is not None:
                params["cloudbase"] = round(cloudbase_metres(temperature, dewpoint), 1)

        wind_speed = data.get("wind_speed")
        if temperature is not None and wind_speed is not None:
            params["wchill"] = round(wind_chill_celsius(temperature, wind_speed), 1)

        cloud_base = data.get("cloud_base")
        if cloud_base is not None:
            params["cloudbase"] = round(cloud_base, 1)

        return params

    async def send(self, data: dict[str, float]) -> bool:
        """POST a form-encoded observation.

        The server answers 200 with an empty body whatever happens, so
        this can only report transport success.
        """
        payload = self._prune(self.build_params(data))
        try:
            async with self._session.post(
                self.url, data=payload, timeout=TIMEOUT
            ) as response:
                body = (await response.text())[:200]
                if response.status == 200:
                    self.last_error = None
                    _LOGGER.debug("Meteo-Services accepted request: %r", body)
                    return True
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
