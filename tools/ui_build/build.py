#!/usr/bin/env python3
"""Build the restored Qt UI independently of the prebuilt driving stack.

Run in Linux: python3 tools/ui_build/build.py [--device] [-j 4].
Outputs stay in .native-build/{pc,device}; installing them is a separate step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]


def run(command):
  if command[0] == "moc":
    print("MOC " + str(command[-3]), flush=True)
  else:
    print(shlex.join(str(value) for value in command), flush=True)
  return subprocess.check_output([str(value) for value in command], cwd=ROOT, text=True).strip()


def copy_changed(source, target):
  source, target = Path(source), Path(target)
  if not target.exists() or source.read_bytes() != target.read_bytes():
    shutil.copyfile(source, target)


def newer(target, inputs):
  target = Path(target)
  return not target.exists() or any(Path(source).stat().st_mtime > target.stat().st_mtime for source in inputs)


def repair_sysroot_links(sysroot: Path):
  """Make extracted absolute-in-sysroot library links relocatable.

  Device rootfs archives commonly preserve ``/lib/...`` links.  Inside a
  cross sysroot those resolve against the x86 container root and make the
  linker silently fall back to static archives.  Rewrite only absolute links
  whose target exists below this sysroot; unrelated links are left alone.
  """
  sysroot = sysroot.resolve()
  repaired = []
  for directory, _, names in os.walk(sysroot, followlinks=False):
    for name in names:
      link = Path(directory) / name
      if not link.is_symlink():
        continue
      target = os.readlink(link)
      if not target.startswith("/"):
        continue
      in_sysroot = sysroot / target.lstrip("/")
      if not in_sysroot.exists():
        continue
      relative = os.path.relpath(in_sysroot, link.parent)
      link.unlink()
      link.symlink_to(relative)
      repaired.append(str(link))
  if repaired:
    print("Repaired sysroot links: " + str(len(repaired)), flush=True)


def require_target_runtime(sysroot: Path):
  """Reject an incomplete sysroot instead of allowing static fallback."""
  required = (
    sysroot / "lib/aarch64-linux-gnu/libc.so.6",
    sysroot / "lib/aarch64-linux-gnu/libpthread.so.0",
    sysroot / "lib/aarch64-linux-gnu/libdl.so.2",
    sysroot / "usr/lib/aarch64-linux-gnu/libstdc++.so.6",
    sysroot / "usr/lib/aarch64-linux-gnu/libgcc_s.so.1",
    sysroot / "usr/lib/aarch64-linux-gnu/libffi.so.7",
    sysroot / "lib/aarch64-linux-gnu/libsystemd.so.0",
    sysroot / "lib/aarch64-linux-gnu/libpcre.so.3",
    sysroot / "lib/aarch64-linux-gnu/libcom_err.so.2",
    sysroot / "usr/lib/aarch64-linux-gnu/libgcrypt.so.20",
    sysroot / "lib/aarch64-linux-gnu/libgpg-error.so.0",
    sysroot / "lib/aarch64-linux-gnu/liblzma.so.5",
    sysroot / "usr/lib/aarch64-linux-gnu/liblz4.so.1",
  )
  missing = [str(path) for path in required if not path.exists()]
  if not ((sysroot / "lib/aarch64-linux-gnu/libz.so.1").exists() or
          (sysroot / "usr/lib/aarch64-linux-gnu/libz.so.1").exists()):
    missing.append(str(sysroot / "lib/aarch64-linux-gnu/libz.so.1"))
  if missing:
    raise RuntimeError("Incomplete ARM64 sysroot runtime (refusing static fallback): " + ", ".join(missing))


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--device", action="store_true", help="Build for the Comma ARM64 hardware APIs")
  parser.add_argument("--preview", action="store_true", help="Build the actual settings-widget preview instead of the main UI")
  parser.add_argument("--ru-preview", action="store_true", help="Build isolated Russian settings screenshots with actual Qt widgets")
  parser.add_argument("--sysroot", type=Path, help="ARM64 sysroot for native host cross-compilation")
  parser.add_argument("--vendor-libs", type=Path, help="Read-only copy of the device's EGL/GLES libraries for ABI-compatible linking")
  parser.add_argument("-j", type=int, default=4)
  args = parser.parse_args()
  if args.ru_preview:
    if args.device:
      parser.error("--ru-preview is a host-only isolated fixture")
    args.preview = True
  os.chdir(ROOT)
  if args.sysroot:
    repair_sysroot_links(args.sysroot)
    require_target_runtime(args.sysroot)
  if args.device and not args.sysroot and run(["uname", "-m"]) != "aarch64":
    parser.error("--device requires the ARM64 container or an ARM64 build host")
  build = Path(".native-build") / ("device" if args.device else "pc")
  for part in ("obj", "moc", "cereal/gen/cpp", "resources", "schemas"):
    (build / part).mkdir(parents=True, exist_ok=True)

  # Regenerate every schema with the compiler installed in this build image.
  schemas = []
  for name in ("log", "car", "custom", "legacy"):
    source = Path("cereal") / (name + ".capnp")
    content = source.read_text()
    if len(content.strip().splitlines()) == 1 and content.strip().endswith(".capnp"):
      # Git symlinks can be represented as plain link-target files on Windows.
      source = source.parent / content.strip()
    target_schema = build / "schemas" / (name + ".capnp")
    copy_changed(source, target_schema)
    schemas.append(str(target_schema))
  (build / "schemas/include").mkdir(exist_ok=True)
  for include in Path("cereal/include").glob("*.capnp"):
    copy_changed(include, build / "schemas/include" / include.name)
  if any(newer(build / "cereal/gen/cpp" / (Path(schema).name + ".h"), schemas) for schema in schemas):
    run(["capnp", "compile", "-I", "cereal/include", "--src-prefix=" + str(build / "schemas"),
         f"-oc++:{build}/cereal/gen/cpp", *schemas])

  modules = [f"Qt5{name}" for name in ("Widgets", "Gui", "Core", "Network", "Concurrent", "DBus", "Xml", "Svg")]
  if args.sysroot:
    os.environ["PKG_CONFIG_SYSROOT_DIR"] = str(args.sysroot)
    os.environ["PKG_CONFIG_LIBDIR"] = str(args.sysroot / "usr/lib/aarch64-linux-gnu/pkgconfig") + ":" + str(args.sysroot / "usr/share/pkgconfig")
  qt_flags = shlex.split(run(["pkg-config", "--cflags", *modules]))
  qt_libs = shlex.split(run(["pkg-config", "--libs", *modules]))
  qt_version = run(["pkg-config", "--modversion", "Qt5Gui"])
  qt_gui_include = next(Path(flag[2:]) for flag in qt_flags if flag.startswith("-I") and flag.endswith("/QtGui"))
  qt_flags += ["-I" + str(qt_gui_include / qt_version / "QtGui")]
  flags = ["-std=c++17", "-fPIC", "-O2", "-pthread", "-DSUNNYPILOT", "-DQT_NO_DEBUG",
           "-DQT_MESSAGELOGCONTEXT", "-Wno-deprecated-declarations", "-Wno-c99-designator", "-I" + str(build), "-I.",
           "-Imsgq_repo", "-Ithird_party", "-Ithird_party/json11", "-Ithird_party/qrcode", "-Ithird_party/linux/include",
           "-Ithird_party/libyuv/include", "-Ithird_party/snpe/include", "-Ithird_party/acados/include",
           "-Ithird_party/opencl/include", *qt_flags]
  if args.device:
    flags += ["-DQCOM2", "-mcpu=cortex-a57"]
  target_flags = ["--target=aarch64-linux-gnu", "--sysroot=" + str(args.sysroot),
                  "--gcc-toolchain=" + str(args.sysroot / "usr")] if args.sysroot else []
  flags += target_flags

  # compile_commands records the exact UI translation units shipped by this fork.
  commands = json.loads(Path("compile_commands.json").read_text())
  sources = sorted({entry["file"].replace("/data/openpilot/", "") for entry in commands
                    if "/selfdrive/ui/" in entry["file"] and entry["file"].endswith(".cc")
                    and "/moc_" not in entry["file"] and "/tests/" not in entry["file"]})
  # Presentation helper is absent from the shipped compile database.
  sources.append("selfdrive/ui/qt/onroad/alerts_ru.cpp")
  if args.preview:
    sources.remove("selfdrive/ui/main.cc")
    sources.append("tools/ui_build/ru_preview.cc" if args.ru_preview else "tools/ui_build/preview.cc")
  sources += ["common/" + name + ".cc" for name in ("params", "swaglog", "util", "watchdog", "ratekeeper", "clutil")]
  sources += ["common/transformations/orientation.cc", "common/transformations/coordinates.cc",
              "cereal/messaging/socketmaster.cc", "third_party/json11/json11.cpp", "third_party/qrcode/QrCode.cc"]
  sources += ["msgq_repo/msgq/" + name + ".cc" for name in ("ipc", "event", "impl_zmq", "impl_msgq", "impl_fake", "msgq")]
  sources += ["msgq_repo/msgq/visionipc/" + name + ".cc" for name in
              ("visionipc", "visionipc_server", "visionipc_client", "visionbuf", "visionbuf_ion" if args.device else "visionbuf_cl")]
  sources += [str(build / "cereal/gen/cpp" / (Path(schema).name + ".c++")) for schema in schemas]
  for header in sorted(Path("selfdrive/ui").rglob("*.h")):
    if "/tests/" in header.as_posix() or "Q_OBJECT" not in header.read_text(errors="replace"):
      continue
    target = build / "moc" / (header.as_posix().replace("/", "_") + ".cc")
    if newer(target, [header]):
      run(["moc", *[flag for flag in flags if flag.startswith(("-D", "-I"))], header, "-o", target])
    sources.append(str(target))

  languages_path = Path("selfdrive/ui/translations/languages.json")
  languages = json.loads(languages_path.read_text())
  qrc = ET.Element("RCC")
  resource = ET.SubElement(qrc, "qresource")
  for language in languages.values():
    source = Path("selfdrive/ui/translations") / (language + ".ts")
    target = (build / "resources" / (language + ".qm")).resolve()
    if newer(target, [source]):
      run(["lrelease", source, "-qm", target])
    ET.SubElement(resource, "file", {"alias": language}).text = str(target)
  translation_qrc = build / "resources/translations.qrc"
  qrc_bytes = ET.tostring(qrc)
  if not translation_qrc.exists() or translation_qrc.read_bytes() != qrc_bytes:
    translation_qrc.write_bytes(qrc_bytes)
  assets = build / "resources/assets.cc"
  if newer(assets, [translation_qrc, "selfdrive/assets/assets.qrc", *list((build / "resources").glob("*.qm"))]):
    run(["rcc", "selfdrive/assets/assets.qrc", translation_qrc, "-o", assets])
  sources.append(str(assets))

  missing = [source for source in sources if not Path(source).is_file()]
  if missing:
    raise RuntimeError("Missing native sources: " + ", ".join(missing))
  # Keep the schema runtime and crypto ABI self-contained: the device uses
  # newer Cap'n Proto/OpenSSL libraries than the Qt 5.12 build container.
  libs = [*qt_libs, "-Wl,--as-needed", "-Wl,-Bstatic", "-lcapnp", "-lkj", "-lssl", "-lcrypto", "-Wl,-Bdynamic",
          "-Wl,--exclude-libs,ALL", "-lzmq", "-lOpenCL", "-lEGL", "-lGLESv2", "-lGL",
          "-lwayland-client", "-lpthread", "-ldl", "-lm", "-Wl,--no-undefined"]
  if args.preview:
    libs += shlex.split(run(["pkg-config", "--libs", "Qt5Test"]))
  if args.sysroot:
    for directory in (args.sysroot / "lib/aarch64-linux-gnu", args.sysroot / "usr/lib/aarch64-linux-gnu"):
      libs.insert(0, "-Wl,-rpath-link," + str(directory))
  if args.vendor_libs:
    if not args.device:
      parser.error("--vendor-libs is only for --device")
    required_vendor = ("libEGL_adreno.so", "libGLESv2_adreno.so", "libOpenCL.so",
                       "libadreno_utils.so", "libcutils.so.0", "libgsl.so", "liblog.so.0")
    missing_vendor = [name for name in required_vendor if not (args.vendor_libs / name).is_file()]
    if missing_vendor:
      parser.error("Incomplete vendor bundle: " + ", ".join(missing_vendor))
    sync_candidates = (args.vendor_libs / "libsync.so.0",
                       args.sysroot / "lib/aarch64-linux-gnu/libsync.so.0",
                       args.sysroot / "usr/lib/aarch64-linux-gnu/libsync.so.0")
    if not any(path.is_file() for path in sync_candidates):
      parser.error("Missing vendor/sysroot dependency: libsync.so.0")
    for name in ("EGL", "GLESv2"):
      library = args.vendor_libs / ("lib" + name + "_adreno.so")
      if not library.is_file():
        parser.error("Missing device library: " + str(library))
      libs[libs.index("-l" + name)] = str(library)
    libs[libs.index("-lOpenCL")] = str(args.vendor_libs / "libOpenCL.so")
    libs.insert(0, "-Wl,-rpath-link," + str(args.vendor_libs))
  # PC and ARM builds can run concurrently; their Ninja dependency databases
  # must not share the repository root (.ninja_deps/.ninja_log).
  # Every UI unit imports the large generated cereal schema. Compile that
  # unchanged common header once per architecture/toolchain, from this source
  # snapshot, rather than reparsing it for every Qt MOC translation unit.
  pch_source = build / "schema_pch.h"
  pch_content = '#include "cereal/gen/cpp/log.capnp.h"\n#include "cereal/gen/cpp/custom.capnp.h"\n'
  if not pch_source.exists() or pch_source.read_text() != pch_content:
    pch_source.write_text(pch_content)
  pch = build / "schema_pch.pch"
  ninja = ["builddir = " + str(build), "rule pch",
           "  command = clang++ " + shlex.join(flags) + " -x c++-header $in -o $out",
           "  description = PCH cereal schemas",
           f"build {pch}: pch {pch_source} | " + " ".join(str(build / "cereal/gen/cpp" / (Path(schema).name + ".h")) for schema in schemas),
           "rule cxx", "  command = clang++ " + shlex.join(flags + ["-include-pch", str(pch)]) + " -MMD -MF $out.d -c $in -o $out",
           "  description = CXX $in", "  depfile = $out.d", "  deps = gcc", "rule link",
           "  command = clang++ " + shlex.join(target_flags) + " $in -o $out " + shlex.join(libs), "  description = LINK $out"]
  objects = []
  for source in sources:
    target = build / "obj" / (source.replace("/", "_").replace("\\", "_") + ".o")
    objects.append(str(target))
    ninja.append(f"build {target}: cxx {source} | {pch}")
  target = build / ("ru-preview" if args.ru_preview else "camera-preview" if args.preview else "ui")
  ninja += [f"build {target}: link " + " ".join(objects), f"default {target}"]
  manifest = build / "build.ninja"
  manifest.write_text("\n".join(ninja) + "\n")
  subprocess.run(["ninja", "-f", str(manifest), "-j", str(args.j)], check=True)
  source_commit = os.environ.get("UI_SOURCE_COMMIT") or run(["git", "rev-parse", "HEAD"])
  provenance = {"git_commit": source_commit, "target": "device" if args.device else "pc",
                "qt_version": run(["pkg-config", "--modversion", "Qt5Widgets"]),
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
  provenance["authorized_ui_source_commit"] = "aabf70ac1f211c343cca0181f77777dd12c1ff6e"
  provenance["compiled_source_sha256"] = {source: hashlib.sha256(Path(source).read_bytes()).hexdigest()
                                           for source in sorted(set(sources))}
  provenance["project_header_sha256"] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                         for directory in ("selfdrive/ui", "common", "msgq_repo/msgq", "system/hardware")
                                         for path in sorted(Path(directory).rglob("*.h"))}
  provenance["schema_sha256"] = {schema: hashlib.sha256(Path(schema).read_bytes()).hexdigest() for schema in schemas}
  provenance["compiler"] = run(["clang++", "--version"]).splitlines()[0]
  provenance["capnp_compiler"] = run(["capnp", "--version"])
  provenance["source_state"] = "cam working snapshot; base commit plus recorded source/header hashes"
  (build / "manifest.json").write_text(json.dumps(provenance, indent=2) + "\n")
  print(json.dumps({key: value for key, value in provenance.items() if not isinstance(value, dict)}, indent=2))


if __name__ == "__main__":
  main()
