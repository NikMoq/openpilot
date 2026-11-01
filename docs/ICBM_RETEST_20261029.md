# Next vehicle validation

This is a pending test plan, not a record of passed vehicle tests. The car is
currently disconnected. Use the user's agreed closed-site session and normal
branch updater; do not update code or firmware during an active drive.

Before testing, record installed commit, packaged component hashes, fresh
Panda pairing/protocol status, current CP/CP_SP and enabled ICBM setting.
Record to persistent storage, with bounded file size and sealed hashes;
the previous /tmp capture cannot guarantee survival after power loss.

Capture in two bounded passive groups:

- CAN, carState, selfdriveState, customReserved11: native button/HCA output,
  brake/driver state, warning chronology and Panda ICBM acknowledgements.
- selfdriveStateSP, carControl, carControlSP, longitudinalPlanSP: target,
  state/action, control envelope and planner source.

Keep streams synchronized by logMonoTime. Supplement with a short separate
pandaStates observation around startup/shutdown. Do not infer controlsAllowed
or safetyModel from rejected-frame flags alone. Download sealed files
sequentially and verify size/SHA-256 locally before ending the session.

| Case | Evidence required |
|---|---|
| Before initial SET/RES | Unset virtual base cannot produce synthetic button requests. |
| Fresh low SET | Native stock feedback and virtual/planned targets agree before automatic adjustments. |
| Initial RES with delayed feedback | Virtual base initializes after valid native feedback; no command toward an unset target. |
| Higher/lower virtual target | Correlated request, native press/release and actual stock setpoint response; no overshoot attributable to extra presses. |
| Brake with UI continue-steering enabled | Existing MADS keeps CC.latActive true; original Panda steering behavior is retained, without an added host brake override or unexplained HCA rejection burst. |
| Brake with UI pause/disengage selected | Existing MADS supplies the inactive CC.latActive request; VW sends inactive/zero HCA and follows the stock recovery/disengage behavior. |
| Gas/brake interrupt ICBM | No synthetic press while driver owns control; retained base and valid release observed. |
| Recovery/RES | Fresh readiness and feedback precede recovery; remembered driver base is preserved. |
| Manual button priority | Driver input is not counteracted by ICBM; button release and subsequent state are recorded. |

Choose test speeds on site with the driver; do not treat software target
values as instructions to accelerate. PQ46 and imperial-mode native button
steps still need their own vehicle/unit-provenance evidence. Metric PQ35
results do not establish either of those requirements.
