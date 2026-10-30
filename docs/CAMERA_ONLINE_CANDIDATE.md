# GitHub cam installation candidate

This branch includes the validated AArch64 UI and CPython 3.12 Params binding
at their native tracked runtime paths. The original tracked `prebuilt` marker
is retained, so the stock launcher does not rebuild on the device.

Source implementation: 5531981edfcc743ed813c7104904ce00b3752b63.
UI SHA-256: 76223afc43f6895bae02bfc1ac13aac02fbc003619d7d129a786938781371edc.
Params SHA-256: 07bb1204a587a9c4be8f8715045408065b01a5f0fb10a3b6b595e8f4cab66464.

The earlier offline validation report describes the source acceptance stage;
the tracked binaries are now replaced specifically for stock Git branch updates.
See CAMERA_NATIVE_UI_BUILD_RESULTS.json and CAMERA_PARAMS_BINDING.md for ABI
and provenance. No Panda firmware or safety changes are included.

Installation alone does not supply regional data. Camera control requires a
local camera database and a fresh unfiltered OSM road graph with its acquisition
manifest. See CAMERA_OFFLINE_DATA_CONTRACT.md. A missing graph prevents camera
acquisition; do not interpret an inactive sign as a completed camera test.

Read-only diagnostics should first verify branch/commit, process startup,
SpeedCameraRoadGraphStatus and database freshness. The planner emits
`speed_target_diagnostic` once per second with candidate targets, camera event,
SLA and ICBM state. Preserve the rlog for a complete runtime review.

Status: publication candidate, hardware validation not performed. Simulation
and ABI checks do not establish road readiness. Initial startup and data checks
must be completed while parked before considering further testing.
