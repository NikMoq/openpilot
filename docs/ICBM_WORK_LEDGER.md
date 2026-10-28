# Active ICBM objective and evidence ledger

Updated: 2026-10-28. Goal is active: functional equivalence to original ICBM on PQ35/PQ46; not achieved by software tests alone.

User-confirmed primary vehicle: Volkswagen Caddy IV, model year 2019, PQ35, factory ACC with radar and distance control (clarified option 1), comma 3, Panda believed F4 (not yet independently identified). User says installed branch is ICBM; exact running commit and artifacts UNKNOWN. PQ46 validation vehicle UNKNOWN.

Published source candidate: 665152c on ICBM, based on stable 3b91794. 333 focused Docker tests passed before subsequent changes. It does not activate the new firmware protocol and is not a completed runtime release.

Current bounded assignments:
- registrar: goal, integration, evidence, user data, final review.
- audit_original: restore camera target producer/planner chain and focused tests.
- audit_backend: reproducible native/ARM64 pandad/Cereal build, isolated offline artifacts.
- freshness: preserve upstream target validity through selfdrived/controlsd; regression tests.

New findings:
- Earlier build-graph blocker was overstated: direct native and ARM64 recipes exist under workspace/results/icbm_trace_20261027. They must be made reproducible for this branch.
- Source candidate omitted camera target producer/planner. Agent is restoring coherent dependencies.
- Retained upstream targets could be restamped valid by downstream publishers. Agent is fixing provenance/freshness.
- mph model unsupported pending evidence of native unit behavior; do not guess button endpoints.

Next real-world evidence needed: exact running commit, Panda hardware/firmware identity and configuration; synchronized existing CAN/button/setpoint traces for the current configuration. Request only after offline prerequisites are clear. No vehicle/firmware operations have been performed by this goal.

2026-10-28 continuation:
- User explicitly authorized separate offline Panda F4 button-protocol source changes, build and emulation, preserving stable300 cNm and protections; no signing/install.
- Current stable source STEER_MAX=300 and safety max_torque=300. Prior donor signed candidate at1000 is unsuitable for this branch.
- Current port broad focused suite:1233 passed/9 skipped (20.59s). Separate real Cereal/freshness:42 passed. Real native msgq/Params camera integration:18 passed, no mocks. Counts overlap; do not sum.
- ARM64 QCOM2 pandad fresh build SHA2565087d67cf22c58e95311c633c5266a2d3ef792a2b6fc18867908f3f02a84a4de, native protocol harness PASS; artifact outside Git. Device ABI/runtime not established.
- Independent donor firmware review identified interception of single-bit legacy RES at standstill. Firmware specialist is fixing with focused test before candidate validation.
- WinSCP saved site name remains comma@192.168.0.105, with HostName192.168.0.102. User says GUI session opens. Fixed read-only MCP still gets authentication_refused. No secrets read or requested; running commit/firmware remain unknown.

## Device read-only confirmation, 2026-10-28

WinSCP console and MCP comma_status now independently succeed through saved
site comma@192.168.0.102. The working GUI connection was saved without storing
a password; the stale session had not reproduced its working configuration.
No authentication material content was inspected or copied.

Installed commit665152c1065e10d79d184b76ddada41d4fd8f756, branchICBM,
AArch64, IsOnroad0/IsOffroad1. Passive pandaStates is alive/valid, hardware dos
(F4 class per panda/python/__init__.py), controlsAllowedfalse, safety noOutput
while ignition is off. This is expected offroad state, not an onroad safety test.
CarParamsPersistent fingerprint VOLKSWAGEN_CADDY_10_20, pcmCruisetrue,
openpilotLongitudinalControlfalse, volkswagenPq safetyParam0.
CarParamsSPPersistent: intelligentCruiseButtonManagementAvailabletrue,
pcmCruiseSpeedtrue. Direct current configuration: IntelligentCruiseButtonManagement
missing; IsMetrictrue; SpeedCameraControlEnabled/SpeedCameraEnabled missing;
SpeedLimitMode2 (warning, not assist). No configuration was changed.

Installed Panda signed file SHA256
ef8ea87c17f0802bec38f754d93a3fb2c9850c640b5ba95e01e661323545daba,
size57668. Installed pandad SHA256
3494196f83fa5bec645b1ec30e6a23048b39efe9d8315429dade86a9f807b3f0,
size4197624. No ICBM release manifest or expected pairing parameter; no observed
customReserved11 publisher during bounded passive subscription. PandaSignatures
was read from cached Params, not a direct USB identity query.

Evidence outside Git:
workspace/results/comma_mcp/icbm-diagnostics-d1fb73a6-8eee-4aed-a722-70e9e697ab12.json
SHA256c0c6a961ab7a30b1b5ad2d7deed69761288e8782388bcbfdb36a177e149947fc.
This proves the current installed source candidate is not an activated ICBM pair.
Standalone diagnostics helper and regression checks are maintained in the outer
EPS project's mcp/comma_icbm_diagnostics.py and tests/test_comma_icbm_diagnostics.py.

Native bridge follow-up: process_icbm_command now requires both Event.valid
and command.valid. Its real native Cereal/msgq harness covers stale/future,
invalid payload/envelope, offroad, pairing, Panda count, idempotence and status
timestamp recovery. All checks passed. Clean ARM64 rebuild supersedes the old
5087 candidate: SHA-256
4a7153f4c8046af32906c1a4d2846234baa70dc95d7b2d27f6b1a3180d6dd875.
No binary was committed or deployed. User requests MCP/console WinSCP only;
Computer Use must not be used for device access.

Signed release follow-up (2026-10-28): user authorized offline signing only.
Candidate signed SHA-256606c7f6dd713331350c2eaf264a3d5beae23b79b0e6f59edbeed6e95d82bd659;
original unsigned body preserved with standard SETLEN/VERS2/RSA128 framing.
Public verification and three tamper rejections passed. Matched daemon remains4a7153f4.
Package outside Git: workspace/results/icbm_signed_review_20261028_af1182e6.
No installation, hardware change or configuration activation occurred.

Optional wrapper release_preflight now validates/provisions a present pair or
revokes stale identity when manifest absent. Invalid manifest/metadata revokes
identity, logs once per bounded retry and sleeps1s before retry, prior to any
Panda discovery/recovery/DFU/flash call. Linux tests15passed/1intentional skip.
Source-only releases remain operable without a deployment manifest.

User reports existing PQ46/mph evidence is available and will supply it.
Do not infer unit-step behavior from metric-only Caddy captures while waiting.
