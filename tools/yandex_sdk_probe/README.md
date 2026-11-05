# Full MapKit route diagnostic

`RouteProbe.java` is a source-only diagnostic for the official Android
`com.yandex.android:maps.mobile:4.45.0-full` SDK. It is not wired into mapd,
vehicle controls, an Activity, or a server. It does not implement a Linux SDK.

An Android harness with `INTERNET` and `ACCESS_NETWORK_STATE` permissions must
supply its own legitimately issued MapKit API key, an application Context, and
two explicit latitude/longitude `Point`s. The harness must have no existing
MapKit initialization or other SDK lifecycle users: this probe sets the key,
initializes MapKit, calls `onStart`, and calls `onStop` when it finishes. Call
`RouteProbe.start(context, ownKey, from, to, 45000, callback)` on the main
thread and retain the returned probe until the callback. `cancel()` also runs
on the main thread. This is a chosen-coordinate request, not a GNSS test.

There is exactly one ONLINE router request, with two WAYPOINTs and one requested
route, no retries, and a 1–60 second main-looper timeout. Keep the looper running;
it is not a hard process-level deadline. Snapshot generation rejects oversized
results rather than truncating. The caller owns consent and storage of the
result. This class never logs or writes the key; normal SDK internals receive
the key for their supported initialization and request authentication.

The success callback receives JSON with SDK/source identity, observation time,
`conditions_outdated`, route geometry as `[latitude,longitude]`, original
per-segment speed-limit values, and events with original ID, location, tag names
and SDK enum ordinals, raw nullable camera speed and polyline position. Ordinals
describe this SDK enum, not an asserted wire protocol. **All speed units remain
`unknown`; no conversion, city-speed substitute, TTL, direction or control limit
is invented.** Raw event descriptions are deliberately omitted. A route result
does not prove a complete camera inventory. The error callback exposes only a
fixed category and exception/SDK error class, never error text or credentials.

Full SDK metadata contains `DrivingRoute.getEvents()`, `getSpeedLimits()` and
`Event.getSpeedLimit()`; NaviKit-only `UpcomingRoadEvent` is not used. Actual
server access and populated camera data still require a legitimate-key runtime
test. Compilation against SDK classes is not an Android execution test.

References: [official initialization](https://yandex.com/maps-api/docs/mapkit/android/generated/getting_started.html),
[DrivingRoute](https://yandex.com/maps-api/docs/mapkit/com/yandex/mapkit/directions/driving/DrivingRoute.html),
[Event](https://yandex.com/maps-api/docs/mapkit/com/yandex/mapkit/directions/driving/Event.html).

Compile outside the checkout using JDK 21 with `javac --release 8`, the official
SDK `classes.jar`, and Android compile stubs plus `org.json` if not in those
stubs. Output must go to an external temporary directory. No APK, signing key,
runtime key, SDK binary, or build cache belongs in this directory.

Compilation checked with JDK 21, `--release 8`, official `4.45.0-full`
`classes.jar`, Maven `com.google.android:android:4.1.1.4` compile stubs, and
`com.vaadin.external.google:android-json:0.0.20131108.vaadin1` for Android's
checked `JSONException` API. All downloaded inputs and `.class` outputs remain
outside the checkout. No SDK runtime or live request was executed; no caller key
was available. These old compile stubs do not claim current SDK OS compatibility.

The actual official AAR manifest requires Android API 26 or later and OpenGL ES
3.0. Its library manifest also requests several Android permissions; a future
harness must review the merged manifest rather than treating compile stubs as a
runtime manifest. This probe does not request location permission or inject GPS.
