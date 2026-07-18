# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
  CWOP is the only supported network with direct scientific use: it
  feeds NOAA's MADIS and the National Weather Service.
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

[Unreleased]: https://github.com/lancer73/ha-weather-uploader/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/lancer73/ha-weather-uploader/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/lancer73/ha-weather-uploader/releases/tag/v0.1.0
