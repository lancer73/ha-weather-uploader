"""Config and options flow for the Weather Network Uploader."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_ALTITUDE,
    CONF_INTERVAL,
    CONF_KEY,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_MAX_SENSOR_AGE,
    CONF_SERVICES,
    CONF_STATION_ID,
    DEFAULT_INTERVAL,
    DEFAULT_MAX_SENSOR_AGE,
    DOMAIN,
    EXPECTED_DEVICE_CLASS,
    GEO_SERVICES,
    MIN_INTERVAL,
    SENSOR_KEYS,
    SERVICE_METEO_SERVICES,
    SERVICE_OPENWEATHERMAP,
    SERVICES,
    UNAUTHENTICATED_SERVICES,
)
from .uploaders.owm_station import StationError, create_station

_LOGGER = logging.getLogger(__name__)


def _sensor_selector() -> selector.EntitySelector:
    """Build an entity selector limited to sensor domains."""
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=["sensor", "input_number", "number"])
    )


def _max_age_selector() -> selector.NumberSelector:
    """Build the staleness threshold field.

    Zero disables the check entirely, which is offered but not
    recommended: without it a station that stops reporting has its last
    reading republished as a current observation indefinitely.
    """
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0,
            max=86400,
            step=60,
            unit_of_measurement="s",
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _coordinate_selector(low: float, high: float) -> selector.NumberSelector:
    """Build a decimal-degrees coordinate field.

    Six decimal places is roughly 0.1 m. That is far more precision
    than any weather network needs and pinpoints a dwelling; the form
    text tells the user to round.
    """
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=low,
            max=high,
            step=0.0001,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _password_selector() -> selector.TextSelector:
    """Build a masked free-form text field.

    Deliberately unvalidated. Providers differ: WOW-BE documents the
    field as "PIN code or Password" and the Met Office no longer
    restricts it to a 6-digit PIN. Any length or charset check here
    would reject valid credentials.
    """
    return selector.TextSelector(
        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
    )


def _mapping_warnings(hass, mapping: dict[str, Any]) -> list[str]:
    """Return human-readable warnings for a proposed sensor mapping.

    Two non-blocking checks:

    - the mapped entity's device_class does not match what the field
      expects (e.g. a humidity sensor mapped to a temperature field), and
    - the mapped entity declares no unit_of_measurement, so runtime unit
      conversion cannot verify or correct it and must assume the value is
      already in the field's internal unit.

    Both are advisory. Many valid weather sensors -- templates, ESPHome,
    DIY hardware -- omit device_class or units, so these are surfaced for
    confirmation, never enforced.
    """
    warnings: list[str] = []
    for key in SENSOR_KEYS:
        entity_id = mapping.get(key)
        if not entity_id:
            continue
        state = hass.states.get(entity_id)
        if state is None:
            # A freshly picked entity should exist; if not, the runtime
            # missing-sensor path will report it. Nothing to warn here.
            continue

        expected = EXPECTED_DEVICE_CLASS.get(key)
        actual = state.attributes.get(ATTR_DEVICE_CLASS)
        if expected and actual and actual != expected:
            warnings.append(
                f"{key}: mapped to {entity_id}, which is a "
                f"'{actual}' sensor, not '{expected}'"
            )

        if state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) is None:
            warnings.append(
                f"{key}: {entity_id} declares no unit, so its value is "
                f"assumed to already be in the expected unit"
            )
    return warnings


def _sensor_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Build the sensor mapping schema, pre-filled from defaults."""
    defaults = defaults or {}
    schema: dict[Any, Any] = {}
    for key in SENSOR_KEYS:
        if defaults.get(key):
            schema[vol.Optional(key, default=defaults[key])] = _sensor_selector()
        else:
            schema[vol.Optional(key)] = _sensor_selector()
    return vol.Schema(schema)


class WeatherUploaderConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle initial setup."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise flow state."""
        self._services: dict[str, dict[str, Any]] = {}
        self._pending: list[str] = []
        self._interval: int = DEFAULT_INTERVAL
        self._mapping: dict[str, Any] = {}
        self._mapping_warnings: list[str] = []
        self._owm_key: str = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return WeatherUploaderOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which networks to upload to."""
        if user_input is not None:
            self._pending = list(user_input[CONF_SERVICES])
            self._interval = user_input[CONF_INTERVAL]
            return await self.async_step_credentials()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SERVICES): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=SERVICES,
                            multiple=True,
                            translation_key="services",
                        )
                    ),
                    vol.Required(
                        CONF_INTERVAL, default=DEFAULT_INTERVAL
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=MIN_INTERVAL,
                            max=3600,
                            step=30,
                            unit_of_measurement="s",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect credentials for each selected network in turn."""
        if not self._pending:
            return await self.async_step_sensors()

        service = self._pending[0]

        # OpenWeatherMap has no dashboard signup: the measurement
        # station_id only exists once a station is created through the
        # API. Route it to a dedicated step that can do that.
        if service == SERVICE_OPENWEATHERMAP:
            return await self.async_step_owm_station()

        if user_input is not None:
            self._services[service] = user_input
            self._pending.pop(0)
            return await self.async_step_credentials()

        schema: dict[Any, Any] = {vol.Required(CONF_STATION_ID): str}

        # No password field for networks that have no credential: showing
        # one would imply a secret protects the station when none does.
        if service not in UNAUTHENTICATED_SERVICES:
            schema[vol.Required(CONF_KEY)] = _password_selector()

        if service in GEO_SERVICES:
            schema[vol.Required(CONF_LATITUDE)] = _coordinate_selector(-90, 90)
            schema[vol.Required(CONF_LONGITUDE)] = _coordinate_selector(-180, 180)
            if service == SERVICE_METEO_SERVICES:
                schema[vol.Required(CONF_ALTITUDE, default=0.0)] = (
                    selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=-500,
                            max=9000,
                            step=0.1,
                            unit_of_measurement="m",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    )
                )

        step = (
            "credentials_open" if service in UNAUTHENTICATED_SERVICES else "credentials"
        )
        return self.async_show_form(
            step_id=step,
            data_schema=vol.Schema(schema),
            description_placeholders={"service": service},
        )

    async def async_step_owm_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the OpenWeatherMap key and choose how to get the ID.

        The user either supplies an existing internal station ID, or
        asks the integration to create a station through the API and
        use the ID it returns.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            self._owm_key = user_input[CONF_KEY]
            if user_input["mode"] == "existing":
                return await self.async_step_owm_existing()
            return await self.async_step_owm_create()

        schema = {
            vol.Required(CONF_KEY): _password_selector(),
            vol.Required("mode", default="create"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["create", "existing"],
                    translation_key="owm_mode",
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
        }
        return self.async_show_form(
            step_id="owm_station",
            data_schema=vol.Schema(schema),
            errors=errors,
        )

    async def async_step_owm_existing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Accept an internal station ID the user already has."""
        if user_input is not None:
            self._services[SERVICE_OPENWEATHERMAP] = {
                CONF_STATION_ID: user_input[CONF_STATION_ID],
                CONF_KEY: self._owm_key,
            }
            self._pending.pop(0)
            return await self.async_step_credentials()

        return self.async_show_form(
            step_id="owm_existing",
            data_schema=vol.Schema({vol.Required(CONF_STATION_ID): str}),
        )

    async def async_step_owm_create(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create a station via the API and store the returned ID.

        On any API failure the form is redisplayed with an error rather
        than advancing, so a bad key or a connectivity problem is
        visible immediately instead of surfacing as failed uploads
        later.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            try:
                station_id = await create_station(
                    session,
                    self._owm_key,
                    external_id=user_input["external_id"],
                    name=user_input["name"],
                    latitude=user_input[CONF_LATITUDE],
                    longitude=user_input[CONF_LONGITUDE],
                    altitude=user_input[CONF_ALTITUDE],
                )
            except StationError as err:
                errors["base"] = err.reason
                _LOGGER.warning("OpenWeatherMap station setup failed: %s", err)
            else:
                self._services[SERVICE_OPENWEATHERMAP] = {
                    CONF_STATION_ID: station_id,
                    CONF_KEY: self._owm_key,
                }
                self._pending.pop(0)
                return await self.async_step_credentials()

        schema = {
            vol.Required("external_id", default="ha_weather_uploader"): str,
            vol.Required("name", default="Home Assistant"): str,
            vol.Required(CONF_LATITUDE): _coordinate_selector(-90, 90),
            vol.Required(CONF_LONGITUDE): _coordinate_selector(-180, 180),
            vol.Required(CONF_ALTITUDE, default=0.0): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-500,
                    max=9000,
                    step=1,
                    unit_of_measurement="m",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }
        return self.async_show_form(
            step_id="owm_create",
            data_schema=vol.Schema(schema),
            errors=errors,
        )

    async def async_step_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Map Home Assistant entities onto weather fields."""
        if user_input is not None:
            self._mapping = user_input
            warnings = _mapping_warnings(self.hass, user_input)
            if warnings:
                # Something looks off, but the user is allowed to proceed.
                # Show the warnings on a confirm step rather than blocking.
                self._mapping_warnings = warnings
                return await self.async_step_confirm_mapping()
            return self._finish_entry(user_input)
        return self.async_show_form(step_id="sensors", data_schema=_sensor_schema())

    async def async_step_confirm_mapping(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show non-blocking mapping warnings and let the user proceed."""
        if user_input is not None:
            return self._finish_entry(self._mapping)
        return self.async_show_form(
            step_id="confirm_mapping",
            data_schema=vol.Schema({}),
            description_placeholders={"warnings": "\n".join(self._mapping_warnings)},
        )

    def _finish_entry(self, mapping: dict[str, Any]) -> ConfigFlowResult:
        """Create the config entry from the collected mapping."""
        return self.async_create_entry(
            title="Weather Network Uploader",
            data={
                CONF_SERVICES: self._services,
                CONF_INTERVAL: int(self._interval),
                CONF_MAX_SENSOR_AGE: DEFAULT_MAX_SENSOR_AGE,
                **mapping,
            },
        )


class WeatherUploaderOptionsFlow(OptionsFlow):
    """Handle re-mapping sensors and changing the interval.

    Credentials are intentionally not editable here. Options are stored
    separately from entry data, so exposing secrets in this form would
    write them to .storage twice. Remove and re-add the entry to rotate
    a key.
    """

    # No custom __init__: modern OptionsFlow exposes config_entry as a
    # framework-managed property, and defining an __init__ that does not
    # chain to super() skips that setup. Instance state is initialised
    # lazily via getattr where it is used.

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the mapping and interval form."""
        if user_input is not None:
            cleaned = {k: v for k, v in user_input.items() if v not in (None, "")}
            cleaned[CONF_INTERVAL] = int(cleaned.get(CONF_INTERVAL, DEFAULT_INTERVAL))
            cleaned[CONF_MAX_SENSOR_AGE] = int(
                cleaned.get(CONF_MAX_SENSOR_AGE, DEFAULT_MAX_SENSOR_AGE)
            )
            mapping = {k: v for k, v in cleaned.items() if k in SENSOR_KEYS}
            warnings = _mapping_warnings(self.hass, mapping)
            if warnings:
                self._pending_options = cleaned
                self._mapping_warnings = warnings
                return await self.async_step_confirm_mapping()
            return self.async_create_entry(title="", data=cleaned)

        current = {**self.config_entry.data, **self.config_entry.options}
        schema: dict[Any, Any] = {
            vol.Required(
                CONF_INTERVAL,
                default=current.get(CONF_INTERVAL, DEFAULT_INTERVAL),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_INTERVAL,
                    max=3600,
                    step=30,
                    unit_of_measurement="s",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_MAX_SENSOR_AGE,
                default=current.get(CONF_MAX_SENSOR_AGE, DEFAULT_MAX_SENSOR_AGE),
            ): _max_age_selector(),
        }
        schema.update(_sensor_schema(current).schema)
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))

    async def async_step_confirm_mapping(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show non-blocking mapping warnings and let the user proceed."""
        if user_input is not None:
            return self.async_create_entry(
                title="", data=getattr(self, "_pending_options", {})
            )
        return self.async_show_form(
            step_id="confirm_mapping",
            data_schema=vol.Schema({}),
            description_placeholders={
                "warnings": "\n".join(getattr(self, "_mapping_warnings", []))
            },
        )
