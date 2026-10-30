# Native camera UI integration

Status: **OFFLINE_NATIVE_UI_VALIDATED**. Full host Qt UI, ARM Qt UI with device graphics SDK, and actual native widget assertions/render QA passed. Original tracked `selfdrive/ui/ui` is preserved. Build outputs stay under ignored `.native-build/` or in isolated build containers, and are not installed.

## Source authorization and provenance

The original `cam` base is `5e049f491ac36a9e6efb9ee31e4871b0f1408a32`. Its launcher `system/manager/process_config.py:129` executes native `selfdrive/ui/ui`; line130 disables the Python raylib UI. Original ELF SHA-256: `c7e674a43cb07fda5f0de20fe72fae2027645bee2d0fe95a999f2fc5e7ff0ba6` (34,600,544 bytes, AArch64).

Original HEAD and its own ancestry contain no native Qt sources. The user subsequently explicitly identified and authorized the full native source in `NikMoq/openpilot` branch `dev`. It was fetched without switching `cam`: exact commit `aabf70ac1f211c343cca0181f77777dd12c1ff6e`. The source has matching `HudRendererSP::drawSpeedLimitSigns`, `AnnotatedCameraWidgetSP`, `SpeedLimitSettings` symbols, native source paths, and a `custom.capnp` identical to the original base. This establishes source/API compatibility, not bit-identical reconstruction of the original executable.

`CAMERA_NATIVE_UI_SOURCE_IMPORT.json` records every imported blob and its pre-edit SHA-256. Only missing native UI translation units selected by original `compile_commands.json`, transitive quoted headers, missing common/msgq native dependencies, and the authorized dev isolated UI builder were imported. Existing files were preserved. No controls, opendbc, Panda, pandad, donor compiled objects or generated headers were imported. The dev README's upstream-source history is informational; all actual imported bytes came directly from the explicitly authorized dev commit.

## Same native sign

`HudRendererSP` consumes `longitudinalPlanSP.speedCameraControl` event identity, rawLimit and appliedOffset. In effective camera mode it never reads a nearest warning sign or restores cached OSM limits. It retains the original native sign rectangle and existing offset rectangle. The countdown appears on the upper-left corner, opposite the unchanged offset above-right. Its width fits `1000 m` and its left edge is kept clear of the unchanged set-speed box. No camera overlay or upcoming OSM sign is drawn in camera mode. Authorized dev warning-only display remains available outside camera mode.

The sign is visible during approach, active control, driver override and trustworthy retained GPS-loss hold. Native offset remains the single offset applied by controls, including a conservative retained target. Proven passage/end/disable clears the event sign. Disabling while the last plan is still a camera plan hides it until a fresh road-mode plan arrives. Transport-stale plans hide the sign. GPS-loss hold hides the countdown and displays Camera data unavailable. Manual input awaiting stock ACC feedback displays Waiting for ACC response; accepted override displays Driver override.

`selfdrive/ui/sunnypilot/camera_countdown.h` tracks remaining distance for the same event. It starts within configured50..1000m threshold in50m steps, displays actual distance if a sample skips the exact boundary, decreases without GPS jitter increases, and clears stale/invalid/passed events. `drawCameraCountdown` uses the actual unchanged native offset rectangle as the opposite-side anchor.

## Native settings and messages

The existing Cruise / Speed Limit settings widget has Camera Speed Control and Camera Reaction Distance (default300m,50..1000m, step50). Missing/malformed/out-of-range/non-step values are normalized to300 before constructing its real Qt selector. The standard native SpeedLimitOffsetType/SpeedLimitValueOffset controls remain; the independent dev Camera Speed Offset selector is removed. Camera source mode is independent of the ordinary road SpeedLimitMode and warning preference; toggling camera control does not write those preferences. Effective source state comes from the planner. Road matching status translates protocol diagnostics into actionable UI text.

Append-only SpeedCameraControl fields: @6 driverSetSpeed(m/s), @7 eventId, @8 distance(m), @9 countdownValid, @10 reactionDistance(m), @11 rawLimit(m/s), @12 appliedOffset(m/s). Existing state/reason/manualTakeover are reused. LiveMapDataSP additions @22..26 carry warning/control IDs, event-bound passage proof, source state and reason. No resolver fields were duplicated. All native consumers are compiled against freshly generated schema headers.

## Build isolation and validation

Host: fresh official Ubuntu22.04 toolchain from `tools/ui_build/Dockerfile.camera-host`, Qt5.15.3 / clang14. Application source is a fresh snapshot of current `cam` under `/work` in `pq35-cam-native-host`.

ARM: existing installed image `pq35-ui-ru-env:local` supplies only compiler/system dependencies (clang10, host MOC5.12.8 and `/opt/sysroot` Qt5.12.8/AArch64 runtime). Application source is a clean `cam` snapshot under `/cam-build` in `pq35-cam-native-arm`. No old `/work` source, old application objects, application binary or generated schema was used. Device vendor/runtime compatibility must be assessed from the candidate's ELF dependencies; no candidate is installed by the builder.

The native builder regenerates Cap'n Proto headers, MOC, translations and resources. Its source-generated schema PCH is architecture-specific and built inside the isolated output directory. Build manifests include authorized dev commit, current base, actual compiled-source hashes, Qt version and resulting executable SHA-256.

Reproduce host native widget QA:

```sh
python3 tools/ui_build/build.py --preview -j4
QT_QPA_PLATFORM=offscreen .native-build/pc/camera-preview /tmp/camera-native-qa
python3 tools/ui_build/build.py -j4
```

`tools/ui_build/preview.cc` uses the real native sign painter and SpeedLimitSettings widget. Assertions cover camera60+native15, same-event300->200, no cached OSM20 afterpass, stale/off transitions, manual pending/accepted labels, native toggle persistence independent of road-modeOFF/warningsOFF, default300 for empty/malformed values, both valid endpoints, and actual +/- clicks across every50m step/bounds. Screenshots are actual Qt output on synthetic background, not vehicle captures. `tools/tests/test_camera_countdown_native.cc` additionally covers every threshold, jitter, NaN/infinity and geometry anchoring. These checks establish source/UI behavior, not vehicle ACC acceptance or road readiness.


## Final offline artifacts

`CAMERA_NATIVE_UI_BUILD_RESULTS.json` records exact hashes and checks. ARM candidate: `.native-build/camera-native-arm/ui`, SHA-256 `76223afc43f6895bae02bfc1ac13aac02fbc003619d7d129a786938781371edc`. Host full UI: `.native-build/camera-native-host/ui`, SHA-256 `d87fa8955b9fc436a1c8d26e1b0dacccfe3d1ebd8fe5bf2454bbe8ac9b0a4561`. Build/source manifests accompany both.

Device graphics linkage uses only the explicitly allowed cached system DSOs from `pq35-ui-ru:/work/.native-build/vendor`. `CAMERA_NATIVE_VENDOR_SDK.json` records all eight AArch64 SONAMEs and SHA-256 values. The complete ELF link closure against the SDK resolves65 AArch64 files with no missing dependencies; final candidate requires Adreno EGL/GLES and `libOpenCL.so`, not generic Mesa. Maximum GLIBC requirement is2.29, compared with original UI's2.38. This is an offline link/dependency check, not device runtime execution.

Actual Qt screenshots under `.native-build/camera-native-qa/` show the same native sign20/offset15 and opposite countdown at1000/300/50m, camera60+15, manual pending/accepted labels, data-loss hold, passage clearing, real below-ACC-minimum20+0, and real settings +/- values. A raw20+15 target35 has no below-minimum marker. The UI marker only describes the unchanged ICBM minimum30km/h/20mph; it does not alter control policy, offset, limits or braking.


## Latest camera button policy

The user's subsequent requirement supersedes the earlier manual-override UI
contract described above. In-zone ACC button actions no longer latch a camera
Driver override or replace the saved pre-camera speed. Native HUD no longer
renders the legacy Driver override / Waiting for ACC response labels for camera
input. The active limit sign/countdown and short empty/GPS labels remain.
Pedals and held buttons still pause actual ICBM transmission via its existing
readiness checks; there is no automatic ACC engagement after braking.
See CAMERA_EVENT_LIFECYCLE_QA.json for the new regression and artifact evidence.
