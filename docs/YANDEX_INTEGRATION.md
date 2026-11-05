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
