# PQ35 vehicle observations, 2026-10-29

Goal remains ACTIVE. Physical functional equivalence is not established.
This note supersedes older installation status in ICBM_STATUS.md.
The later user-required exact stable firmware basis is authoritative:
see ICBM_EXACT_STABLE_BASE.md. Tests of1020-derived images below are historical.

## Confirmed installation and configuration

Read-only diagnostics identify installed ICBM commit
49e5d28e31a9f2fb98490e9373b30bb1f28cde9a on Caddy IV 2019 PQ35.
Packaged firmware, pandad and Params extension hashes match the published
release. Live Panda status reports matching signature and valid ICBM protocol.
The enabled setting was initially false. After the user stopped, switched off
ignition and enabled it, diagnostics confirmed the persisted setting true.
Next onroad diagnostics confirmed pcmCruiseSpeed=false; separate passive
observation confirmed fresh valid selfdriveStateSP, carControl, carControlSP
and longitudinalPlanSP. ICBM remained inactive while cruise was disengaged.
The planner reported 145 kph; source inspection shows that an unset virtual
speed (255) can be clamped to this value. Actual carState virtual/native
speeds were subsequently decoded locally: segments0033..0037 consistently
contain vCruise=vCruiseCluster=255, native cruise speed zero and cruise
disengaged. Thus the observed 145 planner target in this period came from
an unset speed, not a driver-selected 145 kph. The user later reported
setting the general displayed target to30; a corresponding remote capture
was not confirmed before connection loss.

## Confirmed brake-associated LKAS fault

Evidence: workspace/results/icbm_drive_20261029/hca-detail.json and
eps-timeline.json in the outer project. Sealed segment_0004.bin SHA-256:
ca726d9200bf2d2bd28cdd7915fd100a95dd3b882880c0f81bc540d4233bd77e.

Relative to recorded brake CAN transition at logMonoTime 3722884622687:

- +5.25 ms: selfdrive disabled.
- +10.20 through +249.68 ms: thirteen active HCA status7 frames rejected by
  Panda, source192. This flag denotes rejected TX, not successful delivery.
- +239.84 ms: EPS reports LH2_Sta_HCA=2 FAULT.
- +245.26 ms: steerUnavailable/immediateDisable warning.
- +269.47 ms: zero torque, inactive HCA status3 successfully transmitted;
  source128 return and source1 reception. Successful HCA gap: 280.18 ms.

Times are Cereal Event receipt times, not independent physical CAN timestamps.
This establishes a command interruption preceding the EPS fault. Attribution
of the EPS fault specifically to this interruption remains a hypothesis.
The user reports the displayed text as LKAS error / immediate takeover.

Full local review found this sequence in four driving episodes: segments0000,
0001,0004,0008. EPS FAULT followed the brake by220–260ms; successful HCA
transmission gaps were260–300ms, with12–14 rejected active frames per episode.
All11401 native ICBM status samples had requestId0 and txCounter0: physical
ICBM button/setpoint operation was not exercised in the retained data.
The review also found204 rejected inactive HCA3 frames in separate episodes,
outside the driving faults. Most accompany stopped publication/CAN validity
loss; the others occur with an existing EPS fault. Missing ignition and
pandaStates streams prevent attributing those rejects to a safety mode.

Inactive-zero HCA was tested as a diagnostic counterfactual. An initial host
brake override was never published and has been removed. The release must
keep the stock CC.latActive steering decision and the user's UI/MADS
continue-steering behavior. The exact stable USB comparison revealed the
missing MADS integration in the1020-derived firmware; that base was superseded
by the user's explicit whole-original-stable-plus-ICBM requirement.

## Historical1020 reproduction and additional ICBM source corrections

A clean pinned Panda rebuild reproduced unsigned application SHA-256
5453088eebc1faa119aa41f6f10f2a8ba9b2152d5b1859f30a370fc2b7c25670,
identical to the released signed application's body. This reproduction is
comparison evidence, not the basis for the next exact-stable release.
The compiler checked40 stable safety
input hashes. Actual ARM emulation passed13 protocol,4 production and3
stock-control checks. Captured inactive HCA3 was accepted with brake=true
and controls_allowed=false, while captured active HCA7 was rejected.
MMIO/hardware initialization are substituted; EPS behavior is not emulated.
Evidence: outer workspace/results/icbm_rebuild_20261029/manifest.json.

Additional reproduced defects: an unset virtual speed could yield an ICBM
increase toward the planner's clamped145kph; an old plan could exceed a newly
selected lower driver base; initial RES with delayed stock-speed feedback
could leave the virtual speed unset. Source corrections reject invalid/unset
or above-base plans before rounding/action and initialize the first RES once
valid stock feedback arrives, preserving an already remembered driver base.
These corrections also require physical validation after normal updating.

Trace-calibrated synthetic testing found two further host issues. Off-grid
DOWN behavior was not established by historical recordings: all199 DOWN
episodes started on decade values. The selector now uses verified SET-1
off-grid, reserving coarse DOWN for decade values. Both plausible DOWN models
converge without overshoot for all integer target/current pairs30..145.
UP off-grid behavior is supported by56→60,33→40,43→50 and106→110 observations.

Native transactions now use30kph as their attainable endpoint for nonnegative
metric plans below the shared ICBM minimum, preserving the driver's virtual
base. Previously target0 left stock80 unchanged, and target15 could keep
attempting presses after reaching30. Regressions reproduced both before the
fix. No guessed imperial step or unit conversion was enabled.

Expanded current-source Linux checks used real Cereal serialization, Params
and msgq:500 tests passed with zero skips, including actual packaged release
files, selector/coordinator transactions and below-minimum regressions.
Earlier release/wrapper checks overlap; counts are not additive.
Evidence: outer workspace/results/icbm_rebuild_20261029/runtime-expanded-report.json.

The historical1020-based synthetic closed loop passed15 scenarios using production VW
update/coordinator and actual ARM Panda RX, A9/AA, adapter/queues and TX-return.
The stock ACC feedback is an explicit80ms response model calibrated to
historical metric observations, not an ECU emulator. Both DOWN models,
precise targets, driver interrupts/recovery, retries, lost release/feedback
and clean completion at minimum were checked. Planner/upper publisher and
base memory are covered separately, not executed in this closed-loop harness.
Evidence: outer workspace/results/icbm_panda_replay_20261029/synthetic_closed_loop.json.

## Capture scope and open requirements

The continuous passive recorder subscribes to can, carState, selfdriveState
and customReserved11. Sealed fragments are downloaded sequentially and
verified by size and SHA-256. No control publications or vehicle settings
are changed by the recorder. Separate bounded subscribers inspect activation.

Still required: actual ICBM command/setpoint acceptance and release, driver
priority, recovery, PQ46 evidence and supported speed-unit equivalence.

## End of acquisition

The user reported laptop battery exhaustion and comma disconnected. No
further device tests are requested in this session. Local manifest was
rechecked:38 segments,96807500bytes, all sizes and SHA-256 match. This is
approximately19minutes of segmented recording, including ignition-off
periods; it is not19minutes of continuous active driving. Earlier bounded
captures and diagnostic JSON reports are retained separately.

Remote recorder termination and any later segments are unverified. Files
were stored under /tmp; survival after loss of device power is not assumed.
Do not claim that a final growing segment was downloaded or preserved.
