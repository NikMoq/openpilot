# PQ35 ICBM release

Panda base: 1020d355584265391eb3acb556e4353b581fa9c0 (10.2.0).
Apply panda-10.2.0.patch to that base, copy gra_* into board/, and use
build_panda1020_icbm_offline.sh with OPENDBC_1000_ROOT pointing to this directory.
The included safety headers retain the existing 1000 cNm limit and protections.
Signing uses the upstream SETLEN layout (VERS version 2, length, RSA trailer).
No private key is included.

Runtime pair is pinned by selfdrive/pandad/pq_icbm_release.json. The wrapper
checks both file hashes before its normal update flow and atomically provisions
the expected signature using the existing Params namespace. This supports old
Python Params bindings and refreshes identity before each native daemon launch.
The native daemon compares the actual Panda signature; it does not enable ICBM
for mismatched firmware. No Python ICBM USB API is used by this runtime path.

247 release/host tests passed. Earlier ARM protocol, ABI and Cereal tests are
recorded in the build evidence. Offline validation only: physical ECU acceptance
and device runtime of this pair remain to be tested. Install via normal UI update.
