#!/usr/bin/env python3
"""Cross-build only common.params_pyx for the device's CPython 3.12 ABI.

Run this inside pq35-ui-host after extracting matching ARM64 Python 3.12
headers from the official python:3.12-slim-bullseye image. All outputs go to
.native-build/params-persistence; the tracked common/params_pyx.so is untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / ".native-build/params-persistence"


def sha256(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as stream:
    for block in iter(lambda: stream.read(1024 * 1024), b""):
      digest.update(block)
  return digest.hexdigest()


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--sysroot", type=Path, default=Path("/opt/sysroot"))
  parser.add_argument("--python-include", type=Path, required=True,
                      help="ARM64 Python 3.12 include directory from the official image")
  parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
  parser.add_argument("--compiler", default="clang++")
  args = parser.parse_args()

  sysroot = args.sysroot.resolve()
  python_include = args.python_include.resolve()
  output = args.output.resolve()
  patchlevel = python_include / "patchlevel.h"
  pyconfig = python_include / "pyconfig.h"
  for required in (patchlevel, pyconfig, ROOT / "common/params_pyx.cpp",
                   ROOT / "common/params.cc", ROOT / "common/params_keys.h"):
    if not required.is_file():
      parser.error(f"required input is missing: {required}")
  version_header = patchlevel.read_text(encoding="ascii", errors="replace")
  major = re.search(r"^\s*#define\s+PY_MAJOR_VERSION\s+(\d+)", version_header, re.MULTILINE)
  minor = re.search(r"^\s*#define\s+PY_MINOR_VERSION\s+(\d+)", version_header, re.MULTILINE)
  if major is None or minor is None or (major.group(1), minor.group(1)) != ("3", "12"):
    parser.error(f"headers are not CPython 3.12: {patchlevel}")
  if not (sysroot / "usr/lib/aarch64-linux-gnu/libzmq.so").exists():
    parser.error(f"ARM64 ZeroMQ linker library is missing from {sysroot}")

  candidate_dir = output / "candidate" / "common"
  object_dir = output / "obj"
  schema_dir = output / "schemas"
  generated_root = output / "generated"
  candidate_dir.mkdir(parents=True, exist_ok=True)
  object_dir.mkdir(parents=True, exist_ok=True)
  schema_dir.mkdir(parents=True, exist_ok=True)
  generated_root.mkdir(parents=True, exist_ok=True)

  # The Ubuntu 20 sysroot ships Cap'n Proto 0.7. Generate matching headers in
  # our output tree instead of including the repository's newer generated code.
  for name in ("log", "car", "custom", "legacy"):
    shutil.copyfile(ROOT / "cereal" / f"{name}.capnp", schema_dir / f"{name}.capnp")
  include_dir = schema_dir / "include"
  include_dir.mkdir(exist_ok=True)
  for source in (ROOT / "cereal/include").glob("*.capnp"):
    shutil.copyfile(source, include_dir / source.name)
  generated_headers = generated_root / "cereal/gen/cpp"
  generated_headers.mkdir(parents=True, exist_ok=True)
  capnp_command = [
    "capnp", "compile", "-I", str(ROOT / "cereal/include"),
    f"--src-prefix={schema_dir}", f"-oc++:{generated_headers}",
    *(str(schema_dir / f"{name}.capnp") for name in ("log", "car", "custom", "legacy")),
  ]
  print("+", " ".join(capnp_command), flush=True)
  subprocess.run(capnp_command, cwd=ROOT, check=True)

  target = candidate_dir / "params_pyx.so"
  sources = (
    ROOT / "common/params_pyx.cpp",
    ROOT / "common/params.cc",
    ROOT / "common/util.cc",
    ROOT / "common/swaglog.cc",
    ROOT / "third_party/json11/json11.cpp",
  )
  common_flags = [
    "--target=aarch64-linux-gnu",
    f"--sysroot={sysroot}",
    f"--gcc-toolchain={sysroot / 'usr'}",
    "-std=c++17", "-fPIC", "-O1", "-pthread", "-I" + str(generated_root), "-I" + str(ROOT),
    "-I" + str(ROOT / "msgq_repo"), "-I" + str(ROOT / "third_party"),
    "-I" + str(python_include), "-I" + str(sysroot / "usr/include/eigen3"),
  ]
  objects: list[Path] = []
  for source in sources:
    obj = object_dir / (source.name + ".o")
    command = [args.compiler, *common_flags, "-c", str(source), "-o", str(obj)]
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)
    objects.append(obj)

  link_command = [
    args.compiler, "--target=aarch64-linux-gnu", f"--sysroot={sysroot}",
    f"--gcc-toolchain={sysroot / 'usr'}", "-shared", "-pthread", "-Wl,--as-needed",
    *map(str, objects), f"-L{sysroot / 'usr/lib/aarch64-linux-gnu'}",
    "-lzmq", "-lcapnp", "-lkj", "-o", str(target),
  ]
  print("+", " ".join(link_command), flush=True)
  subprocess.run(link_command, cwd=ROOT, check=True)

  readelf = subprocess.run(["readelf", "-h", str(target)], check=True,
                           text=True, capture_output=True).stdout
  if "AArch64" not in readelf:
    parser.error(f"output is not AArch64: {target}")
  report = output / "candidate.json"
  report.write_text(
    '{\n'
    f'  "artifact": "{target.as_posix()}",\n'
    f'  "sha256": "{sha256(target)}",\n'
    '  "python_abi": "CPython 3.12 (aarch64)",\n'
    f'  "sysroot": "{sysroot.as_posix()}"\n'
    '}\n', encoding="utf-8")
  print(f"artifact={target}")
  print(f"sha256={sha256(target)}")
  print(f"report={report}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
