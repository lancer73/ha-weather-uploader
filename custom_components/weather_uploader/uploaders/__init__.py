"""Uploader implementations for supported weather networks."""

from __future__ import annotations

import logging

import aiohttp

from ..const import (
    CONF_KEY,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_STATION_ID,
    MIN_SERVICE_INTERVAL,
    SERVICE_CWOP,
    SERVICE_OPENWEATHERMAP,
    SERVICE_PWSWEATHER,
    SERVICE_WINDY,
    SERVICE_WOW_BE,
    SERVICE_WUNDERGROUND,
)
from .base import BaseUploader, UploaderError
from .cwop import CwopUploader, build_packet
from .openweathermap import OpenWeatherMapUploader
from .pwsweather import PWSWeatherUploader
from .windy import WindyUploader
from .wowbe import WowBeUploader
from .wunderground import WundergroundUploader

__all__ = [
    "BaseUploader",
    "CwopUploader",
    "OpenWeatherMapUploader",
    "PWSWeatherUploader",
    "UploaderError",
    "WindyUploader",
    "WowBeUploader",
    "WundergroundUploader",
    "build_packet",
    "build_uploader",
]


_LOGGER = logging.getLogger(__name__)


def build_uploader(
    session: aiohttp.ClientSession,
    service: str,
    config: dict,
) -> BaseUploader | None:
    """Construct an uploader for a service, or None if unsupported."""
    station_id = config.get(CONF_STATION_ID)
    key = config.get(CONF_KEY)
    interval = MIN_SERVICE_INTERVAL.get(service, 0)

    # CWOP authenticates with the fixed passcode -1: it identifies by
    # station id only, with no key. It also needs coordinates on every
    # packet -- without them it would report from (0, 0) (Null Island),
    # which a pre-geo config entry surviving an upgrade could trigger
    # silently. Skip it instead, so the warning in _build_uploaders
    # surfaces the problem and the user re-adds it with coordinates.
    if service == SERVICE_CWOP:
        latitude = config.get(CONF_LATITUDE)
        longitude = config.get(CONF_LONGITUDE)
        if not station_id or latitude is None or longitude is None:
            return None
    elif not key:
        return None

    if service == SERVICE_CWOP:
        return CwopUploader(
            session,
            station_id,
            min_interval=interval,
            latitude=float(latitude),
            longitude=float(longitude),
        )

    if service == SERVICE_WUNDERGROUND:
        return WundergroundUploader(session, station_id, key, interval)

    if service == SERVICE_WOW_BE:
        return WowBeUploader(session, station_id, key, min_interval=interval)

    if service == SERVICE_PWSWEATHER:
        return PWSWeatherUploader(session, station_id, key, interval)

    if service == SERVICE_WINDY:
        return WindyUploader(session, station_id, key, interval)

    if service == SERVICE_OPENWEATHERMAP:
        return OpenWeatherMapUploader(session, station_id, key, interval)

    return None
