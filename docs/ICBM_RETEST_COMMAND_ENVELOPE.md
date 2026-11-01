# PQ35 retest: command envelope validity

2026-10-29, candidate 28c197e, Volkswagen Caddy PQ35, metric.

Read-only live diagnostics confirmed installed commit28c197e, signed Panda
SHA2569c86febc3db666c79f32a4af0cc15ed18bc5dfbed9ebd1d277f1cd972431a718,
matching actual Panda signature and fresh protocol status. ICBM setting enabled.
The driver reports no LKAS warning on brake. Initial90s capture shows four
brake rising edges after the initial sample, no EPSstatus2 and no HCA192
rejections. This observation is not full vehicle validation.

The host publishes decreasing requests while firmware requestId remains0.
Raw recorded CAN and production parser replay show fresh native readiness,
radar and setpoint feedback; native idle TX-return echo bootstrap is healthy.
A separate live20s observation confirms valid fresh CarControl/CarControlSP,
CC.enabled true, no override/cancel/resume, and decrease target33kph.
The exact remaining runtime coordinator state was not exposed by these logs.

A definite transport defect was found independently: production Card sets the
inner customReserved10.valid but leaves outer Event.valid false. pandad requires
both, so any command from this publisher is discarded before USB A9. Setting
outer validity according to command presence repairs this gate; it does not
establish that all upstream runtime gates or stock ACC responses work.
No-command messages remain invalid and clear retained requests.

Regression tests execute the actual production publisher method with real Cereal,
serialize and decode its output. All four button codes and active-to-empty
clearing are checked. Same tests on the prior method fail5/5; corrected publisher
and existing Card/status tests pass11/11 with no skips in the Linux runtime.
The new tests cover the production publisher boundary previously bypassed by
synthetic tests that supplied a manually valid envelope or called A9 directly.

Only host publisher and tests changed. Panda image, daemon and all steering/MADS
behavior are unchanged. Physical request transmission, correlated button release,
stock setpoint response, PQ46 and mph behavior remain pending.

Raw evidence is preserved outside Git under
workspace/results/icbm_retest_28c197e: continuous/manifest.json,
local-retest-review.json, local-gate-review.json, local-parser-review.json,
activation-retest.json. Captures continue independently of sequential downloads.
No application code or firmware was changed on the moving vehicle.

## Manual-input recovery correction

Actual b1418cb capture confirms8SETleases, each7modifiedTXOK130frames,
withstock40->39->38->37->36->35->34 then40->39->38. First responses
50-80ms afterfirstsyntheticpress. Exactrawstockdata and full hashes remain
outsideGit under workspace/results/icbm_retest_b1418cb/native-press-review.json.

Afterlease8COMPLETE, manualUP invalidated ownedobservation whilehostsettling.
Oldterminaltimestamp precededblock, INVALIDobserver ignorednewidle, leaving
>34secondsdecreaseintent withoutfurtherleases. Productioncoordinatorfixture
reproducedthisdeadlock beforefix.

Correction keeps terminallease/pairing checks. Matchingolderterminal3/6 permits
one-shotresetofownedobservation; clearancestillrequiresNEWmatchingnativeidle
andgenuineTXOKafterbothblockandreset, freshfeedbackandnormalnewpressguards.
Polling neverrenewsoldCOMPLETEtime. Panda/daemon/steering/MADS unchanged.

Validation240passed, including28newrecoverycases; newregression failsagainst
previousHEAD. Independentread-onlyreviewfoundnoblockingissue. Physicalretest
ofnewrecoverybehavior remains pending. Testlog and baselinefailureproof:
workspace/results/icbm_retest_b1418cb/recovery-tests.log and
recovery-baseline-negative.json.
