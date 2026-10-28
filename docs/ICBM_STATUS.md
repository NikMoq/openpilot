# ICBM PQ35/PQ46 integration status

2026-10-28. Goal ACTIVE; functional equivalence to the original on vehicles is NOT established.
Base stable: 3b9179406c4a43319af3383157fc83e1098cba4d. Source donor: aabf70a, with additional integration fixes.
Primary user vehicle: VW Caddy IV 2019, PQ35, factory radar ACC, comma3, Panda dos/F4 class observed. Installed commit 665152c and packaged firmware hash verified read-only; live firmware signature is only cached Params evidence. PQ46 validation vehicle UNKNOWN.

## Implemented and checked offline

- PQ coarse UP/DOWN and fine RES/SET selection, one outstanding transaction, stock setpoint feedback, correlated release and driver priority.
- Camera database/source/updater -> liveMapDataSP -> planner -> ICBM target chain restored.
- Target publishers invalidate ICBM when upstream plan/state sources fail checks instead of restamping retained targets.
- Driver base speed preserved through pedal suspension and temporary camera corrections.
- Native bridge rejects invalid Event envelopes as well as invalid command payloads; actual production-function/msgq integration harness passed.
- Native ARM64 QCOM2 pandad built from current inputs; native protocol harness passed. SHA256: 4a7153f4c8046af32906c1a4d2846234baa70dc95d7b2d27f6b1a3180d6dd875.
- Panda F413 offline candidate built with unchanged stable 300 cNm safety and separately authorized signing completed. Signature/public-key verification passed; see ICBM_SIGNED_OFFLINE.md.

## Evidence

- Broad focused Docker suite: 1233 passed, 9 skipped. Skips are not passes.
- Separate real Cereal/schema/freshness suite: 42 passed.
- Actual native msgq/Params/camera/planner integration: 18 passed, without mocks for those components.
- Production CarController.update with real coordinator/transaction/echo and injected unrelated callbacks: 9 passed.
- Source staging integrity: 12 passed.
- Optional wrapper preflight: 15 passed, 1 intentionally skipped; invalid/missing artifacts and Linux broken symlinks checked before hardware access.
- Panda ARM emulation: 13 test-only, 4 production and 3 ICBM-disabled control checks passed; two linker violations and a wrong1000 safety input were rejected.

Groups overlap; counts must not be added. Software checks do not measure physical ECU acceptance. Artifact hashes and source provenance are in ICBM_BACKEND_GAPS.md, ICBM_PANDA_OFFLINE.md and tools/icbm_build. Test artifacts stay outside Git.

## Remaining requirements

- Current device is confirmed at 665152c, with original packaged Panda and no ICBM pairing/status; validate compatibility and a separately authorized release before activation.
- Pair and install the signed artifacts only under a separately agreed device/bench task; no current firmware or daemon binary is replaced by this source branch.
- Resolve active-ACC standstill fine adjustment: original permits the state, current PQ motion guard blocks it; no suitable native response evidence yet.
- Verify native imperial units/steps before enabling mph; selector currently rejects mph.
- Obtain synchronized physical evidence of request, native CAN output, stock ACC setpoint response, release and interruption/recovery on PQ35 and PQ46.

The published branch remains a source candidate, not a completed installable ICBM release. See ICBM_WORK_LEDGER.md for active assignments/authorization and ICBM_TRACE_READINESS.md for existing trace evidence.
