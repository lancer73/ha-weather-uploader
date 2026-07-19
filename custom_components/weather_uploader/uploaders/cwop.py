"""CWOP (Citizen Weather Observer Program) uploader.

CWOP feeds NOAA's MADIS, which supplies National Weather Service
forecasters and researchers. Along with WOW-BE (the KMI/KNMI network),
it is one of the supported networks whose data is put to operational
and scientific use by a national meteorological service.

CWOP is not an HTTP API. It is APRS-IS: a plain TCP connection to port
14580, a login line, one packet, disconnect. This uploader speaks that
protocol natively via ``asyncio.open_connection`` -- no dependency, and
no third party.

Why not a bridge
================

Public HTTP-to-APRS bridges exist (send.cwop.rest is the best known)
and other forwarders use them. This one does not, deliberately:

- A bridge is an unaffiliated third party. The best-known one is a
  personal project whose author states plainly that it has no relation
  to CWOP, findu, or NOAA.
- Every observation, the station ID, and the station's **exact
  latitude and longitude** would transit it.
- It buys nothing but convenience. CWOP requires no password for
  non-ham stations (the passcode is literally ``-1``), so a bridge is
  not holding a credential on our behalf -- it is only saving us a TCP
  socket, which Home Assistant can open natively.

Trading the station's home coordinates to a third party to avoid
writing 40 lines of socket code is a bad exchange.

Protocol, per http://wxqa.com/faq.html
======================================

- Connect to ``cwop.aprs.net:14580`` (non-ham CWOP servers; these
  require no validation code).
- Read the server banner, send::

      user <CALL> pass -1 vers <software> <version>

- Read the login acknowledgement, then send one packet::

      <CALL>>APRS,TCPIP*:@DDHHMMz<lat>/<lon>_<wx fields>

- Disconnect. There is no application-level acknowledgement; TCP
  delivery is the acknowledgement.

Field encoding is unforgiving and positional:

- Wind direction, wind speed, gust, and temperature are **required in
  that order**, with ``...`` substituted when a value is unavailable.
- ``b`` is pressure in **tenths of a millibar**, five digits.
- ``h`` is humidity in two digits, where ``h00`` means 100%.
- ``r`` is rain over the past hour and ``p`` over the past 24 hours,
  both in hundredths of an inch. **MADIS accepts only these two**;
  ``P`` (since local midnight) is carried but ignored by MADIS.
- Coordinates use APRS/LORAN ``ddmm.hhN/dddmm.hhW`` with mandatory
  leading zeros -- not decimal degrees.

Rate limit: NOAA asks for no more than one packet every 5 minutes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any

from .base import BaseUploader, c_to_f, mm_to_in, ms_to_mph

_LOGGER = logging.getLogger(__name__)

DEFAULT_HOST = "cwop.aprs.net"
DEFAULT_PORT = 14580

#: Non-ham CWOP stations authenticate with this literal passcode.
CWOP_PASSCODE = "-1"

SOFTWARE_NAME = "HomeAssistantWeatherUploader"
SOFTWARE_VERSION = "1.0"

CONNECT_TIMEOUT = 15
IO_TIMEOUT = 15


def format_latitude(value: float) -> str:
    """Return APRS ``ddmm.hhN`` latitude. Leading zeros are required."""
    hemisphere = "N" if value >= 0 else "S"
    value = abs(value)
    degrees = int(value)
    minutes = (value - degrees) * 60
    return f"{degrees:02d}{minutes:05.2f}{hemisphere}"


def format_longitude(value: float) -> str:
    """Return APRS ``dddmm.hhW`` longitude. Leading zeros are required."""
    hemisphere = "E" if value >= 0 else "W"
    value = abs(value)
    degrees = int(value)
    minutes = (value - degrees) * 60
    return f"{degrees:03d}{minutes:05.2f}{hemisphere}"


def _fixed(value: float | None, width: int, scale: float = 1.0) -> str:
    """Render a value as zero-padded digits, or dots when absent."""
    if value is None:
        return "." * width
    scaled = round(value * scale)
    scaled = max(0, min(scaled, 10**width - 1))
    return f"{scaled:0{width}d}"


def _temperature(value: float | None) -> str:
    """Render temperature in Fahrenheit, three chars, may be negative."""
    if value is None:
        return "..."
    fahrenheit = round(c_to_f(value))
    if fahrenheit < 0:
        # Negative temperatures use a minus sign plus two digits.
        return f"-{min(abs(fahrenheit), 99):02d}"
    return f"{min(fahrenheit, 999):03d}"


def build_packet(
    callsign: str,
    latitude: float,
    longitude: float,
    data: dict[str, float],
    timestamp: datetime | None = None,
) -> str:
    """Assemble a complete APRS weather packet.

    Pure function: no I/O, so the wire format is directly testable.
    """
    moment = timestamp or datetime.now(UTC)
    when = moment.strftime("%d%H%M")

    position = f"{format_latitude(latitude)}/{format_longitude(longitude)}"

    # These four are required, in this order.
    wind_dir = _fixed(data.get("wind_direction"), 3)
    wind_speed = _fixed(
        None if data.get("wind_speed") is None else ms_to_mph(data["wind_speed"]), 3
    )
    gust = _fixed(
        None if data.get("wind_gust") is None else ms_to_mph(data["wind_gust"]), 3
    )
    temperature = _temperature(data.get("temperature"))

    packet = (
        f"{callsign}>APRS,TCPIP*:@{when}z{position}"
        f"_{wind_dir}/{wind_speed}g{gust}t{temperature}"
    )

    # MADIS only ingests r (hourly) and p (24 hour) rainfall.
    if data.get("rain_hourly") is not None:
        packet += f"r{_fixed(mm_to_in(data['rain_hourly']), 3, scale=100)}"
    if data.get("rain_24h") is not None:
        packet += f"p{_fixed(mm_to_in(data['rain_24h']), 3, scale=100)}"
    if data.get("rain_daily") is not None:
        packet += f"P{_fixed(mm_to_in(data['rain_daily']), 3, scale=100)}"

    humidity = data.get("humidity")
    if humidity is not None:
        # h00 means 100%: the field is two digits only.
        rounded = round(humidity)
        packet += f"h{0 if rounded >= 100 else max(0, rounded):02d}"

    pressure = data.get("pressure_relative")
    if pressure is not None:
        # Tenths of a millibar, five digits: 1013.2 hPa -> 10132.
        packet += f"b{_fixed(pressure, 5, scale=10)}"

    solar = data.get("solar_radiation")
    if solar is not None:
        watts = round(solar)
        if watts < 1000:
            packet += f"L{watts:03d}"
        else:
            # Values of 1000+ use lowercase l with the leading 1 dropped.
            packet += f"l{min(watts - 1000, 999):03d}"

    return packet


class CwopUploader(BaseUploader):
    """Send observations to CWOP over native APRS-IS.

    ``station_id`` carries the CWOP callsign (e.g. ``EW9876``). The
    ``key`` field is unused: CWOP non-ham stations authenticate with
    the fixed passcode ``-1``, which is not a secret and is not
    configurable.
    """

    name = "CWOP"

    def __init__(
        self,
        session: Any,
        station_id: str | None,
        key: str = "",
        min_interval: int = 0,
        latitude: float = 0.0,
        longitude: float = 0.0,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
    ) -> None:
        """Initialise the uploader.

        ``session`` is accepted for interface uniformity and unused:
        this protocol is not HTTP.
        """
        super().__init__(session, station_id, key, min_interval)
        self.latitude = latitude
        self.longitude = longitude
        self.host = host
        self.port = port
        self.url = f"aprs://{host}:{port}"

    def build_params(self, data: dict[str, float]) -> dict[str, Any]:
        """Return the packet in a dict, for diagnostics and tests.

        CWOP has no query parameters; this exists so the base class
        interface holds and so the wire format is inspectable.
        """
        return {
            "packet": build_packet(self._id or "", self.latitude, self.longitude, data)
        }

    async def send(self, data: dict[str, float]) -> bool:
        """Connect, log in, send one packet, disconnect."""
        packet = build_packet(self._id or "", self.latitude, self.longitude, data)
        login = (
            f"user {self._id} pass {CWOP_PASSCODE} "
            f"vers {SOFTWARE_NAME} {SOFTWARE_VERSION}"
        )

        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=CONNECT_TIMEOUT,
            )

            # The server sends a banner first; it carries no meaning.
            await asyncio.wait_for(reader.readline(), timeout=IO_TIMEOUT)

            writer.write(f"{login}\r\n".encode("ascii"))
            await asyncio.wait_for(writer.drain(), timeout=IO_TIMEOUT)

            # Login acknowledgement, also safely ignorable.
            await asyncio.wait_for(reader.readline(), timeout=IO_TIMEOUT)

            writer.write(f"{packet}\r\n".encode("ascii"))
            await asyncio.wait_for(writer.drain(), timeout=IO_TIMEOUT)

            # There is no application-level ack: TCP delivery is the ack.
            self.last_error = None
            _LOGGER.debug("CWOP packet sent: %s", packet)
            return True
        except TimeoutError:
            self.last_error = "timeout"
            _LOGGER.warning("CWOP upload timed out")
            return False
        except (OSError, UnicodeEncodeError) as err:
            self.last_error = str(err)
            _LOGGER.warning("CWOP upload error: %s", err)
            return False
        finally:
            if writer is not None:
                writer.close()
                with contextlib.suppress(OSError):
                    await writer.wait_closed()
