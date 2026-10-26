# Native Qt UI build and camera replay

This fork ships a prebuilt Qt UI; Python Raylib settings are not used by that
executable. The restored native UI sources originate from sunnypilot tag
`v2025.002.000`, commit `4962753e47d58f6c86fc284710b6a444ae45788f`.
The missing msgq C++ sources originate from its pinned submodule commit
`fd7bd0df50a95dca3f180705721aa1fa300aef0f`.
Existing Python control modules, schemas and parameter declarations are retained.

## Build

The isolated build regenerates schemas, Qt MOC, translations and resources,
then compiles only the native UI and its dependencies. It does not build Panda
or change any installed device.

```sh
docker build --platform linux/arm64 -t pq35-ui-build -f tools/ui_build/Dockerfile .
docker run --rm --platform linux/arm64 -v "$PWD:/work" pq35-ui-build --device -j4
```

Output: `.native-build/device/ui` and a SHA-256 provenance manifest.
The ARM64 output must pass compatibility checks before being copied to the
tracked `selfdrive/ui/ui` for distribution through the regular updater.

Native PC preview (build the same Dockerfile for the PC architecture):

```sh
python3 tools/ui_build/build.py --preview -j4
PARAMS_ROOT=/tmp/pq35-camera-preview QT_QPA_PLATFORM=offscreen \
  .native-build/pc/camera-preview .native-build/preview
```

This instantiates the actual `SpeedLimitSettings` Qt widget, saves screenshots,
clicks its database-update button and verifies the persisted request. All
preview Params are isolated from any installed openpilot instance.

Cross compilation is supported with `--device --sysroot=/path/to/arm64/sysroot`.
The sysroot must contain ARM64 Qt 5.12 headers/libraries and target dependencies;
native host Qt MOC/lrelease and Cap'n Proto tools must have matching versions.
The builder repairs relocatable links for extracted `/lib` targets and refuses
an incomplete runtime rather than silently selecting static pthread/dl.  The
required target runtime includes `libc.so.6`, `libpthread.so.0`, `libdl.so.2`,
`libz.so.1`, `libstdc++.so.6` and `libgcc_s.so.1`.  OpenSSL 1.1 is linked
static so the executable has no `NEEDED` dependency on the container's
OpenSSL ABI; device EGL/GLES are supplied as matching vendor DSOs together
with their target-side dependencies, including `libsync.so.0` for `libgsl`.
The executable is linked with
`--no-undefined`; missing vendor runtime symbols are a build error.

## Synthetic camera data

```sh
python3 tools/camera_replay.py --scenario all --dryrun
python3 tools/camera_replay.py --scenario approach --publish --repeat 3
```

Publication is for an isolated Linux development environment. It publishes
`liveMapDataSP` only and does not emulate Panda, EPS or a complete vehicle.
The offline scenarios test ahead/behind selection, direction and stale GPS.

## Verification limits

Successful compilation, screenshots and replay establish UI/data behavior.
They do not establish physical ICBM button acceptance or vehicle behavior.
The camera source must also return a valid current snapshot; a retired endpoint
must be reported as an error rather than a successful update.
