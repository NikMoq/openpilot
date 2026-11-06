# Yandex on Comma: native Linux source

## Current status — 6 November 2026

**CONFIRMED: offroad native acquisition on the user's Comma.** The coordinator's
bounded device checks reached `192.168.0.104` with offroad=true and used the
actual `/usr/local/venv/bin/python3`. Route GET and conditions POST ran directly
on Linux, without Android, a phone or an external relay.

| Check | Observed result |
| --- | --- |
| Direct native route + conditions | HTTP 200 + HTTP 200; 391 geometry points; 5 fixed speed-control events and 2 school events; limits 40/60 km/h |
| Extended native route | 1148 points; 33 fixed, 1 mobile and 6 warning events; limits 40/50/60/70/90 km/h |
| `NativeYandexProvider` forward/reverse window | complete=true, no errors; 6 fixed and 5 warning events |
| Real `YandexCameraUpdater` on the same Comma | finished=true; `ready: 12 warnings; 10 verified limits`; Params replaced by memory-only test storage |
| Installed package `4b46e03`, full mapd module import and native worker | package/ICBM guard PASS; finished=true; `ready: 8 warnings; 6 verified limits`; memory-only test Params |

These are separate, time/route-specific acquisitions; their counts must not be
combined or treated as an inventory. School events are decoded warnings, not
speed cameras. No control messages were published by the worker diagnostic.
Package `4b46e03` was installed by a clean fast-forward on the device's `waze`
branch. No manager restart or road test was performed. Restart Comma normally
to load the rebuilt UI and worker; the Yandex toggle defaults OFF. Installed
module and acquisition checks do not establish physical speed-control behavior.

Separately, 134 exact directed route-edge matches against Full MapKit SDK 4.45
had zero speed-limit differences, including 20/40/60 km/h. Raw native m/s values
are preserved and converted only under that verified contract. Linux Docker
policy/updater/real-wire control regression: 60 tests passed. Real-wire approach
reduction and proven-passage restoration are synthetic checks, not device control
execution.

## Field guide

The native worker reads its private client configuration from
`/data/media/0/osm/yandex/private/client.json`, with file mode `0600`. This
credential file stays outside Git. The dedicated **Yandex API key** row
(Russian: **Ключ Яндекса**) accepts the owner's replacement MapKit key through
a hidden input dialog. It displays only Configured / Not configured; saving
preserves the other installation fields and atomically replaces the file with
mode `0600`. An existing valid private installation is required.

Saving triggers credential metadata detection: old results are revoked and the
next position-valid tick starts a fresh request without the prior error backoff.
Configured describes local configuration, not server authorization; Connected
requires successful acquisition. Do not place keys in other UI fields, source
files, command-line arguments, public diagnostics or package manifests.

The source toggle is **Yandex Navigator** (Russian: **Яндекс.Навигатор**) in the
existing Cruise / Speed Limit settings, alongside Waze. It defaults OFF. Tap the
title to open the standard description: «Камеры контроля скорости, мобильные
камеры и полицейские посты.» The status/count row shows Off, Waiting for GPS,
Waiting for direction, Updating, Connected, partial update or source unavailable.
The ARM64 UI, Russian translation and CPython 3.12 Params binding have been
rebuilt. The native package guard and the existing ICBM integrity preflight
pass. The rebuilt Params binding also passed its real lifecycle check on Comma
using temporary storage; the system Params directory was not used by that test.

A fresh localized position and usable heading are required to acquire a window.
Refresh is scheduled after 60 seconds, 1 km of movement or a 30-degree heading
change, subject to the worker's request spacing and error backoff. Acquisition
and snapshots are bounded in RAM; no camera-event cache is persisted to disk.
Private client configuration is separate from event storage.

Mobile evidence expires after at most 180 seconds and fixed evidence after at
most 300 seconds from acquisition. Re-reading or merging never renews its age.
Source disable/expiry revokes retained warnings; expiry never asserts passage.
Unknown limits remain warning-only. Mobile cameras and actual police patrols
are not filtered by report-facing tags; directed road geometry and independently
verified road limits qualify each approach, including a uniquely matched nearby
opposite carriageway. A reverse-road limit is never substituted for the current
road's limit.

Generic `POLICE` alone is not proof of a police post: the official EventTag
reference defines it as an umbrella for traffic-code control and a compatibility
fallback. `MOBILE_CONTROL` and `POLICE_PATROL` are distinct enforcement tags.
[Official EventTag reference](https://yandex.ru/maps-api/docs/mapkit/com/yandex/mapkit/road_events/EventTag.html).

Linux verification after the final native worker fixes: 369 Yandex tests pass.
The wider camera/Waze/ICBM regression passed 1408 tests and skipped 9; its four
stale-package failures were then resolved by the rebuilt native package, with
26 package and release checks passing separately. Preserve the Panda/daemon
artifacts and integrity checks: only the Params extension hash pin changes with
its rebuilt binding. No manager restart, firmware change or road test was
performed.

---

The following notes record earlier research stages. Their unresolved gaps and
inactive-source statements describe those stages, not the current status.

# Historical research notes — superseded by current status above

Current requirement, 2026-11-06: autonomous acquisition on Comma Linux, with no
Android runtime, phone or remote Android relay. Android execution was stopped;
the exploratory ARM64 APK changes were reverted. No Android image/runtime was
installed on Comma. Existing historical SDK diagnostics remain evidence only.

## Verified work

`sunnypilot/mapd/yandex/sdk_snapshot.py` is a pure Python, offline import boundary
for the previously obtained SDK snapshots, not a native network implementation.
It verifies source/schema/build, request identity, observation/completion times,
confirmed conditions update, geometry, event position, tag identities and limits.
It accepts SDK formatter observations only when their raw value matches and their
unit is explicitly kilometers per hour. It never assumes raw field units.

SPEED_CONTROL identifies supported fixed control; MOBILE_CONTROL and
POLICE_PATROL identify mobile enforcement. Generic POLICE alone does not.
Mobile warning direction is unrestricted, but its route limit remains applicable
only to the observed route direction. No limit from an opposite carriageway is
substituted. Local expiry caps are 180 seconds for mobile and 300 seconds for
fixed observations; these are conservative import policy, not claimed vendor TTL.
Expiry preserves observation time, including when a file is read again.

All records explicitly remain ineligible for automatic control. The module does
not enable a toggle, publish mapd events, or change the driver's speed target.

Linux Docker verification: 87 tests passed across the snapshot importer and
existing Yandex backend/tile tests. Offline replay of historical SDK responses,
using their original completion times, accepted 35 MKAD, 118 Krasnodar–Rostov
(4 mobile), and 156 Moscow–Voronezh (1 mobile) enforcement records. At original
completion time plus 300001 ms every imported record expired. This replay is
not a new live acquisition or coverage test.

## Actual device preflight

Read-only WinSCP CLI checks reached the user's Comma, with IsOnroad=0 and
IsOffroad=1. Observed Linux 4.9.103, aarch64, 3692952 KiB total RAM and 2324424 KiB
available at that instant. Binder/ashmem nodes and kernel support are present;
KVM, binderfs, app_process, Docker, runc and lxc-start were not present in the
checked paths. These observations do not authorize or implement Android: that
architecture is now explicitly excluded. No launcher, manager, Panda, CAN,
firmware, Params or device files were changed by these checks.

## Native acquisition gap

The working Android API callback snapshots are not captures of network protobuf
envelopes. A Linux client cannot submit them as request bodies or assume their
field names/numbers match the server protocol. The initial SDK route contained
zero events; a separate conditions update produced the camera payload. A native
client therefore needs both route acquisition and its bound conditions exchange.

Static analysis now confirms GET for the route path and POST for the conditions
path, with a serialized protobuf body and application/x-protobuf content type.
The service base URL is configuration supplied; a production host was not
established from these code paths alone.

The shared header builder computes X-YRuntime-Signature using HMAC-SHA1 and an
embedded 16-byte key. The owner separately authorized using that signing material
on 2026-11-06. It was then used only in bounded Linux diagnostics and was not
exported, logged or checked into Git. This does not extend to firmware keys.
The generic GET
and POST dispatch paths are consistent with using that builder, although one
intermediate virtual/thread dispatch remains unresolved. This does not prove
the server's exact signature requirement or authorize borrowing credentials.

One bounded normal-TLS Linux Docker GET with the owner's known-working MapKit
API key header still returned HTTP403, 10 bytes, SHA-256
`c029d1c560ecb35bbb632f9f7102907b41a289e258055d477f92e21884f9ab79`.
The request did not implement the complete SDK wire/authentication contract;
it does not prove that the owner's key is invalid. No key was embedded in the
script, passed in command-line arguments or exported in the result.

Remaining gaps: full service/request contract, normal own-key/application
authorization, semantic population and route binding of the conditions request,
response root/error/compression formats, native event and speed field semantics.
No borrowed application key, identity rotation or certificate bypass is used.

Offline Linux inspection of the official SDK ARM64 ELF (SHA-256
`126607c7824398ca365c1e3eba1718d92e6bf7abeda419c610442b54d3e73f9d`)
found the type name `conditions_request.ConditionsRequest` at file offset and
virtual address `0x2a9ad0`, and `route_events.RouteEvents` at `0x297dfb`.
Neither `conditions_request.proto` nor `route_events.proto` occurs in that
artifact. This narrow check recovered no field numbers/types or speed units;
it does not exclude differently named, packed descriptors or generated parsers.
Subsequent generated-parser/serializer analysis recovered some actual field
numbers and wire types. See [the raw contract](yandex_native_wire_contract.json).
The dependency-free native_codec.py now encodes/decodes the confirmed raw shapes
of ConditionsRequest, RouteRepresentation, SpeedLimit and SpeedLimits. Polyline
and representation field2 remain opaque; field3 has no invented semantic enum;
speed fields have no asserted units. The module neither authenticates HTTP nor
turns a decoded numeric value into a control limit.

The combined Yandex Linux Docker suite now passes 159 tests, including 27 fixed
synthetic native-wire cases. The native wire codec has not decoded an actual
server conditions response; its confirmed component schemas are not a claimed
complete network envelope.

Further verified native components:

* `native_events.py` decodes the raw Conditions -> RouteEvents -> SegmentEvent
  chain with aggregate budgets and unknown-number tracking. Point, tag mapping,
  field semantics and speed units remain unqualified for automatic control.
* `native_auth.py` provides HMAC-SHA1 for the exact prepared request URL/body,
  with a caller-supplied key and no embedded credentials. The signed input is
  User-Agent + METHOD + space + path/query + body, excluding URL authority.
  Synthetic GET/POST cases reached the original ARM64 HMAC call in Linux Unicorn;
  modeled standard-string helpers produced the same input as the Python formula.
  This is instruction-level verification, not an Android runtime or full SDK run.
* Signed diagnostics to the historical driving/2.x route path still returned
  403. Sorting parameters and using the same existing egress proxy did not
  establish access. One network timeout was followed by a bounded same-request
  diagnostic, which again returned 403; timeout was not treated as success.
* A single persistent Linux installation identity was created for the SDK's
  confirmed deviceid/uuid fields; it is reused, not rotated on errors. These
  locally generated identifiers are not asserted equivalent to SDK-issued miid.
* Current SDK service URLs are configuration-driven. Its config GET appends
  a runtime prefix and /config/2.x/; its init worker uses /init/2.x/random and
  /init/2.x/miid. Exact normal bootstrap and current driving prefix remain under
  investigation. The signed legacy-path tests are not complete current-SDK
  request reproductions and do not isolate the reason for server rejection.

The public HTTP Routing API documents geometry/travel times and road flags, but
does not document a camera/mobile-event response contract. It is not treated as
an equivalent camera source. The existing public accident/closure tile backend
also remains separate and does not supply missing enforcement data.

## Completion criteria

1. A bounded Linux request with the owner's valid credentials obtains a genuine
   route and a confirmed conditions payload, with no Android execution.
2. The native decoder validates event identities/coordinates/types/freshness and
   limits independently of historical SDK snapshot field assumptions.
3. A cancellable worker consumes current localized position without blocking
   mapd; requests and cache size are bounded, stale data is not renewed.
4. Independent Yandex freshness/merge/retention checks feed the existing camera
   selection and speed resolver. Mobile events cover both approaches while the
   limit belongs to the actual driven road.
5. Linux end-to-end replay proves approach reduction, actual passage restoration,
   driver override, expiry/failure handling and source disable behavior. Expiry
   alone must not assert that the vehicle passed a camera.
6. The actual ARM64 UI/Params and existing Panda package compatibility checks
   pass before publishing an enabled device package. No road readiness claim.

References:

* [MapKit supported platforms](https://yandex.ru/maps-api/docs/mapkit/index.html)
* [DrivingRoute and conditions update](https://yandex.com/maps-api/docs/mapkit/com/yandex/mapkit/directions/driving/DrivingRoute.html)
* [HTTP Routing API response](https://yandex.ru/maps-api/docs/router-api/response.html)
