# Offline native ICBM build

No device, firmware, signing or installation operation is part of this recipe.
Build outputs belong outside Git.

## Inputs and provenance

Current port source manifest: results/icbm_stable_port_build/port_source_sha256.json
in the outer EPS workspace. All 55 available source/header/schema files override
cached inputs. Hash-pinned source-only foundation: source_inputs.sha256 here (185 text files).
Each staging output records actual foundation/overlay hashes in staged_inputs.json.
Immediate hash-matching foundation snapshot:
pq35-camera-runtime:/tmp/icbm_stable_port_build. This snapshot was created from
/tmp/pq_icbm_cross (the 2026-10-27 ARM64 staging tree) plus current port overlays.
The manifest pins the resulting snapshot, not the earlier /tmp/pq_icbm_cross:
for example their custom.capnp files differ. The earlier tree is deliberately
rejected by the strict hash check. Its historical build evidence lives under
results/icbm_trace_20261027/aarch64_build. Its original upstream reference
is workspace/upstream_sunnypilot_ui_native commit
4962753e47d58f6c86fc284710b6a444ae45788f; Panda headers reference
workspace/panda_build_1020 commit 1020d355584265391eb3acb556e4353b581fa9c0.
The hashes, rather than directory names or a clean-source assumption, pin the
cached foundation used. Regeneration requires matching those hashes; source
changes require a new manifest and fresh objects.

## Exact cached-container recipe

Docker prefix on Windows:
`docker --config C:/Users/Public/pq35-docker-client --host npipe:////./pipe/docker_engine`

Use stage_sources.py to create a NEW /tmp/icbm_source_stage in pq35-camera-runtime
from /tmp/icbm_stable_port_build plus current port sources. Do not copy foundation
directories wholesale: the cached tree contains generated binaries and caches.
The staging tool resolves cereal/car.capnp from opendbc_repo/opendbc/car/car.capnp
and includes the two build helper scripts.

In ARM64 pq35-ui-build stage the same cereal schema under an isolated directory:

```sh
cd cereal
mkdir -p gen/cpp
capnp compile -I. -oc++:gen/cpp custom.capnp car.capnp legacy.capnp log.capnp
```

Transfer generated gen/cpp to the isolated camera-runtime cereal/gen/cpp.
The ARM64 compiler and static libraries are Capn Proto/KJ 0.7.0.
The cached cross sysroot contains ARM64 GCC9 headers. In camera-runtime:

```sh
cd /tmp/icbm_source_stage
CROSS_SYSROOT=/tmp/icbm_arm_sysroot sh build_aarch64_pandad.sh
```

Transfer fresh arm_obj to an isolated directory in pq35-ui-build and link:

```sh
clang++ -pthread -o pandad_icbm_aarch64_qcom2 arm_obj/*.o \
  -lusb-1.0 -lzmq -Wl,-Bstatic -lcapnp -lkj -Wl,-Bdynamic -ldl
ldd -r pandad_icbm_aarch64_qcom2
```

Native source list and exact compiler flags are in the shell script. Linux
libusb development headers, ZeroMQ, clang, GCC9 ARM64 C++ headers and static
Capn Proto/KJ archives are required. Root SConstruct is not required.
The native protocol test uses real Panda codec methods with a mock handle;
it never opens a Panda connection. For ARM64 compile test plus panda.cc with
function sections and gc-sections, link in ARM64 container and execute there.

## Recorded result

Fresh source rebuild and ARM64 link passed, including refreshed Params header
with SpeedCameraSpeedOffsetKph. Daemon SHA-256
4a7153f4c8046af32906c1a4d2846234baa70dc95d7b2d27f6b1a3180d6dd875.
Protocol harness SHA-256
44cd7742e272dd608158efce8ff91677d0a52f08a8286b2f71bde52dcadd4d5e.
Harness output: ICBM native protocol: PASS (no hardware).
ldd -r resolved all symbols in ARM64 Ubuntu20.04.
No device runtime or ECU acceptance claim follows from these checks.

## Strict staging and clean compilation

Use stage_sources.py with --foundation pointing to the fully hash-matching
foundation snapshot, --port to the current port root and --output to a NEW
directory. It checks every foundation file against source_inputs.sha256
before writing, overlays available current sources and records staged_inputs.json.
The cached snapshot must match hashes; a directory name is insufficient provenance.
No object cache is reused. build_aarch64_pandad.sh always compiles all sources
and creates a fresh arm_obj.XXXXXX directory by default; ARM_OBJ_DIR may select
an explicit directory, but compilation still overwrites every listed object.
Link only the printed object directory, never a glob spanning old builds.
The historical result above used a fresh isolated arm_obj directory; the script
now enforces fresh recompilation even when headers/schema/flags alone change.

## Source-only staging filter

The foundation manifest excludes 12 historical cache/binary inputs, including
__pycache__, .pyc, shared libraries, extensionless bridge/pandad executables and
bootstub .bin files. stage_sources.py rejects cache directories and paths outside
an explicit text-source suffix/name allowlist; generated Cereal .c++ sources are
allowed (regenerate them for the selected compiler as described above). Every
foundation input must match its hash even when the current port replaces it.
Both foundation and overlay content must be UTF-8 text without NUL bytes. All
validation finishes before the output directory is created, so invalid input
cannot leave a partially staged source tree. A pre-existing output is refused.

Run staging regression checks with:
`python -m pytest -q -o addopts='' --confcutdir=tools/icbm_build tools/icbm_build/test_stage_sources.py`.

Filter validation on 2026-10-28: the hash-matching snapshot staged successfully
into /tmp/icbm_source_filter_check_20261028_1 with 185 source inputs and no
.pyc/.so/.bin files or __pycache__ directories. The older /tmp/pq_icbm_cross
snapshot was rejected for a custom.capnp hash mismatch before any output was
created. Twelve staging regression tests passed. Filtering does not rebuild or
change the historical daemon hashes recorded above.

## Production native command bridge harness

selfdrive/pandad/tests/test_icbm_bridge.cc calls the actual production
process_icbm_command from pandad.cc and publishes/receives through real native
PubMaster/SubMaster and Cereal. Only PandaCommsHandle is mocked; the protected
TestPanda constructor bypasses USB/SPI opening. Linker gc-sections drops unrelated
daemon hardware paths. No production source seam/refactor is needed.

From a fresh hash-staged native source root, copy the CURRENT test file and
pandad.cc plus current cereal/custom.capnp, regenerate schemas, then execute
`sh /path/to/tools/icbm_build/test_native_bridge.sh`. It compiles all inputs
with matching native Capn Proto headers/compiler. The tested source foundation
is /tmp/pq_icbm_verify in pq35-camera-runtime; isolated output and source overlay
are /tmp/icbm_native_bridge. Native clang19.1.7 and Capn Proto1.1.0 were used.
The harness creates a unique /dev/shm/icbm-native-bridge-PID namespace and sets
OPENPILOT_PREFIX before constructing real msgq sockets. It uses bounded1000ms
polls and assertions; no inference, model runner, Panda or vehicle is opened.

Result: ICBM production native bridge: PASS (real msgq/Cereal, no hardware).
Checks fresh A9 write exactly once, same-ID idempotence including terminal
status, stale/future command timestamps, invalid command payload, offroad,
unpaired and multi-Panda inhibition, zero/out-of-range fields, and terminal
status timestamp preservation across failed read/recovery plus advancement
on a genuine new transition. Event.valid=false with payloadvalid=true is rejected too; production checks
both the Event envelope and the command payload validity.

Evidence outside Git: outer workspace results/icbm_stable_port_build/
native_bridge_{inputs.sha256,validation.log,build.log}. ldd-r resolves all
symbols in the disposable build container. This verifies native bridge policy
and serialization, not USB/SPI transfer behavior or ECU application acceptance.

## Event envelope validity follow-up

Production process_icbm_command now requires both command Event.valid and
payload.valid. Native bridge regression sends Event.valid=false while payload
valid=true and verifies no A9 write. Actual bridge harness passed after the fix
with real Cereal/native msgq and mock transport only.
A full fresh ARM64 production rebuild (all 23 objects, no object reuse) used
current source and regenerated Capn Proto0.7 Cereal, then linked in ARM64
Ubuntu20.04. ldd-r resolves every symbol. Final production daemon SHA-256:
4a7153f4c8046af32906c1a4d2846234baa70dc95d7b2d27f6b1a3180d6dd875
This supersedes the earlier 5087d67c candidate. Output remains outside Git.
Evidence: outer workspace results/icbm_stable_port_build/
event_valid_arm_{inputs.json,build.log,validation.log}, plus refreshed
native_bridge_{inputs.sha256,build.log,validation.log}.
The fresh schema transfer includes cereal/include/c++.capnp. Staging normalizes
shell scripts to LF before execution, supporting Windows source checkouts.
