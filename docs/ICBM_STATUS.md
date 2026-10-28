# ICBM PQ35/PQ46 integration status

Status: SOURCE_CANDIDATE; task incomplete. Base: stable 3b9179406c4a43319af3383157fc83e1098cba4d. Donor: aabf70a.

The Hyundai reference reaches the planner target using stock ACC RES/SET button actions. PQ requires native coarse UP/DOWN and fine RES/SET, fresh stock feedback, single transaction ownership, release tracking and driver/pedal priority. This branch ports those source layers and preserves stable safety limits.

Validation on 2026-10-28: 333 focused tests passed in pq35-camera-runtime Docker, covering selector, echo, transaction, pairing, coordinator, Card runtime, Cereal serialized status, services, encoder, speed units, pedal priority and cruise base memory. Three pytest configuration warnings concern absent optional plugins. The Windows checkout represents symlinks as text; cereal/car.capnp and openpilot package links were restored in the container before testing. These are software checks, not hardware evidence.

Remaining requirements:
- Restore matching full build dependencies and regenerate/rebuild Cereal and ARM64 pandad. Stable is a prebuilt distribution without the full build graph.
- Independently review/build a matched Panda protocol implementation; provision verified pairing identity. No firmware binary or automatic activation is included here.
- Verify imperial native button steps before supporting mph (currently explicitly rejected).
- Perform a separately scoped physical validation of PQ35 and PQ46 ACC acceptance, driver intervention, camera speed reduction and baseline recovery. Software tests cannot establish equivalence to Hyundai on hardware.

See ICBM_BACKEND_GAPS.md. Existing development binaries are offline candidates and are not evidence of vehicle operation. This branch is not an installable completed ICBM release.
