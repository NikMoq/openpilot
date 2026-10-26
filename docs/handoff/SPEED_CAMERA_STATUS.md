# Speed Camera / SLC status

## Scope

This document records the current `dev` implementation for the PQ35 speed-camera
path. It is not a statement of flash readiness, bench readiness, or road
readiness.

## Confirmed APK evidence

- Artifact: `C:\Users\МИХАИЛ\Downloads\58-0.apk`
- SHA-256: `7047b236946fedcbe0da169273cc602c1e6c36146eaaa7d5d06b9a0c5db1b94f`
- Embedded database: `assets/database/RU.json`
- Embedded schema: `{meta, objects[]}`; objects use `lat`, `lng`, `dir`, and
  `speedControls[].speed`.
- Observed export endpoint in APK strings:
  `https://radarbase.info/export/cache/RU/main_extended.json`
- A sample parse of the embedded RU database produced 35,067 normalized
  camera records.

The endpoint and database ownership/licensing are external dependencies. The
runtime URL is configurable through Params and must be reviewed before a
deployment is distributed.

## Implemented on `dev`

- Versioned, atomic local cache with stale-cache protection.
- Configurable URL, version, path, and refresh period.
- Radarbase parser plus generic JSON/GeoJSON compatibility.
- GPS/distance/heading camera selection.
- `liveMapDataSP` camera fields and existing map speed-limit path.
- HUD indicator showing camera limit and distance.
- Existing SunnyPilot SLC remains the owner of target-speed calculation; PQ35
  ICBM remains the existing actuator path.

## Verification evidence

- Embedded APK schema smoke test passed.
- Replay fixture: `sunnypilot/mapd/tests/fixtures/radarbase_sample.json`.
- Replay selection test passed for a camera 33 m ahead at 60 km/h.
- Python syntax compilation passed for changed Python modules.
- Cap'n Proto C++ bindings were regenerated from `cereal/custom.capnp` in
  commit `9c70f61`; the generated header contains the four speed-camera fields.
- Full pytest is not yet runnable in the current Windows checkout because the
  repository's symlink-style `openpilot/common` layout is not resolved by the
  local interpreter (`ModuleNotFoundError: openpilot.common`).

## Remaining blockers

1. Run the unit suite and compile/runtime validation in a Linux/device-like
   checkout, including the regenerated cereal bindings and a live message.
2. Verify a live `liveMapDataSP` message from mapd using a recorded GPS replay.
3. On a controlled stationary bench, verify that an SLC target change produces
   the expected PQ35 ICBM/ACC button frames and that the vehicle controller
   accepts them. No flash or road test is authorized by this document.
4. Confirm camera direction semantics (`dirType`) against recorded data before
   widening matching beyond the current conservative heading filter.
5. Review the external Radarbase terms and availability before enabling the
   default endpoint in a distributable build.

## Next controlled bench evidence

Use a stationary, safe setup with the vehicle secured and no public-road
operation. Capture a short before/after trace while publishing a synthetic
`liveMapDataSP` camera event:

1. Confirm `speedCameraAheadValid`, `speedCameraAheadDistance`,
   `speedCameraSpeedLimit`, and `speedCameraType` on the subscriber side.
2. Confirm the existing SLC resolver selects the map source and produces the
   expected target speed without changing Panda safety limits.
3. Confirm the existing PQ35 IBCM layer emits only the already-supported ACC
   button frames when the target is changed; record CAN ID, bus, counter, and
   checksum from the trace.
4. Confirm clearing the camera event restores the prior map/car speed-limit
   behavior and does not leave a stale target.

This evidence must be reviewed before any hardware installation or road test.

## DragonPilot reference rule

DragonPilot remains a reference for user-visible behavior and update flow. Its
behavior is not treated as proof of the APK endpoint or of PQ35 CAN semantics;
those require independent evidence.
