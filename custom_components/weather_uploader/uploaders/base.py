"""Base class and unit helpers for weather network uploaders."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import aiohttp
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# total bounds the whole request; sock_connect bounds the connection
# phase (DNS resolution + TCP handshake) on its own, so a DNS hang
# raises ConnectionTimeoutError -- distinguishable from a slow server
# response (a read timeout) -- rather than eventually tripping the total.
TIMEOUT = aiohttp.ClientTimeout(total=30, sock_connect=10)

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
        self._last_error: str | None = None
        self._last_error_code: str | None = None
        self._last_error_time: datetime | None = None
        self._last_payload: dict[str, Any] = {}
        # Seed the throttle as if a send just happened, so the first
        # upload after start (or after a Home Assistant restart, which
        # rebuilds every uploader) waits min_interval rather than firing
        # immediately. Without this, a restart shortly after a send would
        # upload again at once and trip a provider's rate limit -- Windy
        # returns 429 inside its 5-minute window. min_interval <= 0
        # (no throttle) is unaffected: is_due short-circuits to True.
        self.last_sent: float | None = time.monotonic() if min_interval > 0 else None

    @property
    def last_error(self) -> str | None:
        """The last upload error message, or None. Credentials redacted."""
        return self._last_error

    @last_error.setter
    def last_error(self, value: str | None) -> None:
        """Store an error message, redacting the key first.

        Error strings can embed the request -- an aiohttp InvalidURL, for
        instance, includes the URL, which for some providers carries the
        key as a query parameter. last_error surfaces in entity
        attributes, so redact the key value before storing it.
        """
        self._last_error = self._redact(value)

    @property
    def last_error_code(self) -> str | None:
        """A short, stable code for the last error (e.g. 'dns', 'http_500').

        Stable across occurrences of the same failure, so it is suitable
        as a sensor state that the recorder can graph and count. None
        when the last send succeeded.
        """
        return self._last_error_code

    @property
    def last_error_time(self) -> datetime | None:
        """When the last error was recorded, or None."""
        return self._last_error_time

    def record_error(
        self, code: str, message: str, *, status: int | None = None
    ) -> None:
        """Record a failed send: a short code, a message, and the time.

        ``code`` is a stable short string for the sensor state; when a
        ``status`` is given it is folded in as ``http_<status>``.
        """
        self._last_error_code = f"http_{status}" if status is not None else code
        self.last_error = message  # redacts the key
        self._last_error_time = dt_util.utcnow()

    def restore_error_state(
        self,
        *,
        code: str | None,
        message: str | None,
        error_time: datetime | None,
    ) -> None:
        """Seed error state from values restored across a restart.

        A restart rebuilds every uploader with empty error state, so the
        first successful cycle would otherwise write ``last_error_time =
        None`` over whatever the status sensor restored, discarding the
        real time of the last pre-restart failure. Seeding the uploader
        itself means the coordinator reads the restored values back on
        the next cycle, so the history survives. Only fills empty fields,
        so a fresh error recorded before this runs is never clobbered.
        The message is assumed already redacted (it round-tripped through
        the sensor from a prior redacted store); it is set directly
        rather than through the redacting setter.
        """
        if self._last_error_code is None:
            self._last_error_code = code
        if self._last_error is None:
            self._last_error = message
        if self._last_error_time is None:
            self._last_error_time = error_time

    def clear_error(self) -> None:
        """Record a successful send: no error, no code."""
        self._last_error = None
        self._last_error_code = None
        # last_error_time is left as the last failure's time, so history
        # shows when the most recent problem happened; the code being
        # None is what signals "currently OK".

    @staticmethod
    def classify_client_error(err: Exception) -> str:
        """Map an aiohttp/OS exception to a short, stable code.

        Distinguishes the common transient failures -- DNS resolution,
        connection refused/reset, TLS -- so the sensor state is
        meaningful rather than a single opaque 'connection'.
        """
        import socket

        if isinstance(err, aiohttp.ClientConnectorError):
            os_err = getattr(err, "os_error", None)
            if isinstance(os_err, socket.gaierror):
                return "dns"
            return "connection"
        # ConnectionTimeoutError and SocketTimeoutError are both
        # subclasses of ServerTimeoutError, so they must be checked first.
        # A connection-phase timeout covers DNS resolution and the TCP
        # handshake -- the closest aiohttp gets to "DNS timed out."
        if isinstance(err, aiohttp.ConnectionTimeoutError):
            return "connect_timeout"
        if isinstance(err, aiohttp.SocketTimeoutError):
            return "read_timeout"
        if isinstance(err, aiohttp.ServerTimeoutError):
            return "timeout"
        if isinstance(err, aiohttp.ClientConnectionError):
            return "connection"
        if isinstance(err, aiohttp.ClientSSLError):
            return "tls"
        return "client_error"

    def _redact(self, text: str | None) -> str | None:
        """Replace the key value with a placeholder in an error string."""
        if text and self._key:
            return text.replace(self._key, "***")
        return text

    def is_due(self, now: float | None = None, tolerance: float = 0.0) -> bool:
        """Return True when enough time has passed to send again.

        Uses a monotonic clock so a system time change cannot stall an
        uploader indefinitely. A throttled uploader starts with its
        clock seeded to construction time (see ``__init__``), so the
        first send waits ``min_interval`` after start rather than firing
        immediately -- this is what keeps a restart from tripping a
        provider's rate limit.

        ``tolerance`` forgives a small shortfall against ``min_interval``.
        It exists because sends are dispatched a moment after the poll
        tick (staggered, plus network latency), so ``last_sent`` is
        stamped slightly after the tick. Without tolerance, a network
        whose ``min_interval`` equals the poll interval reads as "not yet
        due" by that fraction on every tick and so fires only every other
        tick. The caller sizes the tolerance to the dispatch window and
        caps it well below ``min_interval``, so it corrects the
        measurement offset without letting a network actually send faster
        than its rate limit -- a network cannot send twice in one cycle,
        so the true send-to-send gap stays at least the poll interval.
        """
        if self.min_interval <= 0 or self.last_sent is None:
            return True
        current = time.monotonic() if now is None else now
        return (current - self.last_sent) >= (self.min_interval - tolerance)

    def mark_sent(self) -> None:
        """Record a send attempt for throttling purposes.

        Called after any completed attempt, successful or not: a failed
        request still consumed the provider's rate budget, and retrying
        immediately would make a 429 worse.
        """
        self.last_sent = time.monotonic()

    def seed_from_last_attempt(self, seconds_since_attempt: float) -> None:
        """Seed the throttle from a known prior attempt across a restart.

        Normally ``last_sent`` is seeded to construction time, so after a
        restart the first send waits a full ``min_interval`` -- safe, but
        wasteful when the real last attempt was long ago. When the last
        attempt time is recoverable (from the restored last-success and
        last-error timestamps, whichever is later -- an attempt is one or
        the other), pass how long ago it was, and the throttle is placed
        as if that attempt really happened then: an already-elapsed
        interval makes the network due at once, a recent one still waits
        out the remainder.

        ``seconds_since_attempt`` must be clamped by the caller to a sane,
        non-negative range, since it derives from wall-clock times that a
        clock change during downtime could distort. It has no effect on
        an unthrottled uploader.
        """
        if self.min_interval <= 0:
            return
        # Place last_sent in the monotonic past by the elapsed wall-clock
        # time. is_due then compares against min_interval exactly as if
        # the attempt had happened then. Cap the backdating at
        # min_interval: any older and the network is simply due now, and
        # we never need to look further back than one interval.
        elapsed = max(0.0, min(seconds_since_attempt, self.min_interval))
        self.last_sent = time.monotonic() - elapsed

    #: Normalized reading keys this network accepts. Subclasses override
    #: this; it drives the measurement count the status sensor reports,
    #: so that count means the same thing for every network regardless of
    #: wire format. CWOP packs many measurements into one packet string,
    #: which would otherwise count as a single field.
    SUPPORTED_READINGS: frozenset[str] = frozenset()

    def measurement_count(self, data: dict[str, float]) -> int:
        """Return how many weather measurements this network sent.

        This counts the mapped readings the network actually accepts and
        that were present this cycle -- not the number of keys in the
        request. Those differ: the request also carries metadata
        (timestamps, station id, software type), and CWOP encodes every
        measurement into a single packet string. Counting on the reading
        side gives a consistent "measurements sent" figure across all
        networks.
        """
        return sum(1 for key in self.SUPPORTED_READINGS if data.get(key) is not None)

    @abstractmethod
    def build_params(self, data: dict[str, float]) -> dict[str, Any]:
        """Map normalized data onto provider query parameters."""

    # Field names that carry a credential in some provider's payload.
    # build_payload strips these so a status attribute can never expose a
    # secret, regardless of where an uploader happens to add it. WOW-BE,
    # for instance, builds PASSWORD directly into its params.
    _CREDENTIAL_FIELDS: frozenset[str] = frozenset(
        {"PASSWORD", "password", "appid", "apikey", "api_key", "key", "token"}
    )

    def build_payload(self, data: dict[str, float]) -> dict[str, Any]:
        """Return the fields this network would send for ``data``.

        This is ``build_params`` after pruning unset values and redacting
        credentials -- a preview of what goes on the wire. The status
        entity reports the payload actually sent (recorded during
        :meth:`send` as ``last_payload``); this method is used before a
        send and by tests.
        """
        return self._redact_payload(self._prune(self.build_params(data)))

    def _redact_payload(self, params: dict[str, Any]) -> dict[str, Any]:
        """Drop credential fields so a payload is safe to expose.

        WOW-BE, for instance, builds ``PASSWORD`` into its params, so a
        status attribute must never surface the raw dict.
        """
        return {
            key: value
            for key, value in params.items()
            if key not in self._CREDENTIAL_FIELDS
        }

    @property
    def last_payload(self) -> dict[str, Any]:
        """The redacted payload actually sent on the last :meth:`send`."""
        return self._last_payload

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
        self._last_payload = self._redact_payload(params)
        try:
            async with self._session.get(
                self.url, params=params, timeout=TIMEOUT
            ) as response:
                body = (await response.text())[:_BODY_LOG_LIMIT]
                if response.status != 200:
                    self.record_error(
                        "http_error",
                        f"HTTP {response.status}: {body}",
                        status=response.status,
                    )
                    _LOGGER.warning(
                        "%s upload failed (HTTP %s): %s",
                        self.name,
                        response.status,
                        body,
                    )
                    return False
                self.clear_error()
                _LOGGER.debug("%s upload OK: %s", self.name, body)
                return True
        except aiohttp.ClientError as err:
            self.record_error(self.classify_client_error(err), str(err))
            _LOGGER.warning("%s upload error: %s", self.name, err)
            return False
        except TimeoutError as err:
            self.record_error("timeout", f"timeout: {err}")
            _LOGGER.warning("%s upload timed out", self.name)
            return False
