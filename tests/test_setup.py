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


# --- Config-flow selector validity --------------------------------------

# The config form once crashed in the UI (with nothing in the Python log)
# because a NumberSelector was built with step=0.0001, which Home
# Assistant rejects. These tests build every selector the flow uses, so a
# bad selector config fails here instead of in the live interface.


def test_coordinate_selector_builds():
    """Latitude/longitude selectors must construct without raising."""
    from custom_components.weather_uploader.config_flow import _coordinate_selector

    # Both the latitude and longitude ranges we use in the flow.
    assert _coordinate_selector(-90, 90) is not None
    assert _coordinate_selector(-180, 180) is not None


def test_all_flow_number_selectors_build():
    """Every NumberSelector config in the flow must be valid.

    Home Assistant validates step against the range; an over-fine step
    (0.0001) is rejected. Building each selector here catches that.
    """
    from homeassistant.helpers import selector

    from custom_components.weather_uploader.config_flow import (
        _coordinate_selector,
        _max_age_selector,
    )

    # Coordinate selectors.
    _coordinate_selector(-90, 90)
    _coordinate_selector(-180, 180)
    # Staleness selector.
    _max_age_selector()
    # Interval and altitude selectors are inline; reproduce their configs.
    selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=30,
            max=3600,
            step=30,
            unit_of_measurement="s",
            mode=selector.NumberSelectorMode.BOX,
        )
    )
    selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=-500,
            max=9000,
            step=1,
            unit_of_measurement="m",
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def test_owm_mode_selector_builds():
    """The create/existing mode toggle must construct."""
    from homeassistant.helpers import selector

    assert selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=["create", "existing"],
            translation_key="owm_mode",
            mode=selector.SelectSelectorMode.LIST,
        )
    )
