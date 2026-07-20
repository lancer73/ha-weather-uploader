# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A Home Assistant custom integration that reads mapped sensor entities on
an interval and pushes observations to several public weather networks.
No polling of external services, no incoming data. Purely outbound.

## Working style for this repo

- **Minimal diffs.** Change only what the task requires. Do not
  reformat, reorder imports, or "tidy" adjacent code.
- **Say when unsure.** If provider API behaviour cannot be verified,
  say so in the response and mark it in the code or docs. Do not present
  a guess as fact. There are already unverified items in this repo
  (see below) and they are labelled as such deliberately.
- **No flattery.** Skip preamble. Lead with the answer or the diff.
- **SemVer strictly.** See Versioning below.
- **Keep a Changelog format.** See Changelog below.

## Layout

```
custom_components/weather_uploader/
├── __init__.py           setup/unload, uploader construction
├── manifest.json         version lives here too
├── const.py              DOMAIN, SENSOR_KEYS, service ids, hosts
├── config_flow.py        3-step config flow + options flow
├── coordinator.py        entity read, unit normalization, fan-out
├── binary_sensor.py      one connectivity entity per network + a data-health entity
├── sensor.py             one last-error status entity per network (state = short code)
├── translations/en.json  all user-facing strings
├── brand/                icon.png + icon@2x.png (generated)
└── uploaders/
    ├── __init__.py       build_uploader() factory
    ├── base.py           BaseUploader ABC + unit helper functions
    ├── wunderground.py
    ├── wowbe.py          WOW-BE via its WeatherUnderground endpoint
    ├── cwop.py           CWOP over NATIVE APRS/TCP - not HTTP
    ├── pwsweather.py
    ├── windy.py          Windy v2, GET query, metric, WU-compatible
    └── openweathermap.py JSON POST, SI
```

## Architecture

**Data flow, once per interval:**

1. `UploadCoordinator._async_update_data` calls `read_sensors()`.
2. `read_sensors()` walks `self._map` (key → entity_id), reads each
   state, rejects non-numeric and unavailable states, then converts to
   the internal unit via `_convert()` using the entity's declared
   `unit_of_measurement`.
3. If the resulting dict is empty, the cycle is skipped. No empty
   uploads.
4. Otherwise every uploader's `send()` is awaited in parallel via
   `asyncio.gather(..., return_exceptions=True)`.
5. Results become `{"data": ..., "results": ..., "errors": ...}`, which
   the binary sensors read.

**Staleness is not optional, and it MUST use `last_reported`.**
`read_sensors()` drops any reading whose `_reported_at(state)` is older
than `max_sensor_age`. Two rules, both learned the hard way:

1. **Never `last_updated`.** HA's state machine discards a write when
   state and attributes are unchanged: it refreshes `last_reported` and
   leaves `last_updated` alone. For weather, constant values are normal
   — rain sits at 0.0 for days, solar and UV sit at 0.0 every night.
   Keying on `last_updated` drops rain from nearly every payload and
   drops solar/UV nightly. Worse, it *cannot detect the failure it was
   written for*: a healthy dry rain sensor and a dead station have
   identical `last_updated`. Only `last_reported` separates them. This
   was a real bug, caught in review — do not reintroduce it.
2. **Never remove the check.** It is the only thing between a dead
   station and publishing its last reading to NOAA forever. A stale
   value passes every other check: not unknown, not unavailable, parses
   as a float.

The default (3600s) only needs to exceed the station's *reporting*
interval, not its rate of change. It is a heuristic, not a contract.

`_reported_at()` keeps a `getattr` fallback to `last_updated` for cores
older than 2024.4. `hacs.json` requires 2024.6.0, so it should never
fire; leave it.

**Sensor mapping validation is advisory, never blocking.** Three
layers: device_class mismatch and missing-unit warnings at config time
(a confirm step shown only when there is something to flag), and a
runtime plausibility bounds-check (`PLAUSIBLE_RANGE`, `_is_plausible`)
that drops out-of-range values and lists them in `implausible_sensors`.
The DIY/template audience routinely runs sensors with no device_class
or units, so none of this may hard-block a mapping. If asked to enforce
device_class strictly, push back: it would hide valid sensors. Ranges
are wide on purpose — they catch mis-mappings and unit errors (Pascals
vs hPa), not real weather extremes. Do not tighten them to "realistic"
values.

**Two entities, two questions.** `UploadStatusEntity` answers "is the
network accepting our data". `SourceDataEntity` answers "is our data
real". They are independent: a dead station produces green uploads. Do
not merge them or derive one from the other.

**Throttling.** The coordinator polls on one global cadence, but each
uploader gates itself via `is_due()` / `mark_sent()` against
`MIN_SERVICE_INTERVAL[service]`. Networks that are not due are skipped
for that tick and keep their previous status (`_carry_forward`). Two
non-obvious choices, both deliberate:

- **Throttle is seeded at construction.** `last_sent` starts at
`time.monotonic()` for a throttled uploader (not `None`), so the first
send waits `min_interval`. This is what stops a Home Assistant restart
-- which rebuilds every uploader -- from firing an immediate upload and
tripping a provider's rate limit (Windy 429 within its 5-minute window).
`min_interval <= 0` stays unseeded and always due. Do not reset it to
`None` at construction "for a fresh start"; that reintroduces the
restart 429.

**Throttle on attempt, not success.** A failed request still consumed
  the provider's rate budget; retrying immediately makes a 429 worse.
- **`time.monotonic()`, not wall clock.** An NTP step or DST change must
  not stall an uploader for hours.

**Internal units** are defined in the comment block above `SENSOR_KEYS`
in `const.py`. That comment is the contract. Uploaders convert *from*
these; nothing converts *to* them except the coordinator.

**Adding a network** means adding one file under `uploaders/`,
subclassing `BaseUploader`, implementing `build_params()`, and wiring it
into `build_uploader()` and `SERVICES`. Override `send()` only if the
transport differs from GET-with-query-params. OWM POSTs JSON; CWOP is
APRS over TCP. Windy is GET-with-query.

## Invariants — do not break these

- **Catch `HomeAssistantError` around unit conversion.** HA's converters
  raise `HomeAssistantError` (MRO: only `Exception`), not `ValueError`.
  `_convert` must catch it, or one sensor with an odd unit fails the
  whole coordinator refresh every tick. Match each field to the right
  converter: accumulations (`rain_hourly`, `rain_24h`) are distance/mm;
  intensity (`rain_rate`) is speed/`mm/h`.
- **Options is authoritative for the mapping once saved.** The mapping
  lives in entry data at setup and in entry options after the settings
  form is saved. Resolve it from options-if-present, not a
  `{**data, **options}` union -- the union cannot express unmapping a
  sensor (a cleared key falls back to data). Networks/credentials always
  come from data.
- **CWOP needs coordinates and no key.** It uses passcode -1 (no key)
  and must have lat/lon; `build_uploader` returns None (skips it) rather
  than defaulting to (0, 0) if coordinates are missing.
- **`last_error` and `last_payload` are exposed as entity attributes.**
  Both are credential-redacted (`last_error` via the setter, payloads
  via `_redact_payload`). `last_payload` records what `send` actually
  transmitted, not a rebuild.


0. **`SUPPORTED_READINGS` must match what `build_params` consumes.**
   Each uploader declares the normalized reading keys it accepts; the
   status sensor's `sensors_published` counts those present in the
   cycle's data, so the figure means the same thing for every network
   (CWOP's single packet reports its measurements, not `1`). If you add
   or drop a `conv(data, "<key>")` in a `build_params`, update that
   uploader's `SUPPORTED_READINGS` to match. A test
   (`test_measurement_count_matches_supported_readings_consumed`)
   enforces that every declared key actually affects the payload, but it
   cannot catch a consumed key you forgot to declare.

0. **`SUPPORTED_READINGS` must match what `build_params` consumes.**
   Each uploader declares the normalized reading keys it accepts; the
   status sensor's `sensors_published` counts those present in the
   cycle's data, so the figure means the same thing for every network
   (CWOP's single packet reports its measurements, not `1`). If you add
   or drop a `conv(data, "<key>")` in a `build_params`, update that
   uploader's `SUPPORTED_READINGS` to match. A test
   (`test_measurement_count_matches_supported_readings_consumed`)
   enforces that every declared key actually affects the payload, but it
   cannot catch a consumed key you forgot to declare.


1. **Credentials never enter the exposed payload.** `build_params()` may
   add them (WOW-BE builds `PASSWORD` straight into its params), but the
   status entity exposes `build_payload()`, which strips any credential
   field (`_CREDENTIAL_FIELDS` in `base.py`) so a secret cannot surface
   in the states API, templates, or diagnostics. The status attribute
   shows each network's OWN payload (per-network `payloads` map in the
   coordinator), not the shared reading set -- these are different, and
   showing the shared set misreports what a network sent. If you add a
   provider whose credential uses a new field name, add it to
   `_CREDENTIAL_FIELDS`.
2. **Never log request parameters.** Several providers carry the
   credential in the query string (Windy's v2 API requires the
   `PASSWORD` query param; the WU-derived ones use a query key). Log
   response bodies only, and truncate to 200 chars (`_BODY_LOG_LIMIT`
   in `base.py`). Windy's error text is built from status codes, never
   the echoed request.
3. **No validation on the credential field.** WOW-BE documents the
   field as "PIN code or Password" and the Met Office no longer
   restricts it to 6 digits. Any length or charset check rejects valid
   credentials. This is intentional, not an oversight — see the
   docstring on `_password_selector()`.
4. **Credentials stay out of the options flow.** HA stores options
   separately from entry data. Putting a secret in options writes it to
   `.storage` twice. Rotation is remove-and-re-add. If someone asks for
   in-place rotation, implement `async_step_reauth`, not an options
   field.
5. **`_prune()` before sending.** Unmapped fields must be absent, not
   empty-string. Several providers treat `param=` as a zero.
6. **One network failing must not affect others.** Keep the
   `return_exceptions=True` on the gather.

## Provider quirks

- **Met Office WOW (UK) is not supported.** It was dropped during
  development: retirement began 01/2026, full decommissioning late 2026,
  and the Met Office does not permit migration to third parties. The
  endpoint still answers (400 on
  a bad request, not 404) — do not take that as a reason to restore it.
  Do not add `wow.metoffice.gov.uk` back.
- **WOW-BE `rainin` is a rate** (in/h), fed from `rain_rate`. The legacy
  WOW-UK protocol used `rainin` for an hourly accumulation, and
  `wunderground.py` (the real WU service) still does. Same name, two
  meanings, two uploaders. Do not "fix" the inconsistency.
- **WOW-BE `visibility` is km,** not miles. Do not add a `km_to_mi` call
  to `wowbe.py`. `wunderground.py` does convert to miles — that is
  correct for the actual WU service.
- **Pressure:** WU, PWS want sea-level-adjusted (`pressure_relative`).
  OWM takes `pressure` in hPa (we map relative). Windy wants station
  pressure (`pressure_absolute`), sent via `mbar` in hPa. WOW-BE takes both, split
  into `baromin` (relative, authoritative) and `absbaromin` (absolute).
  Getting this wrong yields plausible, wrong data. Do not "simplify"
  these to one field.
- **WU-derived APIs** (WU, PWS) share parameter names but not
  parameter sets. Do not assume a param exists on all three because it
  exists on one.
- **`dateutc`** must be `%Y-%m-%d %H:%M:%S` in UTC. Not ISO-8601. This
  is verified against the WOW-BE server: it accepts that format.
- **OWM** needs a station created via its API first. This integration
  does not do that.
- **Windy uses the v2 API** (`GET /api/v2/observation/update`), not the
  legacy `POST /pws/update/{key}`, which Windy deprecated as of
  2026-01. The password is the `PASSWORD` query param. It is WU
  compatible: send metric names (`temp`, `dewpoint`, `mbar`, `wind`),
  not the imperial aliases. Pressure goes via `mbar` (hPa) to avoid a
  Pa conversion. Do not restore the legacy endpoint.
- **WOW-BE rate limits:** 20/min/site, 600/min/IP, HTTP 429 on excess.
  Do not add retry-on-429 without backoff.

## CWOP is native APRS, not HTTP

`cwop.py` opens a TCP socket to `cwop.aprs.net:14580` and speaks APRS-IS
directly. Do not replace this with an HTTP bridge (send.cwop.rest or
similar), however much simpler it looks:

- A bridge is an unaffiliated third party that would see every
  observation, the station ID, and the user's exact coordinates.
- It holds no credential for us. CWOP non-ham auth is the literal
  passcode `-1`. The bridge saves a socket, nothing more.

`build_packet()` is a pure function and is tested byte-for-byte against
the worked example in NOAA's FAQ. If a test there fails, the packet
format is wrong -- check http://wxqa.com/faq.html before touching the
test.

Packet gotchas, all deliberate:

- Wind dir, speed, gust, temp are **positional and required**; missing
  values are `...`, not omitted.
- `b` is tenths of a millibar, five digits. `h00` means 100%.
- `t` is Fahrenheit in three chars; negatives are `-04`, not `-4`.
- **MADIS ingests only `r` (hourly) and `p` (24h) rain.** `P` (since
  midnight) is sent but ignored. That is why `rain_24h` exists as a
  separate sensor key -- do not "simplify" it away.
- NOAA's rate limit (1 packet / 5 min) is a published rule, unlike most
  of `MIN_SERVICE_INTERVAL`. Do not lower it.

## Networks live in entry.data; add/remove edits it directly

Each network's config (with credentials) lives in `entry.data[services]`,
never in options. The options flow can add or remove networks, and does
so by writing `entry.data` via `async_update_entry` (which reloads through
the existing update listener), NOT by mirroring services into options.
Duplicating credentials into the options blob would write secrets to
`.storage` twice — the reason credential editing was originally kept out
of the options flow.

The credential-collection steps (station id, key, geo, OWM sub-flow) are
shared between the config and options flows via the `_CredentialSteps`
mixin. Both flows override `_credentials_done`: initial setup goes to
sensor mapping, the options flow persists to entry data and reloads. If
you add a new credential step, put it on the mixin so both flows get it.
Do not reimplement credential collection in the options flow.

Existing-network credential rotation is deliberately not offered — remove
and re-add instead. Keep it that way unless there is a way to do it
without a second copy of the secret.

## OpenWeatherMap needs a station created first

OWM is the only network whose `station_id` cannot be obtained from a
website: it is an internal ID that only exists after
`POST /data/3.0/stations`. The config flow creates it
(`uploaders/owm_station.py`), with a manual-ID fallback. Two things not
to break:

- **Store the internal ID (`ID`/`id`), never the `external_id`.** The
  external_id is the user's label; the internal ID is what
  `/measurements` requires. Confusing them makes every upload 404.
- **Creation is idempotent on purpose.** `create_station` lists
  existing stations and reuses a matching `external_id` before
  POSTing, so re-running setup does not spawn duplicates on the user's
  account. Do not drop the lookup.

Station management is create/list only. We deliberately do not implement
PUT/DELETE: the integration should not silently mutate or remove a
user's OWM stations.

## Why there is no Ecowitt uploader

WOW-BE exposes `/send/ecowitt`. It is deliberately not implemented, and
that is a security decision, not an oversight.

That endpoint has **no authentication**. The station is identified by
`PASSKEY`, an MD5 of its registered MAC. A MAC is not a secret: it is
broadcast on the local network, is 48 bits with a public 24-bit vendor
OUI, and the residual ~2^24 space falls to unsalted MD5 in well under a
second. The endpoint's responses are 200/422/429 — no 403, because there
is nothing to reject. Compare `/send/weatherunderground`, which does
define "403 Invalid site credentials".

It exists so off-the-shelf station firmware, which cannot send an
arbitrary key, can reach WOW-BE at all. Home Assistant can send a key.
There is no reason to offer the weaker path.

If asked to add it back: raise the above first. It was written, then
removed on purpose.

## Verified facts — do not re-litigate

WOW-BE was verified on 2026-07-16 against the OpenAPI 3.1 spec at
<https://wow.meteo.be/docs/api/> (inlined in the page as
`docs.apiDescriptionDocument`, not served as a standalone file), the
AGPL server source, and the live endpoint. Settled:

- Endpoint `POST /api/v2/send/weatherunderground`, JSON body.
  `/automaticreading` 404s on this host.
- Auth is the `PASSWORD` body field. `securitySchemes` is empty; there
  is no Basic, bearer, or header scheme. "PIN code or Password" — one
  field, both styles, no mode selector needed.
- Required: `ID`, `PASSWORD`, `dateutc`.
- Responses: 200 ok, 403 invalid credentials, 422 validation, 429 rate
  limited (20/min/site, 600/min/IP).
- **`ID` accepts a short ID *or* a UUID.** The 422 message names
  whichever form it detected ("must be a valid site short ID" vs "...
  site UUID"), which is easy to misread as an exclusive requirement. It
  is not. Do not add client-side ID format validation.
- **The endpoint accepts GET with query params too.** It is Laravel and
  merges query into input. We use POST+JSON so the credential stays out
  of URL logs — that is our choice, not something the API enforces. Do
  not "simplify" to a GET.
- **`dateutc` must be `Y-m-d H:i:s`, UTC.** The server rejects an
  ISO-8601 offset with "The dateutc field must match the format
  Y-m-d H:i:s". The spec's `format: date-time` is wrong. `...Z` happens
  to parse, but do not rely on it. `DATEUTC_FORMAT` in `wowbe.py` is the
  verified value — do not "modernise" it to `isoformat()`.
- **`baromin` beats `absbaromin`.** Both sent -> `absbaromin` discarded.
  Only `absbaromin` sent -> server derives relative from the registered
  altitude. So `build_params` sends exactly one, never both. Do not
  "simplify" that branch into sending both.
- Protocol choice: `/send/weatherunderground` (16 measurement fields)
  over `/send/wow` (15). The only difference is `UV`. Both have
  identical auth and an identical 403. Do not switch to `/send/wow`
  "for consistency with the platform name" — it strictly loses a field.

If asked to add an `auth_mode` back, or to support HTTP Basic for WOW:
don't. It was tried during development and dropped as speculative and
unsupported. An option that can only produce 403 is worse than no
option.

## Still unverified

- **The CWOP APRS socket path has never run against a live server.**
  `build_packet()` is verified byte-for-byte against NOAA's example, but
  that only covers the wire *format*. The connect/login/send sequence in
  `send()` is written from the FAQ and is unexercised. Do not treat the
  passing packet tests as evidence that CWOP uploads work.
- **No end-to-end upload has ever succeeded.** Every WOW-BE check used
  bogus credentials and stopped at 422 on the site id. That proves the
  endpoint, method, body shape, field names, and `dateutc` format are
  right; it does not prove a real observation lands. Only a registered
  station can confirm that.
- **The test suite has never run.** Importing the package pulls in Home
  Assistant, so `pytest` needs `pytest-homeassistant-custom-component`.
  Uploader logic was verified by importing the modules with HA stubbed
  out. `coordinator.py` has no test coverage at all.
- OWM station registration is not implemented; station ids must be
  created out-of-band.
- Whether the poll cadence satisfies what WOW-BE means by an
  "instantaneous" rain rate. The field is documented; the tolerance is
  not.
- `MIN_SERVICE_INTERVAL` for PWSWeather, OpenWeatherMap, and Weather
  Underground are conservative guesses. Only WOW-BE (60 s, RMI) and
  Windy (~5 min) come from documentation. Do not lower any of them
  without a citation.

Keep these labelled. If asked to "clean up the docs", they stay.

## Brand images

`custom_components/weather_uploader/brand/` holds `icon.png` (256x256)
and `icon@2x.png` (512x512). HA serves these directly for custom
integrations since 2026.3; no PR to `home-assistant/brands` is needed,
and the `custom_integrations/` folder in that repo is now legacy.

**The PNGs are generated. Do not hand-edit them.** Source of truth is
`brand_src/icon.svg`; re-render with `python tools/render_brand.py`.

Spec constraints that the renderer enforces, per the brands repo README:
1:1 aspect, 256/512 px, PNG, transparency, trimmed of dead margin. The
artwork is ~1.1:1, so the renderer pads the short axis symmetrically
rather than stretching — that is deliberate, not a bug to "fix".

No `logo.png`: a square icon is used as the logo fallback. No
`dark_icon.png`: the icon was checked against both light and the HA dark
background at 24-256 px and reads on both.

**Custom integrations must not use Home Assistant branded imagery** —
it implies official status. The current icon (cloud + upload arrow) is
deliberately generic and borrows no provider's mark.

## Versioning

Current release: **0.7.0**. Semantic Versioning 2.0.0.
**Do not bump the version without being asked** — the maintainer decides
when and what to release.

Pre-1.0 caveat: while the major is 0, the config schema and entity IDs
are not yet stable, so a breaking change is a MINOR bump (0.x.0) rather
than a MAJOR one until 1.0.0 is declared. After 1.0.0:

- **MAJOR** — config entry schema changes requiring migration, removing
  a sensor key, removing a network, or changing an internal unit.
- **MINOR** — new network, new sensor key, new optional config, new
  entity.
- **PATCH** — fixes, docs, wrong-unit corrections, dependency bumps.

`version` in `manifest.json` and the heading in `CHANGELOG.md` must
match on every release. A release commit touches both.

Between releases, accumulate changes under `[Unreleased]`.

Any change to `SENSOR_KEYS` ordering or naming, or to the internal unit
contract, needs a config entry migration and a `VERSION` bump in
`config_flow.py`. Do not silently reinterpret existing stored keys.

## Changelog

Keep a Changelog 1.1.0. Sections in this order, omitting empty ones:
`Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`.

- Every user-visible change gets an entry under `[Unreleased]` in the
  same commit.
- Security-relevant changes go under `Security`, always. This repo
  handles credentials and publishes location data; that section is not
  optional decoration.
- On release: rename `[Unreleased]` to the version with an ISO date, add
  a fresh empty `[Unreleased]`, update the link refs at the bottom. The
  maintainer decides this, not you.
- `Known issues` is a non-standard section used here at the bottom of a
  release. Keep it.

## Style

- Python 3.12+, `from __future__ import annotations` at the top of every
  module.
- Full type hints. `dict[str, float]`, not `Dict`.
- Ruff for lint and format. Line length 88.
- Docstrings on every module, class, and public method. HA's own
  convention: imperative mood, one line where possible.
- `_LOGGER.debug` for normal operation, `warning` for a failed upload
  (recoverable, expected), `error` only for unexpected exceptions.
- Async everywhere. Never block the event loop. Use
  `async_get_clientsession(hass)` — never create a session.
- Use `homeassistant.util.unit_conversion` converters, not hand-rolled
  math, for anything HA already supports. The helpers in `base.py` exist
  only for provider-specific output units HA has no converter for.

## Testing

```bash
ruff check custom_components/
ruff format --check custom_components/
pytest
```

Uploaders are pure functions from a data dict to a params dict — test
`build_params()` directly, no HTTP mocking needed. Test `send()`
separately with `aioresponses`.

Do not hit the live WOW-BE endpoint in tests. It is rate limited per IP
and CI would trip it. The protocol assertions in `test_uploaders.py`
(endpoint path, required fields, rain-rate semantics, km visibility,
split pressure) exist to catch regressions against the verified spec —
if one fails, check the spec before changing the test.

Coordinator tests should cover: missing entity, unavailable state,
non-numeric state, missing unit attribute, unit conversion, empty-data
skip, and one uploader raising while another succeeds.

## Do not

- Add a dependency to `requirements` without a strong reason. It is
  currently empty and that is a feature.
- Add YAML configuration. Config flow only.
- Cache credentials anywhere outside the config entry.
- Add telemetry, analytics, or a "phone home" check.
- Widen the entity selector beyond numeric domains.
