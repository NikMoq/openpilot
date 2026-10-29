# Required release basis: original stable Panda plus ICBM

2026-10-29. User instruction supersedes the earlier1020-based Panda approach.
Status: OFFLINE_TESTED_SIGNED_PACKAGED_PENDING_VEHICLE_RETEST.

The authoritative whole firmware is the original F4 image from stable commit
3b9179406c4a43319af3383157fc83e1098cba4d, Git blob
835231531d6013dcc9330d79eb187f1b67663ce1, SHA-256
ef8ea87c17f0802bec38f754d93a3fb2c9850c640b5ba95e01e661323545daba.

Only ICBM may be added. Existing steering torque transmission, MADS, brake
settings, safety enforcement, queues and ordinary USB behavior stay original.
The VW host steering branch is restored to its stock CC.latActive decision.
The existing UI/MADS state machine owns continue/pause/disengage decisions.
The user explicitly requires continued steering after braking when that UI
setting is enabled. No separate controller brake override is part of release.

The stable repository contains the complete signed image but no matching
complete build source or ELF. The exact image already executes directly in
the comparative ARM harness, with evidence-backed address aliases. Extension
work therefore preserves the original application bytes except reviewed ICBM
hook sites and necessary signing/length metadata, and appends the new module.
Original data/BSS, queues, stack placement, MADS and safety functions are not
rebuilt or relocated. Every changed byte requires an explicit allowlist.

The implemented seams are two previously unhandled A9/AA dispatch table entries,
the native GRA forwarded-copy call, TX-return observation and extension-state
initialization before original main. Original native safety RX must execute
exactly once, and original TX safety remains authoritative. New ICBM guards
may read original state and track their own bounded lease; they must not write
original steering/MADS/protection state.

Required order: build unsigned exact-base candidate; run recorded CAN and
trace-calibrated synthetic ICBM tests; compare ordinary behavior against the
unchanged original; review framing/signature and package identity; publish to
ICBM only after those checks pass; then perform the user's next real session.
Software passes alone do not establish physical functional equivalence.

## Packaged artifact and completed software gates

Signed firmware SHA-256:
9c86febc3db666c79f32a4af0cc15ed18bc5dfbed9ebd1d277f1cd972431a718,
60196bytes. Unsigned prototype SHA-256:
0523b0181b8f1b5be29fd688a7207ee8690ed2dd64973dcc747f942427e06695.
The appended module is2508bytes with64bytes of separate state. Fourteen
original application bytes differ within five reviewed hook ranges; the
signed length word and RSA/VERS2 framing are updated separately. All other
original application bytes, including steering/MADS/safety, remain identical.

- Full270s replay:1425109rows,1019879physical RX,13501HCA commands,
  13508recorded GRA TXOK observations. Original and prototype compared equal
  for queued/host-RX meaningful packet bytes and mapped guard/mode states.
  Forty-two shared initial label mismatches reflect missing initial torque
  history, not a difference between images.
- Same15 closed-loop scenarios passed using the complete raw image loaded
  before original reset. Stock ACC replies are an explicit trace-calibrated
  model; the planner and driver-base memory have separate tests.
- Eight historical button-trace scenarios passed, including actual native
  UP/DOWN/SET/RES data and driver priority. CAN-tap channel1 is explicitly
  mapped to native bus0 for selected vehicle RX messages; unclassified HCA
  tap traffic is not treated as vehicle RX0.
- Nineteen in-contract independent adversarial scenarios passed. Original
  TX safety approves every generated press; original native RX validation
  remains single and authoritative. The realistic full timer-wrap test uses
  original background safety services. A frozen-background artificial
  observation is documented outside the execution contract, not counted.
- Original startup register/flag context and initialized RAM compare equal;
  twelve additional ABI/nested-critical-context cases passed.
- Current host/Cereal/Params/msgq/selector checks:508 passed, zero skipped.
- Final signed image: public signature/framing and four corruption rejections
  passed. Original D3/D4 USB reads return the new signature; DD stays16/4/5;
  actual signed-image A9→seven presses→release/TXOK→AA COMPLETE smoke passed.
- Packaged release/wrapper checks:17 passed, zero skipped. Coverage overlaps
  other groups; these counts are not additive.

The existing ARM64 daemon4a7153f4...dd875 and Params extension667cb07f...e5ef7
are unchanged. Original health58-byte ABI and packet versions16/4/5 match the
daemon. No private material or raw vehicle recordings are included in Git.
Hardware scope is the observed comma3/dos F4 configuration; placement is
supported by previous same-device live usage and disjoint-original access
evidence. Physical ICBM acceptance, EPS recovery, PQ46 and mph remain unproven.

Reproduction: tools/exact_stable_icbm/README.md and
tools/pq_icbm_release/SYNTHETIC_TESTS.md. Local gate evidence is indexed in
workspace/results/exact_stable_icbm_module_20261029/VALIDATION_MANIFEST.json,
icbm_stable_oracle_snapshot_20261029 and icbm_exact_signed_20261029_0523b018.

Earlier1020-based builds, MADS interop experiments and their closed-loop PASS
reports are historical comparison evidence, not validation of this new basis.
No1020-derived rebuild or unconditional brake-pause workaround is to be
substituted for the required exact original plus ICBM.

Evidence outside Git: workspace/results/icbm_stable_comparison_20261029 and
workspace/results/icbm_panda_replay_20261029. Original full270s comparison
under default configuration had13501 HCA decisions per mode with no difference
from the earlier published candidate. Actual USB MADS configuration tests
revealed the integration difference hidden by that default-only replay.
