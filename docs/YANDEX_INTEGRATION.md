# Yandex integration status — 2026-11-05

Status: **PARTIAL IMPLEMENTATION — CAMERA ACQUISITION UNRESOLVED**.

## Implemented and observed

`sunnypilot/mapd/yandex/` is an independent, anonymous, bounded read-only client
for the public road-event tile layer used by the Yandex Maps web client. It is
not yet a Navigator camera provider. `tools/yandex_probe.py` exercises the actual
client on host/Linux without changing GPS, vehicle mode, Params or controls.

Public primary client sources retrieved on 2026-11-05:

* https://maps.yastatic.net/s3/front-maps-static/maps-front-maps/build/client/desktop/chunks/base/c42ef5964e6c7eb24412.yandex.ru.js
  supplies the `roadEventsRenderer` tile template.
* https://maps.yastatic.net/s3/front-maps-static/maps-front-maps/build/client/worker/content_provider.worker/089ee1eb891e663c037f.js
  contains the VECTOR3 tile schema and point geometry decoder. SHA-256:
  `09e42fbd9b91ff474d7b1099abf862032a684f5dae0896b62aaed2aef0e0c154`.
* https://maps.yastatic.net/s3/front-maps-static/maps-front-maps/build/client/desktop/chunks/ymaps/ca57b789148da110cd60.yandex.ru.js
  establishes EPSG:3395 ellipsoidal projection, not spherical Web Mercator.

An actual 451-byte Moscow fixture contains ACCIDENT and RECONSTRUCTION points.
These are not speed cameras. Empty tiles and parser success do not prove camera
coverage. Unsupported geometry/schema is rejected, never guessed.

Live calls through the implemented Python client in Linux Docker on 2026-11-05:

| Query viewport | Loaded/requested tiles | Observed records |
| --- | --- | --- |
| Krasnodar, 5 km query | 16/16 | 14: 11 CLOSED, 1 ACCIDENT, 2 RECONSTRUCTION |
| Moscow, 5 km query | 25/25 | 87: 5 CLOSED, 10 ACCIDENT, 72 RECONSTRUCTION |

These are tile viewports covering the query, not an assertion that every returned
point lies inside the exact radius. Reports can change between calls. No camera,
limit or vehicle-control operation was claimed or tested by these calls.

Cache policy: 60-second reuse; at most 64 files and 16 MiB after acquisition;
unchanged response bytes do not trigger writes/fsync. Cache hits preserve their
original observation time. An in-memory SHA-bound verification timestamp permits
60-second reuse after an unchanged successful download without writing metadata;
after a process restart the older disk timestamp is used conservatively.
On network failure expired cache is not advertised
as a fresh observation. Runtime data stays outside the source checkout.

## Remaining camera access problem

An ordinary public web route request returned a genuine Krasnodar route, but no
camera events or posted speed limits in its payload. The regional web event API
returned closures. Neither response establishes Navigator camera availability.

Static inspection of a public decompiled Navigator v27 native library located
`https://proxy.mob.maps.yandex.net:443/mapkit2/driving/2.x/route`. The library
SHA-256 was `68a0f9dfee2ded722f74f76653ba6cc164e9f3a9e90669ad75dde0f4ab0be09a`;
third-party archival provenance is not a guarantee of a current application
contract. A small anonymous request returned HTTP 403. The test does not prove
which client requirements were missing. No application keys were extracted,
no account/installation rotation was used, and no binary is stored in Git.

The official SDK exposes route events, segment limits and upcoming events:

* https://yandex.com/maps-api/docs/mapkit/com/yandex/mapkit/directions/driving/DrivingRoute.html
* https://yandex.com/maps-api/docs/mapkit/com/yandex/mapkit/directions/driving/Event.html
* https://yandex.com/maps-api/docs/mapkit/com/yandex/mapkit/navigation/automotive/UpcomingRoadEvent.html
* https://yandex.com/maps-api/docs/mapkit/com/yandex/mapkit/road_events/EventTag.html

### Follow-up: current SDK contract and the 403

The current official Maven artifact `com.yandex.android:maps.mobile:4.45.0-full`
was downloaded and inspected separately from the archived application. Exact
artifact hashes and verified class/method presence are recorded in
`docs/yandex_sdk_contract.json`; no SDK/native binaries are checked into Git.

**Important distinction:** `DrivingRoute.getEvents()`, `getSpeedLimits()` and
`Event.getSpeedLimit()` are present in ordinary Full MapKit. The separate NaviKit
classes `UpcomingRoadEvent` and `Guidance` are absent from that artifact. Thus a
route-event test can target Full MapKit with an own MapKit key; it is premature
to assert that the separate NaviKit activation is required for every camera
query. A real SDK route response is still required to confirm returned camera
data and server entitlements.

Archived application initialization checks for a nonempty API key before runtime
initialization. Its ARM64 library contains the header names `X-YMapKit-Api-Key`
and `x-yruntime-signature`; instruction references were verified (API key header
at native VAs `0x19acafc`, `0x2281638`, signature reference `0x1752120`). Those
references do not establish the exact route request method/body/signing contract.
The anonymous GET omitted normal SDK initialization and is not an authenticated
client test. A follow-up probe returned the same 10-byte 403 from nginx; the host
had no HTTP_PROXY/HTTPS_PROXY/ALL_PROXY configured. This narrows the evidence but
does not prove the precise server-side rejection rule.

Native type families `RoadEventMetadata`, `LayerRoadEventMetadata`,
`SegmentEvent`, and `roadevents.Config` exist in both the archived application
and current SDK. A separate native tile layer exists; its endpoint/configuration
and speed field units have not been reconstructed. Float fields in a protobuf
parser are not enough to name a field speed or select a conversion factor.

`tools/yandex_sdk_probe/` contains an Android diagnostic client for one
ordinary Full MapKit ONLINE route request with a caller-owned key. It exports
original event tags, nullable camera limits, per-segment limits, geometry and the
conditions-outdated flag. It does not normalize unknown units or control a car.
Timeout/cancellation and snapshot-size budgets are included; error text and key
material are not exported. Compilation was checked against the actual official
SDK `classes.jar` using JDK 21 (`--release 8`), Android compile stubs and Android
JSON API. It is not a Linux/Comma runtime or proof of successful camera access.
A Yandex login alone is not a key.

### Executed Android SDK diagnostic

The user subsequently provided an active own MapKit key and explicitly authorized
one disposable APK-signing key for this isolated test only. An ordinary x86_64
Android APK was built with AGP 8.9.1/Gradle 8.14, signed and signature-verified,
then installed in a separate headless Android API-26 emulator. No firmware keys,
Comma installation, vehicle mode, GPS or existing Waze deployment were changed.
SDK credentials, signing material, APKs, SDK binaries, AVD and build outputs are
outside Git. The key is supplied only in the app-private owner-0600 input file.

Actual results on 2026-11-05:

* Ordinary HTTPS in the same Android app: yandex.ru returned 302 and the bare
  mobile route endpoint returned 403; default TLS verification was retained.
* A chosen-coordinate Krasnodar route through Full MapKit did not complete
  within 45/60 seconds. This is a failed acquisition, not an empty camera list.
* Sanitized SDK logging for a 60-second run inspected eight messages: four
  `forbidden`, four `auth_rejected`, four `api_key_mentioned`, and four
  `invalid_key_hint` classifications. DNS, certificate, connection, quota,
  package and region hints were zero; no explicit HTTP-code evidence was parsed.
  These are limited diagnostic hints, not the complete server rejection reason.
* Windows had an enabled loopback proxy and fake-IP DNS (198.18.x.x). Normal
  Android proxy configuration and an emulator-only transparent host-proxy path
  were tested without changing Windows proxy settings. SDK acquisition still
  failed. General HTTPS success is not proof of SDK authorization.
* The user key-settings screenshot showed an empty application-ID restriction.
  No different application identifier restriction was visible. Own runtime key
  transport was checked by digest comparison without exporting the key/digest.

An optional normal MapView A/B mode is included, not asserted to be required for
Directions callbacks. Its test ended in Activity cancellation after key-related
hints; it is not a successful route or evidence of camera coverage. No key
rotation, other application's key or TLS/certificate bypass was used.

Per-run nonsecret IDs and completion timestamps reject stale result files.
Coordinate strings via adb are parsed explicitly because `am start` has no
Double-extra option. The `networkOnly` diagnostic never reads the key or starts
MapKit. Logging exports only fixed counters/cause-class names, never raw SDK
messages, verbose info, headers, tokens or general logcat.

Historical authorization blocker resolved in subsequent SDK tests after the owner
bound the key to org.sunnypilot.yandexprobe. Binding versus activation delay is
not isolated as the cause. See the current verified status below.

Official SDK initialization requires a developer API key. NaviKit requires
additional activation; a Yandex account is not that key. Android/iOS SDKs do not
establish a supported native Linux/Comma implementation:

* https://yandex.com/maps-api/docs/mapkit/android/generated/getting_started.html
* https://yandex.com/maps-api/docs/mapkit/ios/generated/navigation/getting_started.html

The user permits Yandex's effective limit as fallback, with exact camera/road
limits preferred. Preserve that provenance. Do not invent a numeric default or
assume units not established by the actual source contract. The effective limit
is nonempty only for speed-control cameras and can substitute a standard urban
limit when the camera limit is missing. Generalized POLICE tags do not prove a
mobile police patrol; MOBILE_CONTROL and POLICE_PATROL are distinct SDK tags.

## Activation gate

Do not enable the source toggle or automatic control until a supported source
has produced genuine camera/mobile enforcement payloads with coordinates,
identities, freshness, types and established limit units. Then connect a separate
Yandex policy/updater to source merging and retained-warning expiry. Default OFF,
own state, bounded requests, independent cancellation, no renewal of stale data.

An actual ARM64 UI/Params rebuild and the existing Panda/ICBM compatibility gate
are required when enabling the menu; source-only UI changes are insufficient.
Existing Waze/Panda binaries and control algorithms are unchanged by this stage.

## Verification

Linux Docker full camera/Waze/Yandex/ICBM offline suite: 1111 passed, 9 skipped
(before the final independent RAM-cache cap regression). Final Yandex suite:
50 passed. Ruff and Git whitespace checks pass. These checks do not establish
Navigator camera access or road readiness; there was no Comma deployment for
this inactive acquisition stage.


## Verified live SDK acquisition � 6 November 2026

The own bound key now obtains genuine route events through Full MapKit 4.45.0.
The initial route callback contained zero events. Explicit requestConditionsUpdate
followed by ConditionsListener produced fresh events; the client now waits for
that phase and records its completion separately from the initial route.

| Route | Events | SPEED_CONTROL | MOBILE_CONTROL |
| --- | ---: | ---: | ---: |
| MKAD | 41 | 35 | 0 |
| M4 near Krasnodar | 10 | 8 | 0 |
| Krasnodar to Rostov | 139 | 114 | 4 |
| Moscow to Voronezh | 184 | 155 | 1 |
| Rostov to Krasnodar | 118 | 99 | 5 |

All listed runs observed the requested conditions update and reported conditions
not outdated. Counts are time/route specific. POLICE is broad, not evidence of
patrols; POLICE_PATROL was not observed. Reverse routing returns a different set,
so future acquisition must consider both approaches for mobile events while
retaining the speed limit of the actual driven road. No completeness claim.

The SDK I18n canonicalSpeed/localizeSpeed formatter exposes camera and road
limits in kilometers per hour (including mobile limits such as 50/70/90/110).
Raw values remain preserved without an assumed input-unit conversion. The
probe uses no invented urban fallback and no NaviKit effective-limit API.

The checked-in count/hash index is docs/yandex_sdk_live_evidence.json; actual
SDK JSON and credentials remain outside Git. Android compile/lint/package and
signed-emulator execution passed. This does not implement a Linux SDK bridge:
Comma acquisition, expiry/merging and vehicle-control integration remain to be
implemented and validated before enabling the Yandex toggle.
