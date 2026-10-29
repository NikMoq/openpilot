# Disposable native Linux camera/runtime checks

These checks use actual native Params, msgq shared-memory IPC, Cap'n Proto,
OsmMapData/CameraSpeedSource, SubMaster, Resolver, SLA, LongitudinalPlannerSP,
VCruiseHelper and ICBM. Native dependencies are rebuilt from current cam sources;
no installed application libraries, previous objects or external source checkouts
are used. Standard compiler/SDK/Python packages come from the dependency image.
The synthetic inputs do not execute a model or an MPC solver, send physical CAN,
flash hardware, or establish bench/road readiness.

From the cam checkout in PowerShell:

```powershell
docker build -t pq35-cam-native-runtime:local -f tools/tests/Dockerfile.camera-runtime-deps tools/tests
$camSource = (Get-Location).Path
docker run --rm --mount "type=bind,source=$camSource,target=/cam,readonly" -w /tmp -e PQ35_RUNTIME_SMOKE=1 -e PARAMS_ROOT=/data/params pq35-cam-native-runtime:local sh -c 'python /cam/tools/tests/build_native_runtime.py --output /tmp/current-runtime && python /tmp/current-runtime/run_checks.py'
```

No host devices or vehicle network are mounted. Source remains read-only; all
objects, regenerated Cython/C++ schema output and test Params live in the
disposable container. The builder requires a new/empty output outside the source
checkout and rejects `/TICI`. It records native module SHA-256 values in
`native-modules.json`. Clang compiles Params because existing util.cc uses a
designated-initializer order accepted by Clang; this QA does not rewrite that
production source to accommodate GCC.

The generated runner retains explicit `PQ35_RUNTIME_SMOKE=1` and
`PARAMS_ROOT=/data/params` requirements. The two runtime test modules continue to
skip without opt-in. Only the override tests control the wall-clock input;
message receipt uses actual monotonic time and actual IPC. A supplied DEC crash
counter is the declared MPC boundary.

The original core Resolver/SLA tests run through the explicit
`core_speed_limit_compat` pytest plugin. It corrects two test portability issues:
the old fresh-GPS mock stored monotonic seconds in a Unix timestamp field, and
Windows may materialize the original opendbc symlink as a text file. The plugin
uses the original opendbc source-data target and corrects only the fresh mock's
clock epoch. It substitutes no production state machine, interface, Params or IPC.

Runtime scenarios cover native parameter types/path compatibility, independent
warning/control selection, supported/unknown camera metadata, fixed reaction
threshold, native offset once (including raw20+15=>35), four-source minimum,
GPS-loss ceiling/offset hold, disabled control, driver memory, gas/brake priority,
manual changes, delayed native feedback, held UP/DOWN increments and final release,
held buttons across passage, native SET after braking, confirmed passage and a
separate equal-limit camera. Assertions observe ICBM commands but never transmit
them to hardware.

Verified on 2026-10-29 with CPython 3.12: the saved builder regenerated all three
native modules into a new output directory, and its generated runner passed
**89 tests**: 44 original core Resolver/SLA tests, 19 production runtime tests and
26 override/runtime tests. This includes stable held-button release with no later
feedback change, delayed final feedback, and actual SCC-V geometry changing stock
speed without rewriting the remembered manual target.
