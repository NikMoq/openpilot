#!/usr/bin/env python3
"""Verify the original and candidate Params bindings in isolated temp roots.

Run with the matching ARM64 CPython. The test writes only inside temporary
directories and never uses the device's Params path.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types


ROOT = Path(__file__).resolve().parents[2]
CAMERA_KEYS = (
  "SpeedCameraDatabasePath",
  "SpeedCameraDatabaseUrl",
  "SpeedCameraDatabaseUpdatePeriodHours",
  "SpeedCameraDatabaseUpdatedAt",
  "SpeedCameraDatabaseVersion",
  "SpeedCameraDatabaseStatus",
  "SpeedCameraDatabaseError",
  "SpeedCameraDatabaseCount",
  "SpeedCameraDatabaseCacheMaxAgeHours",
  "SpeedCameraEnabled",
  "SpeedCameraControlEnabled",
  "SpeedCameraSpeedOffsetKph",
  "SpeedCameraAlertDistanceMeters",
)
CAMERA_VALUES = {
  "SpeedCameraDatabasePath": "/data/speed-camera/cameras.sqlite",
  "SpeedCameraDatabaseUrl": "https://example.invalid/cameras.json",
  "SpeedCameraDatabaseUpdatePeriodHours": "6",
  "SpeedCameraDatabaseUpdatedAt": "2026-10-26T12:34:56Z",
  "SpeedCameraDatabaseVersion": "camera-db-test-17",
  "SpeedCameraDatabaseStatus": "ready",
  "SpeedCameraDatabaseError": "none",
  "SpeedCameraDatabaseCount": "123",
  "SpeedCameraDatabaseCacheMaxAgeHours": "24",
  "SpeedCameraEnabled": True,
  "SpeedCameraControlEnabled": False,
  "SpeedCameraSpeedOffsetKph": "19",
  "SpeedCameraAlertDistanceMeters": "200",
}


def camera_file(params, key: str) -> Path:
  return Path(params.get_param_path(key))


def expected_camera_bytes(key: str) -> bytes:
  value = CAMERA_VALUES[key]
  return (b"1" if value else b"0") if isinstance(value, bool) else value.encode("utf-8")


def sha256(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as stream:
    for block in iter(lambda: stream.read(1024 * 1024), b""):
      digest.update(block)
  return digest.hexdigest()


def load_binding(path: Path):
  if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
  import common
  common.__path__.insert(0, str(path.parent))
  # The checked-out repository materializes its openpilot/* symlinks as text
  # files on Windows. Recreate the package aliases needed by swaglog imports.
  import openpilot
  openpilot.__path__.insert(0, str(ROOT))
  import system
  sys.modules["openpilot.common"] = common
  sys.modules["openpilot.system"] = system
  openpilot.common = common
  openpilot.system = system
  # The binding only uses cloudlog on conversion-error paths. Keep the import
  # probe focused on Params and avoid installing the application's full stack.
  swaglog = types.ModuleType("openpilot.common.swaglog")
  swaglog.cloudlog = types.SimpleNamespace(warning=lambda *args, **kwargs: None)
  sys.modules["openpilot.common.swaglog"] = swaglog
  spec = importlib.util.spec_from_file_location("common.params_pyx", path)
  if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load extension: {path}")
  module = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = module
  spec.loader.exec_module(module)
  return module


def check_original(path: Path) -> dict:
  module = load_binding(path)
  with tempfile.TemporaryDirectory(prefix="params-old-binding-") as temp:
    params_root = Path(temp) / "params"
    params = module.Params(str(params_root))
    rejected = []
    for key in CAMERA_KEYS:
      try:
        params.check_key(key)
      except module.UnknownKeyName:
        rejected.append(key)
      else:
        raise AssertionError(f"original extension unexpectedly accepts {key}")

    # The adapter can write these names directly to its Params directory even
    # though the old native registry rejects them. Reproduce those exact files.
    seeded = {}
    for key in CAMERA_KEYS:
      target = camera_file(params, key)
      expected = expected_camera_bytes(key)
      target.write_bytes(expected)
      seeded[key] = target
      if target.read_bytes() != expected:
        raise AssertionError(f"failed to seed raw camera file {key}")
    params.clear_all(module.ParamKeyFlag.CLEAR_ON_MANAGER_START)
    del params
    params = module.Params(str(params_root))
    remaining = [key for key, target in seeded.items() if target.exists()]
    if remaining:
      raise AssertionError(f"original clear_all left raw camera files: {remaining}")
  return {
    "camera_keys_rejected": rejected,
    "raw_camera_files_seeded": len(seeded),
    "raw_camera_files_deleted_by_manager_start_clear": list(CAMERA_KEYS),
  }


def check_candidate(path: Path) -> dict:
  module = load_binding(path)
  with tempfile.TemporaryDirectory(prefix="params-new-binding-") as temp:
    params_root = Path(temp) / "params"
    params = module.Params(str(params_root))
    missing = []
    for key in CAMERA_KEYS:
      try:
        params.check_key(key)
      except module.UnknownKeyName:
        missing.append(key)
    if missing:
      raise AssertionError(f"candidate registry missing camera keys: {missing}")

    bool_type = module.ParamKeyType.BOOL
    string_type = module.ParamKeyType.STRING
    for key in CAMERA_KEYS:
      key_type = params.get_type(key)
      value = CAMERA_VALUES[key]
      if isinstance(value, bool):
        if key_type != bool_type:
          raise AssertionError(f"{key} should be registered as BOOL, got {key_type}")
        params.put_bool(key, value)
      else:
        if key_type != string_type:
          raise AssertionError(f"{key} should be registered as STRING, got {key_type}")
        params.put(key, value)
      if camera_file(params, key).read_bytes() != expected_camera_bytes(key):
        raise AssertionError(f"wrong initial bytes for camera key {key}")

    cases = (
      (module.ParamKeyFlag.CLEAR_ON_MANAGER_START, "CameraDebugExpGain", "startup"),
      (module.ParamKeyFlag.CLEAR_ON_ONROAD_TRANSITION, "MapAdvisorySpeedLimit", "onroad"),
      (module.ParamKeyFlag.CLEAR_ON_OFFROAD_TRANSITION, "JoystickDebugMode", "offroad"),
      (module.ParamKeyFlag.CLEAR_ON_IGNITION_ON, "DriverTooDistracted", "ignition"),
      (module.ParamKeyFlag.DEVELOPMENT_ONLY, "AlphaLongitudinalEnabled", "development"),
    )
    cleared_ephemeral = []
    checked_clears = []
    for flag, ephemeral, label in cases:
      if ephemeral == "MapAdvisorySpeedLimit":
        params.put(ephemeral, 17.5)
        ephemeral_bytes = b"17.5"
      else:
        params.put_bool(ephemeral, True)
        ephemeral_bytes = b"1"
      ephemeral_path = camera_file(params, ephemeral)
      if ephemeral_path.read_bytes() != ephemeral_bytes:
        raise AssertionError(f"wrong pre-clear bytes for {ephemeral}")
      params.clear_all(flag)
      del params
      params = module.Params(str(params_root))
      if ephemeral_path.exists():
        raise AssertionError(f"{label} clear did not remove ephemeral {ephemeral}")
      for key in CAMERA_KEYS:
        actual = camera_file(params, key).read_bytes()
        expected = expected_camera_bytes(key)
        if actual != expected:
          raise AssertionError(
            f"{label} clear changed bytes for {key}: expected {expected!r}, got {actual!r}")
      cleared_ephemeral.append(ephemeral)
      checked_clears.append({"clear": label, "camera_files_byte_exact_after_reopen": len(CAMERA_KEYS)})

    params.clear_all(module.ParamKeyFlag.ALL)
    del params
    params = module.Params(str(params_root))
    remaining = [key for key in CAMERA_KEYS if camera_file(params, key).exists()]
    if remaining:
      raise AssertionError(f"full clear left camera params behind: {remaining}")
  return {
    "camera_values_written": {key: expected_camera_bytes(key).decode("utf-8") for key in CAMERA_KEYS},
    "camera_keys_byte_exact_after_each_clear_and_reopen": checked_clears,
    "ephemeral_keys_cleared": cleared_ephemeral,
    "full_clear_removed_all_camera_keys": True,
  }


def worker(mode: str, extension: Path) -> dict:
  if mode == "original":
    return check_original(extension)
  return check_candidate(extension)


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--original", type=Path, default=ROOT / "common/params_pyx.so")
  parser.add_argument("--candidate", type=Path,
                      default=ROOT / ".native-build/params-persistence/candidate/common/params_pyx.so")
  parser.add_argument("--worker", choices=("original", "candidate"), help=argparse.SUPPRESS)
  parser.add_argument("--extension", type=Path, help=argparse.SUPPRESS)
  args = parser.parse_args()
  if args.worker:
    if args.extension is None:
      parser.error("--extension is required in worker mode")
    result = worker(args.worker, args.extension.resolve())
    print(json.dumps(result, indent=2))
    return 0

  for path in (args.original, args.candidate):
    if not path.is_file():
      parser.error(f"extension not found: {path}")
  if sys.version_info[:2] != (3, 12):
    parser.error(f"run under CPython 3.12; got {sys.version.split()[0]}")

  results = {}
  for mode, extension in (("original", args.original), ("candidate", args.candidate)):
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", mode,
               "--extension", str(extension.resolve())]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    try:
      completed = subprocess.run(command, cwd=ROOT, env=env, check=True,
                                 text=True, capture_output=True)
    except subprocess.CalledProcessError as error:
      raise RuntimeError(f"{mode} verification failed:\n{error.stdout}{error.stderr}") from error
    results[mode] = json.loads(completed.stdout)
  results["abi"] = f"CPython {sys.version_info.major}.{sys.version_info.minor} {os.uname().machine}"
  results["original_sha256"] = sha256(args.original)
  results["candidate_sha256"] = sha256(args.candidate)
  print(json.dumps(results, indent=2))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
