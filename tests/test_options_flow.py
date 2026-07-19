"""Tests for adding and removing networks after initial configuration.

Networks and their credentials live in entry data, not options, so the
options flow edits entry data directly for add/remove (writing secrets
into options would duplicate them in storage). Adding reuses the same
credential steps as initial setup.
"""

from unittest.mock import MagicMock

from custom_components.weather_uploader.config_flow import (
    WeatherUploaderConfigFlow,
    WeatherUploaderOptionsFlow,
    _CredentialSteps,
)
from custom_components.weather_uploader.const import (
    CONF_KEY,
    CONF_SERVICES,
    CONF_STATION_ID,
    SERVICE_CWOP,
    SERVICE_PWSWEATHER,
    SERVICE_WINDY,
    SERVICE_WOW_BE,
    SERVICES,
)


def _options_flow(services):
    """Build an options flow over an entry with the given networks."""
    entry = MagicMock()
    entry.data = {CONF_SERVICES: dict(services), "interval": 60}
    entry.options = {}
    flow = WeatherUploaderOptionsFlow()
    flow.hass = MagicMock()
    flow.hass.config_entries.async_update_entry = MagicMock()
    type(flow).config_entry = property(lambda self: entry)
    return flow, entry


def test_both_flows_share_credential_steps():
    """The mixin is the single source of credential-collection logic."""
    assert _CredentialSteps in WeatherUploaderConfigFlow.__mro__
    assert _CredentialSteps in WeatherUploaderOptionsFlow.__mro__


def test_each_flow_has_its_own_terminal():
    """The credentials terminal differs: setup maps sensors, options saves."""
    assert (
        WeatherUploaderConfigFlow._credentials_done
        is not _CredentialSteps._credentials_done
    )
    assert (
        WeatherUploaderOptionsFlow._credentials_done
        is not _CredentialSteps._credentials_done
    )


async def test_init_is_a_menu():
    """The options entry point offers settings / add / remove."""
    flow, _ = _options_flow({SERVICE_WOW_BE: {"station_id": "1", "key": "p"}})
    result = await flow.async_step_init()
    assert result["type"] == "menu"
    assert set(result["menu_options"]) == {"settings", "add_network", "remove_network"}


async def test_add_network_excludes_configured():
    """Only not-yet-configured networks are offered to add."""
    flow, _ = _options_flow(
        {
            SERVICE_WOW_BE: {"station_id": "1", "key": "p"},
            SERVICE_WINDY: {"station_id": "2", "key": "p"},
        }
    )
    result = await flow.async_step_add_network()
    key = next(iter(result["data_schema"].schema))
    options = result["data_schema"].schema[key].config["options"]
    assert SERVICE_WOW_BE not in options
    assert SERVICE_WINDY not in options
    assert SERVICE_CWOP in options


async def test_add_network_persists_to_entry_data():
    """A completed add writes the new network into entry data."""
    flow, _ = _options_flow({SERVICE_WOW_BE: {"station_id": "1", "key": "p"}})
    await flow.async_step_add_network({CONF_SERVICES: [SERVICE_PWSWEATHER]})
    await flow.async_step_credentials({CONF_STATION_ID: "MYPWS", CONF_KEY: "secret"})
    data = flow.hass.config_entries.async_update_entry.call_args.kwargs["data"]
    services = data[CONF_SERVICES]
    assert SERVICE_WOW_BE in services  # existing kept
    assert services[SERVICE_PWSWEATHER]["station_id"] == "MYPWS"


async def test_remove_network_drops_from_entry_data():
    """Removing a network deletes it, and its credentials, from data."""
    flow, _ = _options_flow(
        {
            SERVICE_WOW_BE: {"station_id": "1", "key": "p"},
            SERVICE_WINDY: {"station_id": "2", "key": "p"},
        }
    )
    await flow.async_step_remove_network({CONF_SERVICES: [SERVICE_WINDY]})
    data = flow.hass.config_entries.async_update_entry.call_args.kwargs["data"]
    services = data[CONF_SERVICES]
    assert SERVICE_WINDY not in services
    assert SERVICE_WOW_BE in services


async def test_add_aborts_when_all_configured():
    """Nothing to add when every network is present."""
    flow, _ = _options_flow({s: {"station_id": "x", "key": "y"} for s in SERVICES})
    result = await flow.async_step_add_network()
    assert result["type"] == "abort"
    assert result["reason"] == "all_networks_configured"


async def test_remove_aborts_when_none_configured():
    """Nothing to remove from an empty set."""
    flow, _ = _options_flow({})
    result = await flow.async_step_remove_network()
    assert result["type"] == "abort"
    assert result["reason"] == "no_networks_configured"
