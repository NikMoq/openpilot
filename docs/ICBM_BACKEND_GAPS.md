# PQ ICBM backend source candidate

Source baseline: stable 3b9179406c4a43319af3383157fc83e1098cba4d.
Backend source donor: aabf70a (local development commit).
Status: source integration only; device and ECU acceptance UNKNOWN.

## Included

PQ controller, raw TX-return observer, one outstanding transaction, stock feedback,
freshness guards, native Panda command/status transport, Cereal schema and services.
Native Params key PQ35IcbmExpectedPandaSignature is declared; no value provisioned.
Existing stable torque values and safety headers are retained.
The firmware and daemon binaries in Git are unchanged. The Python wrapper now performs optional release preflight before hardware access.
Without a release manifest, preflight revokes stale ICBM identity and preserves stock wrapper behavior; no deployment manifest is committed.

## Offline build result (2026-10-28)

CONFIRMED: fresh ARM64 QCOM2 native daemon compiled and linked offline using
`tools/icbm_build/build_aarch64_pandad.sh`, with generated Capn Proto/KJ 0.7
schemas. Real Panda codec protocol test passed in ARM64 emulation; `ldd -r`
resolved every symbol in the build container. Final daemon SHA-256:
`4a7153f4c8046af32906c1a4d2846234baa70dc95d7b2d27f6b1a3180d6dd875`.
The latest Params header, including SpeedCameraSpeedOffsetKph, was restaged
and params.cc rebuilt before final link. Header SHA-256:
`2d3cffc4a92fbb1267d6dd4eff42f82d8b6ff12ac509c3968ba4bee3acb14d80`.
Artifact and validation log remain outside Git in the outer workspace
`workspace/results/icbm_stable_port_build`. This does not establish device
runtime, firmware pairing or physical ECU acceptance. Stable binaries remain
unchanged. Direct compilation works without restoring root SConstruct; the
full cached-source hashes and commands are recorded in tools/icbm_build.

## Required release dependencies

1. Use the hash-pinned direct recipe in tools/icbm_build. Root SConstruct and
   cereal/SConscript are absent but not required by this recipe. Linux/ARM64
   toolchain, libusb, Capn Proto, Cereal messaging/common sources and Panda
   headers are required; the tested cache supplies the missing native sources.
2. Regenerate custom.capnp C++ bindings and rebuild dependent Cereal/Python/native
   modules. Existing prebuilt bindings do not implement speedUnit, command/status,
   LongitudinalPlanSP.speedCameraControl, or LiveMapDataSP camera fields.
3. Build and verify the native ARM64 pandad from these sources, including its
   protocol test. No rebuilt daemon is included. The checked-in stable binary
   does not gain this protocol from Python source edits.
4. A separately reviewed, matched firmware implementing version-1 A9/AA button
   command/status and correlated forwarding/release is required. No firmware,
   calibration changes, signatures, keys, or firmware activation are supplied.
5. Pairing identity must be provisioned from independently verified release
   artifacts. The new backend remains inactive without a valid matching signature
   and protocol status. Device installation requires its separate authorized task.

## Schema compatibility

New camera fields append ordinals: LongitudinalPlanSP @8; LiveMapDataSP @6..21.
Existing stable field ordinals and struct IDs are preserved. New speedUnit @3
has unknown=0 so old publishers cannot silently authorize a target unit.
CustomReserved10/11 retain existing struct IDs and add command/status fields.
Source schema compatibility does not update precompiled consumers or supply a
camera data producer. Planner/camera production integration is tracked separately.

## Validation and remaining evidence

Twelve affected backend Python sources parse successfully. The native build and protocol check above passed; no
physical test was performed. Fresh stock feedback and TX-return are not ECU
application acknowledgements. Exact button acceptance, pedal interruption,
manual RES after brake and restoration of base speed require separately scoped
physical validation before any readiness claim.

## Native Python pipeline verification

On 2026-10-28, all 18 opt-in test_camera_runtime_integration.py checks passed
in isolated pq35-camera-runtime:/icbm-port using actual compiled x86_64 native
msgq and Params (Python3.12), current Cereal schema and current planner sources.
Command: PQ35_RUNTIME_SMOKE=1 PARAMS_ROOT=/data/params
PYTHONPATH=/icbm-port:/icbm-port/opendbc_repo:/icbm-port/msgq_repo
python -m pytest -q -o addopts= --confcutdir=tools/tests
tools/tests/test_camera_runtime_integration.py. Three unrelated pytest config
options produced warnings. No Params, sockets or modules were mocked.

The stable distribution lacked usable x86 extensions. Native sources came from
the hash-pinned build foundation, while params_keys.h and pyx/schema/planner
inputs came from the current port. Native Cereal C++ was regenerated with the
container compiler (1.1) to match installed headers. Moving the ModelRunner
import into get_model_path avoids loading an unrelated model execution backend
while reading model bundle metadata. Model path callers retain the same import.

Outer workspace results/icbm_stable_port_build contains runtime_smoke.log,
runtime_native_build.log and runtime_inputs.sha256. /runtime remained untouched.
This verifies map publication, real msgq decoding and planner integration in
the test scope; it does not run model inference, an MPC solver or vehicle ECU.

## Event envelope validity follow-up

Production process_icbm_command now requires both command Event.valid and
payload.valid. Native bridge regression sends Event.valid=false while payload
valid=true and verifies no A9 write. Actual bridge harness passed after the fix
with real Cereal/native msgq and mock transport only.
A full fresh ARM64 production rebuild (all 23 objects, no object reuse) used
current source and regenerated Capn Proto0.7 Cereal, then linked in ARM64
Ubuntu20.04. ldd-r resolves every symbol. Final production daemon SHA-256:
4a7153f4c8046af32906c1a4d2846234baa70dc95d7b2d27f6b1a3180d6dd875
This supersedes the earlier 5087d67c candidate. Output remains outside Git.
Evidence: outer workspace results/icbm_stable_port_build/
event_valid_arm_{inputs.json,build.log,validation.log}, plus refreshed
native_bridge_{inputs.sha256,build.log,validation.log}.
The fresh schema transfer includes cereal/include/c++.capnp. Staging normalizes
shell scripts to LF before execution, supporting Windows source checkouts.
