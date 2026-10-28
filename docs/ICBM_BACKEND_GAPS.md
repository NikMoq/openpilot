# PQ ICBM backend source candidate

Source baseline: stable 3b9179406c4a43319af3383157fc83e1098cba4d.
Backend source donor: aabf70a (local development commit).
Status: source integration only; device and ECU acceptance UNKNOWN.

## Included

PQ controller, raw TX-return observer, one outstanding transaction, stock feedback,
freshness guards, native Panda command/status transport, Cereal schema and services.
Native Params key PQ35IcbmExpectedPandaSignature is declared; no value provisioned.
Existing stable torque values and safety headers are retained.
The stable pandad Python launcher, firmware binary and daemon binary are unchanged.
The release verification helper is inert: no wrapper import/call or release manifest.

## Required build dependencies

1. Restore the matching full source build graph: this stable distribution has no
   root SConstruct or cereal/SConscript. Added selfdrive/pandad/SConscript alone
   cannot build the complete runtime. Linux/ARM64 toolchain and libusb, Capn Proto,
   Cereal messaging/common libraries and existing Panda headers are required.
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

Twelve affected backend Python sources parse successfully. No native build or
physical test was performed. Fresh stock feedback and TX-return are not ECU
application acknowledgements. Exact button acceptance, pedal interruption,
manual RES after brake and restoration of base speed require separately scoped
physical validation before any readiness claim.
