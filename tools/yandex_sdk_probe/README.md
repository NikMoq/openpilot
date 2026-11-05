# Full MapKit route diagnostic

This independent Android test application uses the official
`com.yandex.android:maps.mobile:4.45.0-full` SDK. It is not connected to mapd,
vehicle controls, a server or the Comma installation, and does not implement a
native Linux SDK.

## Build

Build a copy of this directory outside the source checkout with AGP 8.9.1,
Gradle 8.14, JDK 21 and Android SDK platform 36/build-tools 35.0.0. Set `sdk.dir`
in an external `local.properties`, then run `assembleRelease`. Google Maven and
Maven Central resolve the official SDK and its transitive dependencies.
The package targets the isolated x86_64 emulator, requires Android API 26+ and
uses the SDK AAR's manifest/resources/native library. Its manifest requires
INTERNET and ACCESS_NETWORK_STATE; review the merged SDK permissions too.

The diagnostic release variant is debuggable for `run-as`, but unsigned; the
debug variant is disabled, preventing automatic debug-keystore generation.
Normal Android installation requires a signed package. The user separately
authorized one disposable signing key for the isolated emulator test. Signing
material, SDK credentials, APKs, AVD and build outputs never belong in Git.
See `.gitignore` and `docs/yandex_sdk_contract.json` for dependency provenance.

## Run

Only use the explicitly selected owned emulator, not an attached user device.
Before launching, provide its own legitimate MapKit key in app-private
`files/mapkit.key`, owned by the app UID, mode 0600. The Activity checks owner,
size and file type without following symlinks. Never put the key in source,
Gradle properties, APKs, Intent extras, command-line arguments or logs.

Force-stop only this test package before each independent run, retaining its
app data. Remove the previous private result and require a new nonsecret
`requestId` (1-64 ASCII letters/digits/underscore/hyphen). Reject absent or
mismatched result IDs; every result includes a completion timestamp.

Provide four explicit coordinate strings with adb `--es`: `fromLatitude`,
`fromLongitude`, `toLatitude`, `toLongitude`. Double extras from a Java caller
are also accepted; `am start` has no Double-extra switch and `--ef` is Float.
Optional long `timeoutMillis` defaults to 45000 and is bounded to 1000-60000.
The normal SDK route request is ONLINE, two WAYPOINTs, one requested route,
without application-level retries. Keep the Android main looper running and
retain the probe/session/listener. Its timeout is not a process-level hard
network deadline. The Activity atomically writes owner-0600 `files/result.json`
and finishes. Retrieve only that result, not general logcat.

Default mode has no map widgets or location injection. Optional
`--ez withMapView true` runs an A/B case with a normal MapView inside the
headless emulator, with the same coordinates and normal SDK lifecycle.
It is not asserted to be required for Directions or to fix authorization.
Optional `--ez networkOnly true` performs two fixed HTTPS checks without reading
the key or initializing SDK. Redirects are off; ordinary TLS validation remains.
It exports only hosts/statuses or exception cause-class names.

## Output and evidence limits

Successful SDK snapshots contain source/SDK identity, original route geometry
as `[latitude,longitude]`, conditions-outdated flag, original per-segment limit
values, event IDs/locations/tags and SDK enum ordinals, nullable camera limits
and polyline positions. Ordinals are not claimed wire enum values. All speed
units remain unknown until established from actual source evidence. No numeric
city fallback, TTL, camera direction, completeness or control eligibility is
invented. Event descriptions are omitted; oversized snapshots are rejected.

The official SDK logging API supplies transient messages to a bounded classifier.
Only fixed hint/HTTP-code counters reach `sdk_diagnostics`; messages, scope,
verbose info, headers, tokens and server text are never exported or saved.
Hints are not a complete server rejection reason. Errors otherwise expose only
fixed categories and exception/SDK error class names.

## Executed status

The Android APK build and signature verification passed. The signed package was
installed in an isolated API-26 x86_64 emulator with an own active key. Route
acquisition timed out, with forbidden/auth_rejected/api_key_mentioned/invalid_key
hints. Ordinary Android HTTPS completed. No actual camera/limit payload has yet
been obtained. The MapView A/B ended in Activity cancellation after key-related
hints, so it is not a completed routing or camera-coverage test. See
`docs/YANDEX_INTEGRATION.md` for detailed evidence and remaining authorization
questions. Nothing was deployed to Comma or enabled for vehicle control.

References: [initialization](https://yandex.com/maps-api/docs/mapkit/android/generated/getting_started.html),
[DrivingRoute](https://yandex.com/maps-api/docs/mapkit/com/yandex/mapkit/directions/driving/DrivingRoute.html),
[Event](https://yandex.com/maps-api/docs/mapkit/com/yandex/mapkit/directions/driving/Event.html).
