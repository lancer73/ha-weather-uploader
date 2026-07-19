"""Base class and unit helpers for weather network uploaders."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=30)

# Response bodies are truncated before logging. Never log request params:
# they carry credentials for every provider except Windy.
_BODY_LOG_LIMIT = 200


def c_to_f(value: float) -> float:
    """Celsius to Fahrenheit."""
    return value * 9 / 5 + 32


def ms_to_mph(value: float) -> float:
    """Metres per second to miles per hour."""
    return value * 2.236936


def mm_to_in(value: float) -> float:
    """Millimetres to inches."""
    return value / 25.4


def hpa_to_inhg(value: float) -> float:
    """Hectopascals to inches of mercury."""
    return value * 0.02952998


def km_to_mi(value: float) -> float:
    """Kilometres to miles."""
    return value / 1.609344


class UploaderError(Exception):
    """Raised when an upload fails in a way worth surfacing."""


class BaseUploader(ABC):
    """Common behaviour for all providers.

    Subclasses map the normalized data dict onto provider-specific
    parameters. The default transport is a GET with query parameters,
    which is what the Weather Underground-derived APIs all use.
    """

    name: str = "unknown"
    url: str = ""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        station_id: str | None,
        key: str,
        min_interval: int = 0,
    ) -> None:
        """Initialise the uploader.

        ``min_interval`` is the minimum number of seconds between sends
        to this network. The coordinator polls on a single global
        cadence, so each uploader gates itself: a fast poll cannot trip
        a slow provider's rate limit.
        """
        self._session = session
        self._id = station_id
        self._key = key
        self.min_interval = min_interval
        self.last_error: str | None = None
        # Seed the throttle as if a send just happened, so the first
        # upload after start (or after a Home Assistant restart, which
        # rebuilds every uploader) waits min_interval rather than firing
        # immediately. Without this, a restart shortly after a send would
        # upload again at once and trip a provider's rate limit -- Windy
        # returns 429 inside its 5-minute window. min_interval <= 0
        # (no throttle) is unaffected: is_due short-circuits to True.
        self.last_sent: float | None = time.monotonic() if min_interval > 0 else None

    def is_due(self, now: float | None = None) -> bool:
        """Return True when enough time has passed to send again.

        Uses a monotonic clock so a system time change cannot stall an
        uploader indefinitely. A throttled uploader starts with its
        clock seeded to construction time (see ``__init__``), so the
        first send waits ``min_interval`` after start rather than firing
        immediately -- this is what keeps a restart from tripping a
        provider's rate limit.
        """
        if self.min_interval <= 0 or self.last_sent is None:
            return True
        current = time.monotonic() if now is None else now
        return (current - self.last_sent) >= self.min_interval

    def mark_sent(self) -> None:
        """Record a send attempt for throttling purposes.

        Called after any completed attempt, successful or not: a failed
        request still consumed the provider's rate budget, and retrying
        immediately would make a 429 worse.
        """
        self.last_sent = time.monotonic()

    @abstractmethod
    def build_params(self, data: dict[str, float]) -> dict[str, Any]:
        """Map normalized data onto provider query parameters."""

    @staticmethod
    def _prune(params: dict[str, Any]) -> dict[str, Any]:
        """Drop unset values so we never send empty fields."""
        return {k: v for k, v in params.items() if v is not None}

    @staticmethod
    def conv(
        data: dict[str, float],
        key: str,
        func: Any = None,
        digits: int = 3,
    ) -> float | None:
        """Fetch and optionally convert a value, returning None if absent."""
        value = data.get(key)
        if value is None:
            return None
        if func is not None:
            value = func(value)
        return round(value, digits)

    async def send(self, data: dict[str, float]) -> bool:
        """Send an observation. Returns True on success."""
        params = self._prune(self.build_params(data))
        try:
            async with self._session.get(
                self.url, params=params, timeout=TIMEOUT
            ) as response:
                body = (await response.text())[:_BODY_LOG_LIMIT]
                if response.status != 200:
                    self.last_error = f"HTTP {response.status}: {body}"
                    _LOGGER.warning(
                        "%s upload failed (HTTP %s): %s",
                        self.name,
                        response.status,
                        body,
                    )
                    return False
                self.last_error = None
                _LOGGER.debug("%s upload OK: %s", self.name, body)
                return True
        except aiohttp.ClientError as err:
            self.last_error = str(err)
            _LOGGER.warning("%s upload error: %s", self.name, err)
            return False
        except TimeoutError as err:
            self.last_error = f"timeout: {err}"
            _LOGGER.warning("%s upload timed out", self.name)
            return False
