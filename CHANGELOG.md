# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.8.0] - 2026-07-20

Pre-1.0: the configuration schema and entity IDs may still change before
1.0.0.

### Added

- Connection-phase timeouts are now distinguished from server-response
  timeouts. HTTP requests set a separate connect timeout, so a DNS
  resolution or TCP-handshake stall is reported as `connect_timeout`
  (CWOP reports its own connect timeout the same way), while a slow
  response after connecting is `read_timeout`. Previously both collapsed
  into a single `timeout` code. DNS resolution *failures* remain `dns`.

### Changed

- Uploads to the different networks are now staggered a few seconds
  apart instead of all firing at once, shortest-period networks first.
  Concurrent dispatch made every network resolve DNS and open a
  connection simultaneously, which on a constrained resolver could cause
  DNS timeouts -- adding CWOP made this worse, because it does an
  uncached name lookup on a raw socket every time, unlike the shared
  keep-alive HTTP session. Networks are ordered by ascending minimum
  send interval so the stagger's offset falls on the long-period
  networks, which have slack to absorb it; a tight-interval network
  (e.g. 60 s) would otherwise risk its next-cycle send landing just
  under its own floor. The spacing stays well within the minimum poll
  interval, and Home Assistant already skips a scheduled refresh if the
  previous one is still running, so a long cycle cannot pile up. A network
  whose send fails (for example a transient DNS timeout) is simply left
  until its next scheduled cycle rather than retried, which keeps every
  network's send cadence fixed.

## [0.7.0] - 2026-07-20

Pre-1.0: the configuration schema and entity IDs may still change before
1.0.0.

### Added

- A per-network error-status sensor
  (`sensor.weather_network_uploader_<network>_last_error`). Its state is
  a short, stable code -- `ok`, or a failure kind such as `timeout`,
  `dns`, `connection`, `tls`, or `http_<status>` -- so, unlike the
  binary sensor's `last_error` attribute, Home Assistant's recorder
  keeps a history of it. An intermittent failure such as a DNS timeout
  now leaves a durable, graphable trail. The full credential-redacted
  message and the time of the last error are exposed as attributes.
  Errors are classified from the exception type (for example, a DNS
  resolution failure is distinguished from a refused connection) rather
  than by matching the message text.

- CWOP's latitude and longitude fields are pre-filled from your Home
  Assistant location, rounded to ~3 decimals (~100 m) so the default
  does not publish your exact dwelling to APRS-IS. You can still enter a
  more precise value. This matches the existing OpenWeatherMap
  station-location pre-fill.

### Fixed

- A throttle test was intermittently failing due to floating-point
  rounding at an exact interval boundary (the live monotonic seed is a
  large float, and `(seed + 300) - seed` is not always exactly `300.0`).
  The test now uses a fixed base for exact arithmetic. This is a
  test-only change; the throttle behaviour was already correct.

## [0.6.0] - 2026-07-20

Pre-1.0: the configuration schema and entity IDs may still change before
1.0.0.

### Changed

- `DeviceInfo` now uses the `DeviceEntryType.SERVICE` enum instead of
  the deprecated string form, and the coordinator is stored on
  `entry.runtime_data` rather than `hass.data`, following current Home
  Assistant patterns.

### Fixed

- One sensor with an unrecognized unit no longer takes the whole
  integration down. Home Assistant's unit converters raise
  `HomeAssistantError` (whose class hierarchy is only `Exception`, not
  `ValueError`), which the converter's error handling did not catch, so
  the exception propagated out of the coordinator refresh and failed
  every update, for all networks, on every tick. The unrecognized unit
  now drops just that field with a warning.
- `rain_rate` used a length converter (mm) instead of an intensity one.
  Rain rate is mm/h (or in/h) -- a volumetric-flux/speed unit -- so a
  correctly declared rain-rate sensor hit the unrecognized-unit path and
  (via the bug above) took the integration down. It now uses the speed
  converter with mm/h.
- A sensor mapped during initial setup could not be unmapped from the
  options flow. The mapping lived in the config entry data, the options
  form wrote only non-empty keys, and the two were merged, so a cleared
  field fell back to its original value. Once the settings form is
  saved, its mapping is now authoritative, so clearing a field removes
  it.
- CWOP no longer asks for an authentication key. It uses the fixed
  public passcode -1 and never reads a key, so the field forced users to
  invent an unused value.
- CWOP is now skipped with a warning, rather than reporting from (0, 0),
  when a config entry has no coordinates -- which a pre-coordinate entry
  surviving an upgrade could otherwise trigger silently.
- `last_payload` now records the payload actually sent during the
  upload, rather than rebuilding it afterward (which recomputed
  timestamps and, for CWOP, the packet, so the attribute differed
  slightly from what went on the wire).
- The upload key is redacted from `last_error`, in case an error string
  (for example an invalid-URL error) embeds a URL carrying the key as a
  query parameter, since `last_error` is exposed as an entity attribute.
- The credentials step no longer claims all credentials are sent "over
  TLS." That is true for the HTTP networks but false for CWOP, which
  carries no secret and connects over plaintext APRS-IS.
- Removed a duplicate `_credentials_done` definition in the shared
  credential-steps mixin, where the second silently shadowed an abstract
  guard.
- The per-network upload status sensor's `last_payload` and
  `sensors_published` attributes now show what that network actually
  sent -- its own provider-specific field subset -- rather than the full
  set of mapped readings shared across every network. CWOP's status
  sensor, for example, now shows its single packet field instead of all
  mapped sensors. As part of this, `build_payload()` strips any
  credential field before exposing it, so a provider that builds its
  password into the request params (WOW-BE) cannot leak it into the
  states API.

- The `sensors_published` count on the status sensor now reports the
  number of weather measurements a network sent, counted consistently
  across networks, rather than the number of fields in the request.
  CWOP previously showed `1` because it packs every measurement into a
  single APRS packet; it now reports the measurements in that packet.
  Each uploader declares its accepted readings (`SUPPORTED_READINGS`),
  so the figure excludes request metadata such as timestamps and
  station identifiers.

## [0.5.0] - 2026-07-19

Pre-1.0: the configuration schema and entity IDs may still change before
1.0.0.

### Removed

- The Meteo-Services (`meteo_services`) and Wetternetzwerk.pro
  (`wetternetzwerk`) uploaders. Both were unverifiable: Meteo-Services
  registration was not obtainable, and Wetternetzwerk.pro is
  Germany-only, so neither could be tested against a live account.
  Shipping an untestable uploader is worse than not shipping it. The
  unauthenticated-credential config step (`credentials_open`) that only
  Meteo-Services used was removed with it. An existing entry that had
  one of these configured simply stops uploading to it after upgrade;
  remove it from the entry to clear the stored config.

### Added

- The OpenWeatherMap station-creation form now pre-fills the location
  name, coordinates, and altitude from Home Assistant's own configured
  location, so you no longer have to retype what HA already knows. The
  fields stay editable, so a rounded or different location can still be
  published.

### Fixed

- Restarting Home Assistant no longer triggers an immediate extra upload
  that could trip a provider's rate limit. Uploaders are rebuilt on
  restart, and their per-network throttle was starting empty, so the
  first poll uploaded regardless of when the previous send happened --
  Windy returned 429 when a restart fell inside its 5-minute window. The
  throttle is now seeded at construction, so each network waits its own
  minimum before the first send after start. The first upload after a
  restart is delayed by up to that minimum in exchange.

- Corrected the README's characterisation of how each network uses
  contributed data. An earlier claim that CWOP was the only network with
  scientific use was wrong (WOW-BE, the KMI/KNMI network, also feeds the
  KNMI app and research per KNMI); the subsequent claim that the other
  networks are "community and hobbyist" platforms was also wrong.
  Weather Underground's PWS data is sold through The Weather Company
  (IBM) enterprise packages, and Windy and OpenWeatherMap run paid
  professional API tiers over their data (OpenWeatherMap markets
  agriculture, logistics, insurance, and forestry use). The note now
  describes a spectrum -- national weather services, commercial data
  platforms, and community/regional networks -- rather than labelling
  networks. Also corrected in the CWOP uploader docstring and the
  earlier changelog note.

## [0.4.0] - 2026-07-18

Pre-1.0: the configuration schema and entity IDs may still change before
1.0.0.

### Added

- Networks can be added or removed after initial setup, from the
  integration's Configure dialog, without deleting and re-adding the
  entry. Adding reuses the same credential steps as initial setup
  (including OpenWeatherMap station creation) and writes the new network
  into the config entry; removing deletes a network and its stored
  credentials. Either change reloads the entry so uploads start or stop
  accordingly. Sensor mappings are shared across networks and left
  untouched. Credentials remain in entry data only -- they are never
  duplicated into options -- so the credential steps are shared between
  the config and options flows via a mixin rather than reimplemented.

### Fixed

- Config flow crashed in the interface (with nothing in the log) when
  advancing past a step that asks for coordinates — the OpenWeatherMap
  station step, and the CWOP and Meteo-Services credential steps. The
  latitude/longitude selector used `step=0.0001`, which Home Assistant's
  number selector rejects; it now uses `step="any"` for arbitrary
  decimal precision.
- Async tests were silently skipped rather than run. `pytest-asyncio`
  needs `asyncio_default_fixture_loop_scope` set to claim them under the
  Home Assistant test environment; it is now set in `pyproject.toml`.
  The full suite runs and passes.

## [0.3.0] - 2026-07-18

Pre-1.0: the configuration schema and entity IDs may still change before
1.0.0.

### Added

- OpenWeatherMap station creation in the config flow. OpenWeatherMap has
  no website signup for stations: the measurement `station_id` is an
  internal identifier that only exists once a station has been created
  through the API. Setup now asks for the API key and either creates a
  station (`POST /data/3.0/stations`, storing the returned internal ID)
  or accepts an existing internal ID. Creation is idempotent: an
  existing station with the same `external_id` is reused rather than
  duplicated, so re-adding the integration does not create copies. API
  failures (bad key, connectivity, rejected creation) are shown on the
  form rather than surfacing later as failed uploads. Implemented in
  `uploaders/owm_station.py`; verified against the Weather Stations API
  3.0 documentation.

### Changed

- Minimum Home Assistant version raised to 2024.8.0, the release that
  added the `config_entry` parameter to `DataUpdateCoordinator`.
- The options flow no longer defines a custom `__init__`. Modern
  `OptionsFlow` provides `config_entry` as a managed property, and an
  `__init__` that does not chain to the base class can interfere with
  it; per-flow state is now initialised lazily.

### Fixed

- Setup failure on recent Home Assistant. The update coordinator was not
  constructed with its `config_entry`, which newer cores require before
  `async_config_entry_first_refresh`. On those versions setup raised and
  Home Assistant reported the misleading "No setup or config entry setup
  function defined". The coordinator now passes `config_entry` to its
  base class. This was a latent bug independent of which networks were
  configured.

## [0.2.0] - 2026-07-18

Pre-1.0: the configuration schema and entity IDs may still change before
1.0.0.

### Added

- Sensor mapping validation, in three non-blocking layers. At
  configuration time, a mapping whose source entity has a mismatched
  `device_class` (for example a humidity sensor on a temperature field),
  or which declares no unit, triggers a confirmation step listing the
  concerns; a clean mapping still saves in one step. At runtime, each
  converted reading is bounds-checked against a wide sane range per
  field (`PLAUSIBLE_RANGE` in `const.py`), so a mis-unit — the classic
  being Pascals where hectopascals are expected — is dropped and
  surfaced rather than published. None of these block a mapping: many
  legitimate DIY and template sensors lack a device_class or units.
- `implausible_sensors` attribute on the source-data problem binary
  sensor, naming any field whose value fell outside its plausible range.
- `solarradiation` is now sent to Windy, which the v2 endpoint accepts.

### Changed

- Windy uploads migrated from the legacy `POST /pws/update/{key}`
  endpoint to the current `GET /api/v2/observation/update` (Windy
  Stations API v2). Windy's documentation states the legacy API is
  unsupported as of January 2026. The new endpoint is Weather
  Underground compatible: values travel as query parameters and the
  station password as the `PASSWORD` query field. Pressure is sent via
  `mbar` (hectopascals) rather than converted to Pascals. Windy's
  documented failure codes (400, 401, 409, 429 with `retry_after`) are
  handled with specific messages. This is a breaking change for Windy
  users only if Windy had already disabled the legacy endpoint for
  their station; the configuration is unchanged.

### Fixed

- OpenWeatherMap uploads used the wrong endpoint and units. The send
  path is `POST /data/3.0/measurements`, not
  `/data/3.0/stations/measurements` (which returns 404), and the
  endpoint takes metric units — Celsius, hectopascals, m/s, mm, km —
  not the Kelvin and Pascals that OpenWeatherMap's read endpoints
  default to. Temperature and dew point were being sent in Kelvin and
  pressure in Pascals; all three are now correct. Success is HTTP 204
  (200 and 201 are the update- and create-station codes and are no
  longer accepted as success).

### Verified against live services

Verified against https://openweathermap.org/api/stations
(Weather Stations API 3.0):

- The OpenWeatherMap send endpoint is `POST /data/3.0/measurements`,
  not `/data/3.0/stations/measurements` (which 404s), and it takes
  **metric** units -- Celsius, hectopascals, m/s, mm, km -- not the
  Kelvin and Pascals that OpenWeatherMap's read endpoints default to.
  The documented request example (`temperature: 18.7`, `pressure:
  1021`) confirms this. The 0.1.0 uploader had the wrong path and sent
  Kelvin and Pascals; both are fixed in this release. Success
  is HTTP 204 (200/201 are the update/create-station codes and are not
  accepted); the `Content-Type: application/json` header the spec
  requires for POST is sent; `appid` carries the key. Every field sent
  (`station_id`, `dt`, `temperature`, `dew_point`, `humidity`,
  `pressure`, `wind_speed`, `wind_gust`, `wind_deg`, `rain_1h`,
  `rain_24h`, `visibility_distance`) is in the documented parameter
  table.

Also verified against https://stations.windy.com/api-reference (Windy
Stations API v2):

- The uploader was migrated off the legacy
  `POST /pws/update/{key}` endpoint, which Windy's documentation now
  states is unsupported (the new API is effective January 2026). It
  targets `GET /api/v2/observation/update`, a Weather Underground
  compatible endpoint that takes query parameters and the station
  password as the `PASSWORD` query field.
- Pressure is now sent via `mbar` (hectopascals) rather than converted
  to Pascals, and `solarradiation` is now included. Every parameter
  sent is in the documented v2 list.
- Windy's `MIN_SERVICE_INTERVAL` of 300 s is confirmed by the
  documentation ("at most once every 5 minutes"), not a guess.
- The documented failure codes (400 bad password/payload, 401 missing
  password, 409 duplicate, 429 rate limited with `retry_after`) are
  handled with specific messages.

## [0.1.0] - 2026-07-18

First release. Pre-1.0: the configuration schema and entity IDs may
still change before 1.0.0.

### Added

- Home Assistant custom integration that reads mapped sensor entities on
  an interval and publishes to multiple weather networks in parallel.
- Uploaders for eight networks: WOW-BE (RMI/KMI Belgium), Weather
  Underground, CWOP/NOAA, PWSWeather, Windy, OpenWeatherMap,
  Wetternetzwerk.pro, and Meteo-Services.
- CWOP support over **native APRS-IS** (`cwop.aprs.net:14580`) rather
  than an HTTP bridge. The packet builder is a pure function and is
  tested byte-for-byte against the worked example in NOAA's own FAQ.
  CWOP feeds NOAA's MADIS and the National Weather Service; along with
  WOW-BE (KMI/KNMI), it is one of the supported networks put to
  operational and scientific use by a national weather service.
- `rain_24h` sensor key, added because MADIS ingests only hourly and
  24-hour rainfall from CWOP packets.
- Station latitude, longitude, and altitude configuration, collected
  only when a network that needs them (CWOP, Meteo-Services) is
  selected.
- Config flow in three steps: network selection and interval,
  per-network credentials, and sensor mapping.
- Options flow for changing the upload interval and re-mapping sensors
  without removing the integration. Credentials are deliberately
  excluded; see Security.
- Thirty mappable sensor fields covering temperature, dewpoint,
  humidity, absolute and relative pressure, wind speed, gust, direction
  and gust direction, rain rate and five accumulation windows, solar
  radiation, UV index, illuminance, indoor temperature and humidity,
  soil temperature and moisture, leaf wetness, PM2.5, PM10, CO2,
  lightning count and distance, visibility, and cloud base.
- Automatic unit conversion from each entity's declared
  `unit_of_measurement` using Home Assistant's own converters, so
  imperial and metric source entities are both handled.
- One connectivity `binary_sensor` per configured network, exposing
  `last_error`, `sensors_published`, and `last_payload`.
- A `problem` binary sensor for source data health, exposing
  `stale_sensors`, `missing_sensors`, `sensors_published`, and the
  active `max_sensor_age`. Upload success and data quality are separate
  failures and now have separate entities: a station that stops
  reporting produces perfectly successful uploads of stale readings.
- Sensor staleness rejection, keyed on `State.last_reported` rather than
  `State.last_updated`. Readings whose entity has not reported within
  `max_sensor_age` (default 3600 s, configurable in the options flow, 0
  to disable) are dropped rather than published. Home Assistant retains
  the last value of a sensor that silently stops reporting, so without
  this a station with a dead battery would have its final reading
  republished as a current observation indefinitely, while every upload
  succeeded.

  The choice of timestamp is load-bearing. Home Assistant's state
  machine discards a write when neither state nor attributes changed --
  it refreshes `last_reported` and leaves `last_updated` untouched. For
  weather data a constant value is the normal case: rain sits at 0.0 for
  days, solar radiation and UV sit at 0.0 every night. Keying on
  `last_updated` would drop rain from nearly every payload and drop
  solar and UV nightly, while still failing to detect a dead station,
  because a healthy dry rain sensor and a dead station have identical
  `last_updated` values. Only `last_reported` distinguishes them.
- Parallel dispatch per cycle: one failing network does not block the
  others.
- Per-network send throttling. The configured interval is a sensor
  polling cadence; each network additionally gates itself against its
  own minimum interval (`MIN_SERVICE_INTERVAL`), so a fast poll cannot
  trip a slow provider's rate limit. Throttling is keyed on attempt
  rather than success, since a failed request still consumes the
  provider's budget, and uses a monotonic clock so a system time change
  cannot stall an uploader.
- Integration icon, shipped in `custom_components/weather_uploader/brand/`
  as `icon.png` (256x256) and `icon@2x.png` (512x512). Home Assistant
  2026.3 and later serve brand images from a custom integration's own
  directory, so no submission to the `home-assistant/brands` repository
  is required. The SVG source lives in `brand_src/icon.svg` and is
  rendered by `tools/render_brand.py`.

### Security

- Stale readings are never published. A weather station that stops
  reporting is indistinguishable from one reporting an unchanging value
  by state alone; only `last_reported` separates them. For CWOP this
  matters beyond correctness, since its observations reach NOAA's MADIS
  and National Weather Service forecasters.
- CWOP is implemented over native APRS rather than through a public
  HTTP-to-APRS bridge. A bridge would see every observation, the station
  ID, and the station's exact coordinates, while holding no credential
  on the user's behalf: CWOP non-ham stations authenticate with the
  fixed passcode `-1`, so a bridge saves only a TCP socket. Home
  Assistant can open one natively, so the privacy cost buys nothing.
- Meteo-Services has no authentication -- station ID only. It is
  implemented anyway, unlike WOW-BE's equally unauthenticated Ecowitt
  protocol, because Ecowitt had an authenticated alternative on the same
  network and Meteo-Services has none: the choice is these terms or no
  participation. The config flow uses a dedicated step that states the
  trade before any details are entered, and shows no password field,
  since one would imply a secret exists.
- Station coordinates are requested only when CWOP or Meteo-Services is
  selected, and the form text notes that both networks publish station
  positions and recommends rounding to ~3 decimals (about 100 m).
- Request parameters are never logged. Response bodies are truncated to
  200 characters before being written to the log.
- Credentials are excluded from the options flow. Home Assistant stores
  options separately from entry data, so a credential editable there
  would be written to `.storage` twice. Rotation is remove-and-re-add.
- The `last_payload` attribute contains sensor values only. Credentials
  are injected inside each uploader after the payload dict is built, so
  they cannot leak into entity attributes or the state machine.
- The credential field applies no length or charset validation, so long
  keys are not silently rejected or truncated.
- WOW-BE's Ecowitt protocol is deliberately **not** implemented. It has
  no authentication: the station is identified by an MD5 of its MAC
  address. A MAC is not a secret — it is broadcast on the local network,
  is 48 bits of which the top 24 are a public IEEE vendor OUI, and the
  residual space falls to unsalted MD5 in well under a second. Anyone
  who learned a station's MAC could publish as that station, with no key
  to rotate. The endpoint's response list corroborates this: 200, 422,
  and 429, with no 403, because there is no credential to reject.
- Met Office WOW (UK) is not supported. The Met Office began retiring
  WOW in January 2026 with full decommissioning planned for late 2026,
  and does not permit migration to third parties.
- README documents location disclosure, cleartext credential storage in
  `.storage`, credential exposure in provider access logs, WOW-BE
  credential strength, and the occupancy-inference risk of publishing
  indoor and CO2 readings.

### Verified against live services

Confirmed on 2026-07-16 against the WOW-BE OpenAPI 3.1 spec
(<https://wow.meteo.be/docs/api/>), the AGPL-3.0 server source
(<https://github.com/rmibelgium/wowbe>), and the live endpoint:

- WOW-BE uses `POST /api/v2/send/weatherunderground` with a JSON body.
  The Weather Underground protocol is chosen over WOW-BE's own WOW
  protocol because it accepts one additional measurement field (`UV`)
  with identical authentication and an identical `403` response.
- Authentication is the `PASSWORD` body field, documented as
  "Authentication Key (PIN code or Password)". One field, both
  credential styles, no mode to select. The spec declares no
  `securitySchemes`.
- `dateutc` must match `Y-m-d H:i:s` and is UTC. The server rejects an
  ISO-8601 offset form explicitly; the spec's `format: date-time` is
  misleading.
- `rainin` is an instantaneous rain **rate** in inches/hour, fed from
  `rain_rate`. The legacy WOW-UK protocol used the same name for an
  hourly accumulation.
- `visibility` is kilometres.
- `ID` accepts either the short station ID or the long UUID. The
  server's validation error names whichever form it detected, so an
  earlier reading of "must be a valid site short ID" reflected the input
  shape rather than an exclusive requirement.
- The endpoint is Laravel and merges query parameters into input, so it
  accepts credentials in a GET query string as well as a JSON body.
  This integration uses the body form deliberately; keeping the
  credential out of URLs is our choice, not an API guarantee.
- `baromin` (relative) is authoritative over `absbaromin` (absolute):
  when both are sent the server discards `absbaromin`, and when only
  `absbaromin` is sent it derives the relative value from the registered
  altitude. The uploader therefore sends exactly one.
- Rate limits are 20 requests/minute/site and 600/minute/IP.

### Known issues

- The per-network minimum intervals for PWSWeather, OpenWeatherMap,
  Weather Underground, Wetternetzwerk.pro, and Meteo-Services are
  conservative defaults, not verified against published limits. Only
  WOW-BE's (RMI: 60 s, 20/min/site), CWOP's (NOAA: 5 min), and Windy's
  (~5 min) come from documentation.
- No CWOP packet has been delivered end-to-end, and the APRS socket path
  is untested against a live server. The packet *format* is verified
  byte-for-byte against NOAA's documented example, and the connect /
  login / send / disconnect sequence follows the FAQ exactly, but the
  two are independent: a correct packet does not prove correct I/O.
  Testing needs a registered callsign, since sending from a fake one
  would inject junk into a NOAA-facing network.
- Meteo-Services returns HTTP 200 with an empty body regardless of
  outcome, so its status entity can only report transport success, not
  that an observation was stored.
- Meteo-Services' API accepts evapotranspiration and humidex. Neither is
  sent: deriving them well needs inputs this integration does not
  collect, and a plausible-looking wrong value is worse than an absent
  one.
- OpenWeatherMap station registration is not implemented. Create the
  station via the OpenWeatherMap API and supply the resulting station id
  manually.
- The test suite has not been executed. Importing the integration package
  pulls in Home Assistant, so `pytest` requires
  `pytest-homeassistant-custom-component` in a dev environment. Uploader
  logic has been verified independently, but the coordinator has no test
  coverage: unit conversion, missing entities, non-numeric states, and
  the parallel dispatch are untested.
- `illuminance`, `co2`, `lightning_count`, `lightning_distance`, and
  `cloud_base` are collected and normalized but no supported network has
  a parameter for them. They appear in `last_payload` only.

[Unreleased]: https://github.com/lancer73/ha-weather-uploader/compare/v0.8.0...HEAD
[0.8.0]: https://github.com/lancer73/ha-weather-uploader/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/lancer73/ha-weather-uploader/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/lancer73/ha-weather-uploader/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/lancer73/ha-weather-uploader/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/lancer73/ha-weather-uploader/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/lancer73/ha-weather-uploader/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/lancer73/ha-weather-uploader/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/lancer73/ha-weather-uploader/releases/tag/v0.1.0
