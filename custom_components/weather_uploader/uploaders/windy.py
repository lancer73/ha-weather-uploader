"""Windy.com personal weather station uploader."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .base import TIMEOUT, BaseUploader

_LOGGER = logging.getLogger(__name__)


class WindyUploader(BaseUploader):
    """Upload to Windy.

    Windy takes metric units and a JSON body. The API key sits in the
    URL path, so it is never logged by this integration -- but keep it
    out of any debug HTTP tracing you enable elsewhere.

    Note Windy wants *station* pressure, not sea-level-adjusted. Map
    ``pressure_absolute`` for this provider to be correct.
    """

    name = "Windy"

    def build_params(self, data: dict[str, float]) -> dict[str, Any]:
        """Map normalized data onto a Windy observation object."""
        conv = self.conv
        pressure = data.get("pressure_absolute")
        return {
            "station": self._id,
            "temp": conv(data, "temperature"),
            "dewpoint": conv(data, "dewpoint"),
            "rh": conv(data, "humidity"),
            "pressure": None if pressure is None else round(pressure * 100, 1),
            "wind": conv(data, "wind_speed"),
            "gust": conv(data, "wind_gust"),
            "winddir": conv(data, "wind_direction"),
            "precip": conv(data, "rain_hourly"),
            "uv": conv(data, "uv_index"),
        }

    async def send(self, data: dict[str, float]) -> bool:
        """POST a single observation to Windy."""
        payload = self._prune(self.build_params(data))
        url = f"https://stations.windy.com/pws/update/{self._key}"
        try:
            async with self._session.post(
                url, json={"observations": [payload]}, timeout=TIMEOUT
            ) as response:
                body = (await response.text())[:200]
                if response.status != 200:
                    self.last_error = f"HTTP {response.status}: {body}"
                    _LOGGER.warning(
                        "Windy upload failed (HTTP %s): %s", response.status, body
                    )
                    return False
                self.last_error = None
                _LOGGER.debug("Windy upload OK: %s", body)
                return True
        except aiohttp.ClientError as err:
            self.last_error = str(err)
            _LOGGER.warning("Windy upload error: %s", err)
            return False
        except TimeoutError:
            self.last_error = "timeout"
            _LOGGER.warning("Windy upload timed out")
            return False
