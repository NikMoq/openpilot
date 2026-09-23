# Panda 1000 integration into pqplatform

Status: **DEV / OFFLINE BUILD PREPARED**. No Panda was flashed and no vehicle
test was performed.

## Base

- repository: `https://github.com/NikMoq/openpilot.git`
- base branch: `pqplatform`
- base commit: `42a6bb6cc389e54f0dbb6b26a8eb43e9f503ff33`
- working branch: `dev`
- `pqplatform` and `stable` remain unchanged.

The phrase Panda 1000 refers to the exact deployed-base artifact produced by
this project, not to an independently identified SunnyPilot Panda source tree.
No separate SunnyPilot repository was merged.

## Confirmed artifact

Source stock image:

`panda/board/obj/panda.bin.signed` from the base snapshot
SHA-256: `ef8ea87c17f0802bec38f754d93a3fb2c9850c640b5ba95e01e661323545daba`

Dev image copied from the independently validated exact patch:

`panda/board/obj/panda.bin.signed`
SHA-256: `42ab4e5731225fe029e2a3a725379e25fc93cdb3070f17846489d9587c90d15c`

The unsigned payload changes only offsets `0x8B1C..0x8B1D`, little-endian
`300 -> 1000`. The 128-byte Panda debug RSA signature verifies. Companion
limits remain rate up 6, rate down 10, RT delta 113, driver allowance 80,
driver multiplier 3, and `TorqueDriverLimited`.

## Source alignment

The minimum source changes in `opendbc_repo` are:

- `opendbc/car/volkswagen/values.py`: `STEER_MAX 300 -> 1000`;
- `opendbc/safety/modes/volkswagen_pq.h`: `max_torque 300 -> 1000`;
- `opendbc/safety/tests/test_volkswagen_pq.py`: expected static limit `1000`.

No UI, planner, controls, CAN definitions, Panda host protocol, updater, or
other SunnyPilot code was merged. The source header is an audit/reference
alignment for the prebuilt exact artifact; this checkout does not contain a
reproducible Panda board compiler tree.

## Build and artifact checks

The pqplatform snapshot contains prebuilt Panda artifacts but no `SConstruct`,
`SConscript`, board source, linker files, or ELF. Therefore a fresh Panda
firmware rebuild cannot be claimed from this tree. Verify the copied image
before any separate device operation:

```powershell
Get-FileHash panda/board/obj/panda.bin.signed -Algorithm SHA256
```

Expected hash is the dev SHA above. The source-level safety tests can be run
through the existing opendbc/Docker procedure documented in the parent EPS
PQ35 handoff. The formal generic suite contains known unrepresentable ±1499
vectors; do not change production safety logic to force those vectors to pass.

## Update/flash path

The exact pandad updater and bootstub path are present in this pqplatform
checkout, but physical flashing is intentionally not performed by this
integration. Before any separately authorized bench operation, capture the
running Panda identity/signature, preserve the stock rollback image, verify the
dev SHA, and follow the existing pandad signature/version checks. This branch
is not a claim of `BENCH_READY` or `ROAD_READY`.

Confidence labels: artifact identity and binary delta are **CONFIRMED**;
source-to-prebuilt reproducible compilation is **UNKNOWN**; physical
controller→Panda→EPS behavior remains **UNKNOWN** until bench capture.
