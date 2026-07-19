# Home Assistant Weather Network Uploader

<img src="custom_components/weather_uploader/brand/icon.png" width="96" align="right" alt="">

Publish your weather station's sensor readings from Home Assistant to
multiple public weather networks at once.

Supported networks:

| Network | Endpoint | Transport | Credential | Units sent |
| --- | --- | --- | --- | --- |
| **WOW-BE** (RMI/KMI, Belgium) | `wow.meteo.be/api/v2/send/weatherunderground` | POST JSON | Site authentication key | Imperial + km |
| Weather Underground | `weatherstation.wunderground.com/weatherstation/updateweatherstation.php` | GET query | Station key | Imperial |
| **CWOP / NOAA** | `cwop.aprs.net:14580` | **TCP (APRS-IS)** | None — fixed passcode `-1` | Imperial |
| PWSWeather | `pwsupdate.pwsweather.com/api/v1/submitwx` | GET query | Station password | Imperial |
| Windy | `stations.windy.com/api/v2/observation/update` | GET query | Station password | Metric |
| OpenWeatherMap | `api.openweathermap.org/data/3.0/measurements` | POST JSON | API key | Metric (°C, hPa) |
| Wetternetzwerk.pro (Germany) | `api.wetternetzwerk.pro/weatherstation/updateweatherstation.php` | GET query | Station key | Imperial |
| Meteo-Services (Germany) | `channel1.meteo-services.com/stations/index.php` | POST form | **None** — station ID only | Metric |

Where your data ends up varies by network, and none of these are
purely hobbyist. A rough spectrum:

- **National weather services.** **CWOP** feeds NOAA's MADIS, which
  supplies National Weather Service forecasters and researchers (see
  [CWOP](#cwop--noaa)). **WOW-BE** is run by KMI/KNMI; KNMI states that
  Dutch WOW observations feed the maps in the KNMI app and contribute to
  research such as urban-heat studies and more detailed extreme-weather
  advice.
- **Commercial data platforms.** **Weather Underground** is part of The
  Weather Company (an IBM business), which sells the aggregated PWS data
  through enterprise data packages. **Windy** and **OpenWeatherMap** run
  paid, professional API tiers over their data; OpenWeatherMap markets
  agriculture, logistics, insurance, and forestry use explicitly. So
  contributing to these is contributing to a commercial product, not
  only a public map.
- **Community and regional networks.** **PWSWeather**,
  **Wetternetzwerk.pro**, and **Meteo-Services** are primarily
  enthusiast and regional-hobbyist platforms, though even these may be
  consumed by third parties.

The practical takeaway: your observations may be used well beyond a
personal dashboard — by forecasters, researchers, or commercial data
buyers, depending on the network. If that matters to you, weigh it per
network before enabling it, and note the coordinate-precision guidance
below.

**Meteo-Services has no authentication.** See
[Networks without authentication](#networks-without-authentication).

WOW-BE offers three protocols. This integration uses the **Weather
Underground** one — see [Protocol choice](#wow-be-protocol-choice).

### Met Office WOW (UK) is not supported

The Met Office began retiring WOW in January 2026, with full
decommissioning planned for late 2026, and is
[not permitting migration to a third party](https://weatherspares.co.uk/blogs/news/new-replacement-for-the-uk-metoffice-wow-system).
RMI relaunched the platform as WOW-BE with a new backend, extended to
cover the rest of Europe.

WOW-BE is a national-meteorological-service network, not just a hobbyist
platform. KNMI (which directs Dutch users to the KMI-run WOW-BE) states
that Dutch WOW observations feed the maps in the KNMI app and contribute
to research, including urban-heat studies and more detailed
extreme-weather advice. Observations are unvalidated and the institute
does not guarantee their quality, but they are put to operational use.

This integration does not support `wow.metoffice.gov.uk`. The endpoint
still responds as of this writing, but it is on a published path to
shutdown and no new station should be pointed at it. Re-register at
[wow.meteo.be/web/login](https://wow.meteo.be/web/login) — Met Office
site IDs and PINs do not carry over.

You map Home Assistant entities once. The integration converts units per
network and drops any field you have not mapped.

## Installation

### HACS (custom repository)

1. HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/lancer73/ha-weather-uploader`, category
   "Integration"
3. Install "Weather Network Uploader"
4. Restart Home Assistant

### Manual

Copy `custom_components/weather_uploader/` into your Home Assistant
`config/custom_components/` directory and restart.

## Configuration

Settings → Devices & Services → Add Integration → "Weather Network
Uploader".

The flow has three steps:

1. **Networks and interval.** Pick one or more networks. The interval
   is in seconds, minimum 60. Most networks throttle or reject faster
   than one update per minute; 300 is a safe default.
2. **Credentials.** One form per network. You will be asked for a
   station identifier and an authentication key.
3. **Sensor mapping.** One optional entity picker per weather field.
   Leave blank what you do not have.

### Changing configuration later

Settings → Devices & Services → Weather Network Uploader → Configure.

The options flow lets you change the **interval** and the **sensor
mapping**. It does not let you change credentials — see
[Security considerations](#security-considerations) for why. To rotate a
key, remove the integration and add it again.


### Adding or removing networks later

You do not have to remove and re-add the integration to change which
networks you publish to. Open the integration's **Configure** dialog and
choose:

- **Edit sensor mapping and intervals** — the sensor-to-field mapping,
  polling interval, and staleness limit.
- **Add a weather network** — pick one or more networks you have not set
  up yet and enter their credentials, using the same steps as initial
  setup (including OpenWeatherMap station creation). The new uploads
  start after the entry reloads.
- **Remove a weather network** — pick networks to stop publishing to.
  Their stored credentials are deleted and their uploads stop. Your
  sensor mapping is untouched, since it is shared across networks.

To change an existing network's credentials, remove it and add it again;
credential editing in place is not offered, to avoid holding secrets in
more than one part of the stored configuration.
## WOW-BE protocol choice

WOW-BE exposes three send endpoints. They are not equivalent:

| Protocol | Auth | Measurement fields | Used? |
| --- | --- | :-: | :-: |
| `/send/weatherunderground` | `ID` + `PASSWORD` | **16** | ✅ |
| `/send/wow` | `siteid` + `siteAuthenticationKey` | 15 | — |
| `/send/ecowitt` | **none** (MD5 of MAC) | 15 | ❌ |

**Weather Underground is used** because it accepts the most parameters
with no security cost. Its only measurement advantage over the WOW
protocol is `UV` — the two are otherwise identical in coverage and both
define a `403 Invalid site credentials` response. One extra field for
free.

**Ecowitt is deliberately not implemented.** RMI's documentation states
it *"does not require an ID or authentication key. The station uses its
MAC address to identify itself."* The `PASSKEY` it sends is an MD5 of
the station MAC, which is an identifier rather than a credential: a MAC
is broadcast on the local network, is 48 bits of which the top 24 are a
public IEEE vendor OUI, and the residual space falls to unsalted MD5 in
well under a second. Anyone who learns a station's MAC could publish as
that station, with no key to rotate afterwards. The endpoint's response
list corroborates it — 200, 422, 429, and **no 403, because there is no
credential to reject**.

That protocol exists so off-the-shelf station firmware, which cannot
send an arbitrary key, can reach WOW-BE at all. Home Assistant has no
such limitation, so this integration does not offer the weaker option.

## WOW-BE authentication

Verified against the [WOW-BE API docs](https://wow.meteo.be/docs/api/)
(OpenAPI 3.1, v2.0), the [open-source
server](https://github.com/rmibelgium/wowbe), and the live endpoint on
2026-07-16.

Authentication is the `PASSWORD` field in the JSON body of a `POST` to
`/api/v2/send/weatherunderground`. The spec describes it as
*"Authentication Key (PIN code or Password)"* — one field, either
credential style. There is no separate mode to select: **a complex
password just works.** Paste whatever you chose at registration.

The spec declares no `securitySchemes` and there is no HTTP Basic or
bearer-token variant. `ID`, `PASSWORD`, and `dateutc` are the three
required fields.

For `ID`, either the short station ID or the long UUID from your
registration email works. The server accepts both and tailors its
validation error to whichever form you sent — a numeric-looking value is
checked as a short ID, a UUID-shaped one as a UUID.

### Timestamps

`dateutc` is sent as `Y-m-d H:i:s` in UTC. The server enforces exactly
this: an ISO-8601 offset form is rejected with *"The dateutc field must
match the format Y-m-d H:i:s"*. The spec's `format: date-time` is
misleading. This is handled for you.

### Migrating from WOW-UK

If your station was on the Met Office WOW, re-register at
[wow.meteo.be/web/login](https://wow.meteo.be/web/login) — your old site
ID and 6-digit PIN do not carry over. You will receive a new station ID
and the key you chose. Then add WOW-BE as a separate network in this
integration; do not just change credentials on the WOW-UK entry.

### Rate limiting and interval

WOW-BE limits sends to **20 per minute per site** and **600 per minute
per IP**, returning HTTP 429 on excess.

RMI [recommends a 1-minute upload
interval](https://wow.meteo.be/en/connect-your-station/wow-be-ready-station/),
which is the default here and uses 5% of the per-site budget.

**You do not need to compromise for slower networks.** The configured
interval is the sensor *polling* cadence. Each network additionally
throttles itself against its own minimum, so a 60 s poll sends to WOW-BE
every minute while Windy still only receives an update every 5 minutes.

| Network | Minimum send interval | Source |
| --- | --- | --- |
| WOW-BE | 60 s | RMI recommendation; 20/min/site limit |
| Weather Underground | 60 s | accepts rapid-fire; 60 s is conservative |
| CWOP | 300 s | **NOAA rule**: max one packet per 5 min |
| PWSWeather | 300 s | conservative default, unverified |
| Windy | 300 s | **documented**: once per 5 min |
| OpenWeatherMap | 60 s | conservative default, unverified |
| Wetternetzwerk.pro | 600 s | another operator's choice, unverified |
| Meteo-Services | 300 s | another operator's choice, unverified |

Set the poll interval to whatever your fastest network wants. Networks
that are not due are skipped for that tick and keep their previous
status.

## CWOP / NOAA

CWOP is not an HTTP API. It is APRS-IS: a TCP connection to
`cwop.aprs.net:14580`, a login line, one packet, disconnect. This
integration speaks that protocol **natively** — no third-party bridge,
no extra dependency.

### Why not an HTTP bridge

Public HTTP-to-APRS bridges exist, and other forwarders use them. This
integration does not, deliberately:

- A bridge is an unaffiliated third party. The best-known one is a
  personal project whose author states plainly it has no relation to
  CWOP, findu, or NOAA.
- Every observation, your station ID, and your **exact coordinates**
  would pass through it.
- It buys nothing but convenience. CWOP requires **no password** for
  non-ham stations — the passcode is literally `-1` — so a bridge holds
  no credential on your behalf. It saves a TCP socket, which Home
  Assistant can open itself.

Trading your home coordinates to a third party to avoid writing socket
code is a bad exchange.

### Registration and limits

Register at <https://madis.ncep.noaa.gov/cwop_signup.shtml> to get a
callsign (e.g. `EW9876`). Enter it as the station ID; leave the key
blank.

NOAA asks for **no more than one packet every 5 minutes**, which is the
default here and is a published rule rather than a guess.

### What MADIS actually ingests

The APRS weather format carries three rainfall figures, but **MADIS
accepts only two**: `rain_hourly` (past 60 min) and `rain_24h` (rolling
24 h). `rain_daily` (since local midnight) is transmitted but ignored by
MADIS. If you want your rain data used, map `rain_hourly` and
`rain_24h`.

## Networks without authentication

**Meteo-Services** identifies your station by station ID alone. There is
no key, password, or token. Anyone who learns or guesses that ID can
publish observations as your station, and there is nothing to rotate
afterwards.

This integration declines to implement WOW-BE's Ecowitt protocol for
exactly this reason — but that case had an authenticated alternative on
the same network. Meteo-Services does not: it is participate on these
terms or not at all. So it is offered, with the trade stated plainly in
the config flow, and left to you.

Note also that Meteo-Services returns HTTP 200 with an empty body
regardless of outcome. A green status entity means "the request was
accepted", not "the observation was stored".

## OpenWeatherMap stations

OpenWeatherMap is the only supported network with no website signup for
stations. A station is created through its API, and the `station_id`
used for uploads is an internal identifier that does not exist until the
station has been created.

The configuration handles this for you. When you add OpenWeatherMap, the
setup step asks for your API key and then offers a choice:

- **Create a new station.** You give a short label, a display name, and
  the station's coordinates and altitude. The integration calls
  OpenWeatherMap, creates the station, and stores the internal ID it
  returns. If a station with the same label already exists on your
  account, it is reused rather than duplicated, so re-adding the
  integration will not spawn copies.
- **Use an existing station ID.** If you already created a station (via
  the API or a previous setup), enter its internal ID directly — the
  long hexadecimal string, not the label you chose.

A bad API key or a connection problem is reported on the form
immediately, rather than surfacing later as failed uploads.

## Station coordinates

CWOP and Meteo-Services require your station's latitude and longitude on
every observation; Meteo-Services also wants altitude. The config flow
only asks when one of those networks is selected — there is no reason to
collect coordinates for networks that never receive them.

**These are published.** Both networks plot contributing stations
publicly. Six decimal places locates a doorway; three (~100 m) is ample
for meteorology. Round before entering unless you specifically want your
exact position on a public map.

## Sensor reference

Every field is optional. The left column is the mapping key shown in the
config flow. The "internal unit" is what the integration normalizes to
before each uploader converts again.

| Key | Internal unit | WOW-BE | WU | CWOP | PWS | Windy | OWM | WNW | M-S |
| --- | --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| `temperature` | °C | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `dewpoint` | °C | ✅ | ✅ | — | ✅ | ✅ | ✅ | ✅ | ✅ |
| `humidity` | % | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `pressure_absolute` | hPa | ✅ | — | — | — | ✅ | — | — | — |
| `pressure_relative` | hPa | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ | ✅ |
| `wind_speed` | m/s | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `wind_gust` | m/s | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `wind_direction` | ° | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `wind_gust_direction` | ° | ✅ | ✅ | — | — | — | — | ✅ | — |
| `rain_rate` | mm/h | ✅ | — | — | — | — | — | — | ✅ |
| `rain_hourly` | mm | — | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `rain_24h` | mm | — | — | ✅ | — | — | ✅ | — | — |
| `rain_daily` | mm | ✅ | ✅ | ▲ | ✅ | — | — | ✅ | ✅ |
| `rain_weekly` | mm | — | ✅ | — | — | — | — | — | — |
| `rain_monthly` | mm | — | ✅ | — | ✅ | — | — | — | — |
| `rain_yearly` | mm | — | ✅ | — | ✅ | — | — | — | — |
| `solar_radiation` | W/m² | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ | ✅ |
| `uv_index` | index | ✅ | ✅ | — | ✅ | ✅ | — | ✅ | ✅ |
| `illuminance` | lux | — | — | — | — | — | — | — | — |
| `indoor_temperature` | °C | — | ✅ | — | — | — | — | ✅ | — |
| `indoor_humidity` | % | — | ✅ | — | — | — | — | ✅ | — |
| `soil_temperature` | °C | ✅ | ✅ | — | ✅ | — | — | ✅ | ✅ |
| `soil_moisture` | % | ✅ | ✅ | — | ✅ | — | — | ✅ | ✅ |
| `leaf_wetness` | % | — | ✅ | — | — | — | — | — | ✅ |
| `pm25` | µg/m³ | — | ✅ | — | — | — | — | — | — |
| `pm10` | µg/m³ | — | ✅ | — | — | — | — | — | — |
| `co2` | ppm | — | — | — | — | — | — | — | — |
| `lightning_count` | count | — | — | — | — | — | — | — | — |
| `lightning_distance` | km | — | — | — | — | — | — | — | — |
| `visibility` | km | ✅ | ✅ | — | — | — | ✅ | — | — |
| `cloud_base` | m | — | — | — | — | — | — | — | ✅ |

Columns: WNW = Wetternetzwerk.pro, M-S = Meteo-Services.
▲ = transmitted but ignored by MADIS.

Fields with no ✅ in any column are accepted, normalized, and exposed in
the `last_payload` attribute, but no supported network has a parameter
for them yet. They are kept so a future uploader can use them without a
config migration.

### Rain rate vs rain accumulation

**WOW-BE's `rainin` is an instantaneous rain rate in inches/hour.** The
legacy WOW-UK protocol used the same parameter name for an hourly
*accumulation*; RMI changed this deliberately, and the spec is explicit.

This integration keeps them as separate mapping keys — `rain_rate` and
`rain_hourly` — and routes each to the right parameter per network. Map
both if your station exposes both. Mapping an accumulation sensor to
`rain_rate` publishes a wrong rate that looks entirely plausible.

### Absolute vs relative pressure

This matters and gets silently mis-mapped often:

- **`pressure_relative`** is sea-level-adjusted (QNH). WOW-BE
  (`baromin`), Weather Underground, PWSWeather, and OpenWeatherMap want
  this (OpenWeatherMap in hPa, the others converted to inHg).
- **`pressure_absolute`** is raw station pressure (QFE). Windy wants
  this, and WOW-BE accepts it separately as `absbaromin`.

WOW-BE resolves an ambiguity the legacy protocol had: `baromin` is now
always relative and `absbaromin` always absolute. **`baromin` is
authoritative** — if both are sent, the server uses `baromin` and
discards `absbaromin`. If only `absbaromin` is sent, the server derives
the relative value using the altitude from your station registration.

Because sending both would waste a field, this integration sends exactly
one:

- `pressure_relative` mapped → sends `baromin` (your own reduction wins)
- only `pressure_absolute` mapped → sends `absbaromin` (RMI reduces it
  for you, using your registered altitude)

If you are unsure which your station reports, mapping only
`pressure_absolute` is the safer choice: RMI's reduction uses your
registered altitude and is not subject to your station's own
configuration.

Mapping the wrong one produces data that looks plausible and is wrong by
roughly 1 hPa per 8 m of altitude. Map both if your station exposes
both.

### Unit conversion

You do not need to convert anything yourself. The coordinator reads each
entity's `unit_of_measurement` attribute and converts using Home
Assistant's own converters. An entity reporting °F is handled the same
as one reporting °C.

If an entity has no unit attribute, its value is passed through
unconverted — make sure it is already in the internal unit from the table
above.

## Entities

### One per network

`binary_sensor.weather_network_uploader_<network>_upload`, e.g.
`..._wow_be_upload`. Device class `connectivity`.

- **State:** `on` when the last upload succeeded, `off` when it failed,
  `unknown` before the first attempt.
- **`last_error`:** the status and truncated response body from the last
  failure, or `null`. WOW-BE distinguishes 403 (bad credentials), 422
  (validation, with the offending field named), and 429 (rate limited).
- **`sensors_published`:** how many fields went out last cycle.
- **`last_payload`:** the normalized values sent. Sensor data only —
  credentials are added inside each uploader after this dict is built,
  so they never appear here, in the states API, or in a template.

### One for source data health

`binary_sensor.weather_network_uploader_source_data_problem`. Device
class `problem`, so `on` means something is wrong.

**This exists because upload success is a misleading health signal on
its own.** If your station stops reporting, Home Assistant keeps its
last value — a perfectly valid number. Every upload would keep
succeeding, and every network entity would stay green, while the
observations you publish are hours old. The upload is working; the data
is fiction.

- **State:** `on` when nothing publishable remains.
- **`stale_sensors`:** fields dropped for being too old.
- **`missing_sensors`:** fields whose entity is gone, unavailable, or
  non-numeric.
- **`implausible_sensors`:** fields whose value fell outside the sane
  range for that measurement — usually a wrong sensor or a unit
  mismatch.
- **`sensors_published`** / **`max_sensor_age`**: current counts and the
  active threshold.

Watch this one, not the upload sensors, to know whether your station is
alive.

## How failures are handled

| Failure | Detection | Behaviour |
| --- | --- | --- |
| Entity deleted or renamed | `states.get()` returns nothing | Field skipped; warned once; other fields still publish |
| Entity `unavailable` / `unknown` | State check | Field skipped silently (normal and transient) |
| Entity state not numeric | `float()` fails | Field skipped; warned once |
| **Entity stops reporting** | `last_reported` older than the limit | **Field dropped, never republished as current**; named in `stale_sensors` |
| No fields left at all | Empty payload | No upload attempted; `source_data_problem` turns `on` |
| One network rejects the data | Non-2xx response | That network's entity goes `off` with `last_error`; the others are unaffected |
| One network times out or refuses | Transport exception | Same; caught per network, never propagated |
| One network raises unexpectedly | `return_exceptions=True` on the gather | Logged as an error; other networks still complete |
| Provider rate limit hit | HTTP 429 | Recorded in `last_error`; the per-network throttle should prevent it |

Nothing is retried within a cycle. A failed upload consumed the
provider's rate budget, and retrying immediately would make a 429 worse;
the next cycle is the retry.

### Sensor staleness

Readings whose entity has not **reported** within `max_sensor_age`
(default 3600 s) are not published. Tune it under **Configure**; `0`
disables the check.

#### Reporting is not the same as changing

This distinction decides whether the check protects you or silently
deletes your data, so it is worth being explicit.

Home Assistant tracks two timestamps:

- **`last_updated`** — when the value last *changed*.
- **`last_reported`** — when the integration last *wrote* the value,
  changed or not.

When a write leaves the state and attributes identical, HA does not
create a new state object at all: it refreshes `last_reported`, fires
`state_reported`, and leaves `last_updated` alone.

That matters enormously for weather data, because **holding a constant
value is the normal case**:

| Field | Sits at 0.0 | For |
| --- | --- | --- |
| `rain_rate`, `rain_hourly` | whenever it is not raining | most of the time |
| `rain_daily`, `rain_24h` | all night, and all dry week | days |
| `solar_radiation`, `uv_index` | every night | 8–14 hours, nightly |
| `wind_speed`, `wind_gust` | on a calm night | hours |

Every one of those has a `last_updated` of hours or days ago while being
perfectly healthy. A check built on `last_updated` would drop rain from
nearly every payload and drop solar and UV **every single night**.

More fundamentally, `last_updated` *cannot* tell the two failures apart:

| | `last_updated` | `last_reported` |
| --- | --- | --- |
| Healthy rain sensor, dry for 2 days | 2 days ago | **30 s ago** |
| Dead station, last value 2 days old | 2 days ago | **2 days ago** |

Identical on the left; decisive on the right. This integration uses
`last_reported`.

#### Choosing a value

The default is deliberately generous: it only needs to exceed your
station's reporting interval, not its rate of change. Lower it if your
station reports every minute and you want failures caught sooner.

Disabling it is offered but discouraged: with the check off, a station
that dies at 03:00 republishes its final reading as a current
observation forever. For CWOP that means feeding fiction to NOAA's MADIS
and, through it, National Weather Service forecasters.

### Guarding against faulty sensor mappings

Nothing stops you from mapping the wrong entity to a field — pointing
the temperature field at a humidity sensor, say. Three non-blocking
controls try to catch that, none of which prevents a determined
mapping (many valid DIY sensors lack a device_class or units):

- **Type check at configuration.** When you save a mapping whose source
  entity has a `device_class` that does not match the field (a
  `humidity` sensor on a temperature field), the flow shows a
  confirmation step listing the mismatches. You can proceed anyway. A
  correct mapping saves in one step with no extra prompt.
- **Missing-unit warning at configuration.** If a mapped entity
  declares no `unit_of_measurement`, the same step warns that its value
  will be assumed to already be in the field's expected unit, since
  there is nothing for the runtime converter to check against.
- **Plausibility check at runtime.** Each converted reading is bounds-
  checked against a wide sane range for that field (humidity 0–100,
  relative pressure ~870–1085 hPa, and so on). A value outside it — the
  classic symptom being a pressure of `101325` because the source was
  Pascals rather than hectopascals — is dropped for that cycle and
  named in the `implausible_sensors` attribute of the source-data
  problem sensor. It is never published.

The ranges are deliberately generous: they exist to catch a wrong
mapping or a wrong unit, not to second-guess real weather. Cumulative
rain totals (weekly, monthly, yearly) have no upper bound and are not
range-checked.

Example automation:

```yaml
automation:
  # A network is rejecting us.
  - alias: Notify on weather upload failure
    trigger:
      - platform: state
        entity_id: binary_sensor.weather_network_uploader_wow_be_upload
        to: "off"
        for: "01:00:00"
    action:
      - service: notify.persistent_notification
        data:
          message: >
            WOW-BE uploads failing for an hour:
            {{ state_attr(trigger.entity_id, 'last_error') }}

  # Our own station has gone quiet. This is the one that matters:
  # it fires while every upload sensor is still green.
  - alias: Notify on stale weather data
    trigger:
      - platform: state
        entity_id: binary_sensor.weather_network_uploader_source_data_problem
        to: "on"
        for: "00:30:00"
    action:
      - service: notify.persistent_notification
        data:
          message: >
            Weather station may be offline. Stale:
            {{ state_attr(trigger.entity_id, 'stale_sensors') | join(', ') }}
```

## Security considerations

Read this before publishing.

### Your location becomes public

Weather Underground and both WOW instances plot participating stations
on public maps at the precision you gave during station registration.
Publishing associates a physical address with an always-on device and a
posting schedule. If that is not what you want, register the station at
a deliberately coarse location, or do not publish.

### Credentials in query strings

Weather Underground, WOW-UK, PWSWeather, and OpenWeatherMap all pass the
credential as a URL query parameter. TLS protects it in transit, but it
lands in the provider's access logs by design, and in any intercepting
proxy's logs on your own network. This is inherent to those APIs.

Windy puts the key in the URL path — same exposure.

**WOW-BE is the exception**, and it is a meaningful one: this
integration sends the credential in a JSON request body, so it stays out
of URL-based logging on both ends.

(Windy's v2 API is the opposite case: it is Weather Underground
compatible and requires the station password in the query string. This
integration keeps it out of its own logs and error attributes, but the
password does travel in the request URL, as the API mandates. Use
HTTPS, which this integration always does.)

That is our design choice, not a property of the API. WOW-BE's endpoint
is Laravel and merges query parameters into request input, so it accepts
the credential in a GET query string just as readily — other clients do
exactly that. The endpoint permits the weaker form; we do not use it.

### Credential storage

Config entry data, including every key, is stored in cleartext in
`config/.storage/core.config_entries`. This is standard Home Assistant
behaviour and applies to every integration. It matters if that file is
in an off-box backup, a git repo, or a snapshot you share when asking
for help.

The options flow deliberately excludes credentials. Home Assistant
stores options separately from entry data, so a credential editable
there would be written to `.storage` in two places, doubling the
cleanup surface on rotation.

### WOW-BE credential strength

WOW-BE accepts arbitrary-length keys and its docs call the field "PIN
code or Password". Historically the Met Office issued a 6-digit numeric
PIN — a 10⁶ keyspace against a publicly visible site ID.

Choose a long random password at registration. Nothing in this
integration can compensate for a 6-digit one, and the site ID is not
secret.

WOW-BE's rate limit of 20 requests/minute/site bounds online brute
force, which the legacy protocol did not.

### Why Ecowitt is not offered

WOW-BE's Ecowitt endpoint has no authentication: the station is
identified by an MD5 of its MAC address, which is a public identifier
rather than a secret. It is not implemented here. See
[Protocol choice](#wow-be-protocol-choice).

### Logging

Request parameters are never logged. Response bodies are truncated to
200 characters before logging. Do not enable aiohttp's own debug logging
while this integration is running: it logs full request URLs, including
every credential above.

### Data you may not intend to publish

`indoor_temperature`, `indoor_humidity`, and `co2` are occupancy
signals. A CO₂ trace published every five minutes reveals when your house
is occupied and roughly by how many people. Weather Underground accepts
indoor readings and displays them. Map these only if you have thought
about it.

## Troubleshooting

Enable debug logging:

```yaml
logger:
  default: warning
  logs:
    custom_components.weather_uploader: debug
```

| Symptom | Likely cause |
| --- | --- |
| All networks `off`, no log entries | No sensors mapped, or all mapped entities unavailable. Uploads are skipped entirely when there is no data. |
| `source_data_problem` is `on` | Every mapped sensor is stale, missing, or unusable. Check `stale_sensors` and `missing_sensors`; usually the station is offline. |
| Uploads green but the network shows old data | Values are being dropped as stale before sending. Check `sensors_published` and `stale_sensors`. |
| Fields vanish from `last_payload` over time | Those entities stopped *reporting* and are now past `max_sensor_age`. A value that merely stops *changing* — rain at 0.0, solar at night — is not affected. |
| `HTTP 403` from WOW-BE | Invalid site credentials. Check the key and that you used the ID from the WOW-BE registration email, not an old Met Office site ID. |
| `HTTP 429` from Windy | Sent faster than once per 5 minutes. The per-network throttle should prevent this; if you see it, the station is also being fed by another client. The error carries a `retry_after` time. |
| `HTTP 422` from WOW-BE | Field validation failed. The message names the field; check `last_error`. A bad `siteid` gives "must be a valid site short ID". |
| `HTTP 429` from WOW-BE | Rate limited: 20/min/site, 600/min/IP. Increase the interval. |
| `HTTP 401` / `HTTP 403` elsewhere | Wrong key or station ID. |
| Rain rate looks 10x off on WOW-BE | `rain_rate` mapped to an accumulation sensor. It must be a rate. |
| Values plausible but consistently offset | Absolute/relative pressure mix-up, or an entity with a missing unit attribute. |
| `Mapped entity ... is not numeric` | The entity's state is a string like `north`. Use a template sensor to convert. |

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
ruff check custom_components/
ruff format --check custom_components/
pytest
```

### Brand image

The integration icon ships in `custom_components/weather_uploader/brand/`,
which Home Assistant serves directly for custom integrations
[since 2026.3](https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api).
No submission to the `home-assistant/brands` repository is needed.

The PNGs are build artifacts. Edit `brand_src/icon.svg` and re-render:

```bash
pip install cairosvg pillow
python tools/render_brand.py
```

Only `icon.png` (256×256) and `icon@2x.png` (512×512) are shipped. The
artwork is square, so Home Assistant falls back to the icon where a
logo would be used, and it is legible on both light and dark themes, so
no `dark_` variants are needed.

## Contributing

Issues and pull requests welcome. Two things that would genuinely help:

1. Per-network upload intervals. The interval is currently global, which
   forces a compromise between RMI's recommended 60 s and Windy's ~5 min
   minimum.
2. Additional uploaders — the `BaseUploader` interface is one method.

## License

MIT. See [LICENSE](LICENSE).
