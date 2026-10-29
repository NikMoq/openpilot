# Camera Params registry binding

The Python binding embeds the C++ registry. Adding keys only to `params_keys.h`
does not update the tracked ARM `params_pyx.so`; its `clear_all` also removes
unknown files. A matching rebuilt binding is therefore part of the candidate.
No manager cleanup bypass or Python Params wrapper replacement was introduced.

## Inputs and provenance

- Original tracked binding SHA-256:
  `667cb07f118989b8d09a0699008b071a79530bbaca8348ee73f08aed9c0e5ef7`.
- ELF inspection confirms AArch64. The CPython 3.12-specific
  `PyUnstable_Code_NewWithPosOnlyArgs` import and embedded Python 3.12 header
  paths support the 3.12 ABI; no device interpreter was queried.
- Build helper imported from the explicitly authorized NikMoq/openpilot dev
  commit `aabf70ac1f211c343cca0181f77777dd12c1ff6e`, then adapted for a native PC
  QA profile, Windows schema pointers and input hashes. Import hash is recorded
  in `tools/ui_build/params_binding_import.json`.
- Cython 3.1.3 regenerates C++ from the current checkout's `params_pyx.pyx`.
  All five translation units are compiled afresh from current sources. No
  donor object or application binary is linked.
- ARM headers: official `python:3.12-slim-bullseye`, ARM64 image digest
  `sha256:411fa4dcfdce7e7a3057c45662beba9dcd4fa36b2e50a2bfcd6c9333e59bf0db`,
  Python 3.12.11. Native QA headers/runtime are Python 3.12.14 from the fresh
  dependency environment. Header and source hashes are in each candidate JSON.

All three new keys (`SpeedCameraReactionDistance`, `SpeedCameraRoadGraphPath`,
`SpeedCameraRoadGraphStatus`) are registered as `PERSISTENT | BACKUP`.

## Candidates

Generated artifacts remain outside Git in `.native-build/camera-params-binding`.
The tracked original `common/params_pyx.so` is preserved.

| Candidate | SHA-256 | Verification |
| --- | --- | --- |
| `native/candidate/common/params_pyx.so` | `f2f4b33ebcbe3591d9b2161f25d6183e9a46dca0efd246ed2c52e9446052bcea` | Actual CPython 3.12 native execution |
| `arm64/candidate/common/params_pyx.so` | `07bb1204a587a9c4be8f8715045408065b01a5f0fb10a3b6b595e8f4cab66464` | Fresh AArch64 compilation and ABI inspection |

The ARM candidate has the same required shared-library names as the original:
`libzmq.so.5`, `libstdc++.so.6`, `libgcc_s.so.1`, `libpthread.so.0`, `libc.so.6`.
Its maximum version requirements match the original: GLIBC 2.17 and
GLIBCXX 3.4.26. This is an offline candidate, not proof of device execution.

## Native verification

`tools/ui_build/check_params_binding.py <fresh-native-binding>` executes the
actual Cython/C++ binding against an isolated temporary directory. Only Python
cloud logging is replaced. It verifies:

- all three keys, native types/defaults, persistence and backup membership;
- preservation through manager-start, onroad, offroad and ignition cleanup;
- persistence after destroying and reopening Params;
- continued removal of unknown files, proving cleanup was not bypassed.

The check passed for the native candidate above. No hardware installation,
flashing, steering change, or device readiness claim is part of this work.

## Rebuild

Inside a clean toolchain environment, generate the C++ source using
`cython --cplus -3 --module-name common.params_pyx common/params_pyx.pyx -o <output>/params_pyx.cpp`.
Invoke `tools/ui_build/build_params_binding.py --python-include <matching-arm64-python3.12-headers>
--generated-cpp <output>/params_pyx.cpp --output <arm-output>` for the ARM build.
Add `--native` and provide native Python 3.12 headers for the PC QA build.
The helper generates matching Cap'n Proto headers into the output tree, hashes
its source inputs, and never overwrites the tracked binding.
