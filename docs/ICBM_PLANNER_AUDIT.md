# ICBM planner source transplant

Date: 2026-10-28. Source: dev `aabf70ac1f211c343cca0181f77777dd12c1ff6e`.
Destination baseline: stable `3b9179406c4a43319af3383157fc83e1098cba4d`.

Transferred `sunnypilot/selfdrive/car/cruise_ext.py`,
`sunnypilot/selfdrive/car/intelligent_cruise_button_management/controller.py`,
and `selfdrive/car/cruise.py`, plus focused ICBM/PQ tests.

The remembered native-ACC target survives pedal suspension and RES. Camera
episode button corrections do not replace that base. SET used to reengage ACC
updates the base after release and valid native feedback; the main cruise switch
ends the remembered session. Physical pedals, native ACC engagement and manual
camera takeover suspend automatic buttons. Timer dictionaries are per instance.

CONFIRMED software checks on the destination tree: 241 pure selector,
transaction, observer, pairing, coordinator and pedal tests passed; 82 cruise
memory and pedal tests passed (these groups overlap). Coordinator reported
333 Docker port tests passed, including actual Cereal speed-unit and Card
serialization checks. Local Windows native Cereal import aborted; its result
is not counted as a pass. `git diff --check` passed for the planner changes.

The packaged-release test skips only when its manifest is absent, as expected
for a source candidate. A present invalid manifest or mismatched artifact still
fails. No binary or firmware was added by this transplant.

UNKNOWN: physical radar acceptance and equivalence on every PQ35/PQ46 variant.
The verified selector is kph only. Supporting mph requires evidence for native
fine/coarse ECU step behavior; unit conversion alone does not establish it.
Software tests do not establish vehicle readiness.
