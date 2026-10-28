# ICBM device acceptance plan — proposed, not executed

2026-10-28. Source candidate e1272e2. Offline review package in the outer EPS workspace:
workspace/results/icbm_review_20261028_f012b5c0/manifest.json
Manifest SHA-256 d251f169802cb1fa6f2801e1084bf437302f1a074344d59b9ba2891842961a9a.
The package contains an unsigned Panda candidate and the ARM64 daemon, not an installer.

## Current authorization and identities

User authorized offline Panda protocol changes/build/emulation, preserving stable300 cNm. Signing and device installation were excluded. A separate signing question is pending; no key content has been accessed. Device changes and physical tests need their own agreed bench task. No road test is authorized.

Current read-only device evidence: Caddy IV2019 PQ35 factory radar ACC, comma3, Panda dos/F4 class, branch ICBM665152c, ignition off, safety noOutput. Persistent CP identifies VOLKSWAGEN_CADDY_10_20 with native ACC. ICBM is available but not enabled: parameter absent and pcmCruiseSpeed=true. SpeedLimitMode=warning. No new ICBM firmware status/pairing is present. Cached PandaSignatures is not a direct flash readback.

## Release prerequisites

1. Independently verify the candidate unsigned BIN and daemon against the review manifest and committed source/evidence.
2. Complete separately authorized signing; verify the signed framing, signature with an appropriate public key, and unchanged application bytes except the documented signing length field. No keys belong in Git, chat, logs or transfer package.
3. Pin signed firmware and daemon hashes/public signature in a release manifest. Confirm loader/ABI compatibility on the intended device before launch.
4. Agree a bench task with exact artifacts, identity/rollback checks, controlled conditions, data capture and stop criteria. Existing rollback_packaged files match device packaged hashes; they are not a Panda flash readback.
5. Only after compatible runtime/pairing is established, agree activation of ICBM and the required settings. An update alone must not be labeled activation or successful operation.

## Required evidence by behavior

| Requirement | Evidence that would establish it |
|---|---|
| Native target reached | Correlated host request, actual transmitted GRA frame, released button, and fresh ACC_GRA_Anzeige setpoint response; TX-return alone insufficient |
| Fine/coarse behavior | Separate observed UP/DOWN/RES/SET response including nondecade targets, no duplicate or overlapping presses |
| Camera reduction/recovery | Versioned camera/planner target and measured ACC setpoint/vehicle response before and after camera passage; remembered driver base preserved |
| Driver priority | Real manual buttons and gas/brake inhibit new automatic presses; outstanding release accounted for |
| Recovery | Gas release resumes only with verified idle; after brake only driver's RES reengages ACC; no spontaneous engagement |
| Fault handling | Stale/invalid plan/state/CAN, unit mismatch and pairing loss stop new requests; owned release remains tracked and no blind retry |
| Protection preservation | No unexplained rejection/fault; original torque/rate/driver/control policy preserved in actual system |
| Units | Kph behavior confirmed; mph needs independent native unit/step evidence before enabling |
| Platform scope | PQ35 Caddy evidence does not prove PQ46; obtain a separately identified PQ46 configuration and validation |

Capture clocks, bus numbering, raw input hashes, source commit, firmware/daemon identity, configuration and test conditions with every physical result. Distinguish requested setpoint, native displayed setpoint, actual vehicle speed and Panda TX evidence. Do not convert software/emulation results into physical acceptance claims.
