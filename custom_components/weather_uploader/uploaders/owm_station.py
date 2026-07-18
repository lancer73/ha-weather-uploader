"""OpenWeatherMap station management for the config flow.

OpenWeatherMap is unusual among the supported networks: a station has
no dashboard signup, and its measurement ``station_id`` is an internal
identifier that only exists once the station has been created through
the Weather Stations API. This module creates (or finds) that station
so the config flow can obtain the ID on the user's behalf.

Verified against https://openweathermap.org/api/stations on 2026-07-18.

Two identifiers, deliberately kept distinct:

- ``external_id``  -- a human-chosen label the user supplies
  (for example ``HA_Garden``). Ours to send.
- ``id`` / ``ID``  -- the internal identifier OpenWeatherMap generates.
  This is what ``/measurements`` requires, and what the integration
  stores as ``station_id``.

Endpoints used:

- ``GET  /data/3.0/stations``  -- list the account's stations, so an
  existing station with our ``external_id`` can be reused instead of
  creating a duplicate. Creation is otherwise not idempotent: posting
  the same ``external_id`` twice yields two stations.
- ``POST /data/3.0/stations`` -- register a station; returns 201 and
  the created object, whose ``ID`` is the internal identifier.

Errors arrive as ``{"code", "message"}`` with a six-digit code whose
first three digits are the HTTP status. :class:`StationError` carries a
message suitable for showing in the config flow.
"""

from __future__ import annotations

import logging

import aiohttp

_LOGGER = logging.getLogger(__name__)

STATIONS_URL = "https://api.openweathermap.org/data/3.0/stations"
_TIMEOUT = aiohttp.ClientTimeout(total=30)


class StationError(Exception):
    """A station could not be created or looked up.

    ``reason`` is a short machine key the config flow maps to a
    translated error string; ``str(self)`` is a human-readable detail
    for logs.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        """Store the reason key and optional detail."""
        super().__init__(detail or reason)
        self.reason = reason


async def _read_error(response: aiohttp.ClientResponse) -> str:
    """Extract OpenWeatherMap's ``message`` from an error body."""
    try:
        payload = await response.json()
    except (aiohttp.ClientError, ValueError):
        return f"HTTP {response.status}"
    if isinstance(payload, dict) and "message" in payload:
        return f"HTTP {response.status}: {payload['message']}"
    return f"HTTP {response.status}"


async def find_station_id(
    session: aiohttp.ClientSession, api_key: str, external_id: str
) -> str | None:
    """Return the internal ID of an existing station, or None.

    Looks the ``external_id`` up in the account's station list so a
    repeated setup reuses the station instead of creating duplicates.
    """
    try:
        async with session.get(
            STATIONS_URL, params={"appid": api_key}, timeout=_TIMEOUT
        ) as response:
            if response.status == 401:
                raise StationError("invalid_auth", await _read_error(response))
            if response.status != 200:
                raise StationError("cannot_connect", await _read_error(response))
            stations = await response.json()
    except aiohttp.ClientError as err:
        raise StationError("cannot_connect", str(err)) from err
    except TimeoutError as err:
        raise StationError("cannot_connect", "timeout") from err

    if not isinstance(stations, list):
        return None
    for station in stations:
        if isinstance(station, dict) and station.get("external_id") == external_id:
            # The list endpoint returns the internal id as "id"; the
            # create endpoint returns it as "ID". Accept either.
            internal = station.get("id") or station.get("ID")
            if internal:
                _LOGGER.debug(
                    "Reusing existing OpenWeatherMap station %s for %s",
                    internal,
                    external_id,
                )
                return str(internal)
    return None


async def create_station(
    session: aiohttp.ClientSession,
    api_key: str,
    external_id: str,
    name: str,
    latitude: float,
    longitude: float,
    altitude: float,
) -> str:
    """Create a station and return its internal ID.

    If a station with ``external_id`` already exists on the account it
    is reused, so calling this more than once does not create
    duplicates.
    """
    existing = await find_station_id(session, api_key, external_id)
    if existing is not None:
        return existing

    body = {
        "external_id": external_id,
        "name": name,
        "latitude": round(latitude, 5),
        "longitude": round(longitude, 5),
        "altitude": round(altitude),
    }
    try:
        async with session.post(
            STATIONS_URL,
            params={"appid": api_key},
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=_TIMEOUT,
        ) as response:
            if response.status == 401:
                raise StationError("invalid_auth", await _read_error(response))
            # The spec documents 201 for a created station.
            if response.status != 201:
                raise StationError("cannot_create", await _read_error(response))
            created = await response.json()
    except aiohttp.ClientError as err:
        raise StationError("cannot_connect", str(err)) from err
    except TimeoutError as err:
        raise StationError("cannot_connect", "timeout") from err

    internal = created.get("ID") or created.get("id")
    if not internal:
        raise StationError("cannot_create", "response contained no station ID")
    _LOGGER.debug("Created OpenWeatherMap station %s for %s", internal, external_id)
    return str(internal)
