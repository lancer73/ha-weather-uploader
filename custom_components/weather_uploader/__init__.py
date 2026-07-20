"""The Weather Network Uploader integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_INTERVAL, CONF_SERVICES, DEFAULT_INTERVAL
from .coordinator import UploadCoordinator
from .uploaders import BaseUploader, build_uploader

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


def _build_uploaders(hass: HomeAssistant, entry: ConfigEntry) -> list[BaseUploader]:
    """Construct every configured uploader for this entry."""
    session = async_get_clientsession(hass)
    services: dict = entry.data.get(CONF_SERVICES, {})
    uploaders: list[BaseUploader] = []
    for service, config in services.items():
        uploader = build_uploader(session, service, config)
        if uploader is None:
            _LOGGER.warning("Skipping unsupported or unconfigured service: %s", service)
            continue
        uploaders.append(uploader)
    return uploaders


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Weather Network Uploader from a config entry."""
    interval = entry.options.get(
        CONF_INTERVAL, entry.data.get(CONF_INTERVAL, DEFAULT_INTERVAL)
    )
    coordinator = UploadCoordinator(
        hass, entry, _build_uploaders(hass, entry), interval
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
