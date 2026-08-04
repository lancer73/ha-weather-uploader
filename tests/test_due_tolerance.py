"""A network whose min_interval equals the poll interval must fire every
tick, not every other one.

Sends are dispatched a moment after the poll tick (stagger + latency), so
last_sent lands just after the tick and a naive ``elapsed >= min_interval``
check reads as not-due by that fraction on every tick. The due-check
tolerance forgives the dispatch offset without letting a network send
faster than its rate limit.
"""

import time
from datetime import timedelta
from unittest.mock import MagicMock

from custom_components.weather_uploader.coordinator import UploadCoordinator
from custom_components.weather_uploader.uploaders import build_uploader


def _coordinator(min_intervals, poll_seconds):
    c = UploadCoordinator.__new__(UploadCoordinator)
    ups = []
    for mi in min_intervals:
        u = MagicMock()
        u.min_interval = mi
        ups.append(u)
    c.uploaders = ups
    c._update_interval = timedelta(seconds=poll_seconds)
    return c


def test_tolerance_covers_dispatch_window():
    # 2 throttled networks, 5s stagger, 2s margin -> (2-1)*5+2 = 7
    assert _coordinator([60, 60], 60)._due_tolerance() == 7


def test_tolerance_capped_at_half_poll_interval():
    # 6 networks -> window 27, but a 30s poll caps it at 15
    assert _coordinator([60] * 6, 30)._due_tolerance() == 15


def test_tolerance_zero_without_throttled_networks():
    assert _coordinator([0, 0], 60)._due_tolerance() == 0.0


def test_network_at_poll_interval_is_due_just_under():
    """The core fix: 59.7s elapsed on a 60s floor is due with tolerance."""
    up = build_uploader(None, "wow_be", {"station_id": "s", "key": "k"})
    up.min_interval = 60
    up.last_sent = time.monotonic() - 59.7
    assert up.is_due() is False  # naive check skips it
    assert up.is_due(tolerance=7) is True  # tolerance fixes it


def test_tolerance_does_not_forgive_a_real_early_send():
    """Tolerance corrects sub-window offset only, not a genuine early send."""
    up = build_uploader(None, "wow_be", {"station_id": "s", "key": "k"})
    up.min_interval = 60
    up.last_sent = time.monotonic() - 30  # only half the interval elapsed
    assert up.is_due(tolerance=7) is False
