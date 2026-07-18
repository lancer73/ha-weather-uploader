"""Setup-time regression tests.

The integration once failed to load with a misleading "No setup or
config entry setup function defined" error. The real cause was that the
coordinator did not pass ``config_entry`` to its base class, which
recent Home Assistant requires before
``async_config_entry_first_refresh``. These tests exercise the setup
path so that regression cannot return silently.
"""

from unittest.mock import MagicMock

import pytest

from custom_components.weather_uploader.coordinator import UploadCoordinator


def test_coordinator_is_linked_to_config_entry():
    """The coordinator must expose its config entry to Home Assistant.

    Newer cores raise from async_config_entry_first_refresh unless the
    coordinator was constructed with config_entry set. We assert the
    link exists rather than calling the refresh, so the test does not
    depend on a running event loop.
    """
    hass = MagicMock()
    entry = MagicMock()
    entry.data = {"services": {}}
    entry.options = {}
    entry.entry_id = "abc"

    coordinator = UploadCoordinator(hass, entry, [], interval=60)

    # Base class stores it as config_entry; the integration also keeps a
    # plain alias. Both must point at the same entry.
    assert getattr(coordinator, "config_entry", None) is entry
    assert coordinator.entry is entry


@pytest.mark.parametrize(
    "services",
    [
        {"wow_be": {"station_id": "1", "key": "p"}},
        {"windy": {"station_id": "2", "key": "p"}},
        {
            "wow_be": {"station_id": "1", "key": "p"},
            "windy": {"station_id": "2", "key": "p"},
        },
    ],
)
def test_coordinator_construction_with_services(services):
    """Construction must succeed for any mix of configured networks."""
    hass = MagicMock()
    entry = MagicMock()
    entry.data = {"services": services, "interval": 60}
    entry.options = {}
    entry.entry_id = "abc"

    coordinator = UploadCoordinator(hass, entry, [], interval=60)
    assert coordinator.config_entry is entry
