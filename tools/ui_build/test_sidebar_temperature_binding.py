#!/usr/bin/env python3
"""Exercise the SidebarTemperatureMode native registry in isolated Params roots.

Run with ARM64 CPython 3.12 and a candidate params_pyx.so. All Params writes
remain in temporary directories; this does not probe a device's /data/params.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types


KEY = "SidebarTemperatureMode"
ROOT = Path(os.environ["PQ35_REPO_ROOT"]) if "PQ35_REPO_ROOT" in os.environ else Path(__file__).resolve().parents[2]


def load_binding(path: Path):
  sys.path.insert(0, str(ROOT))
  import common
  common.__path__.insert(0, str(path.parent))
  import openpilot
  openpilot.__path__.insert(0, str(ROOT))
  import system
  sys.modules["openpilot.common"] = common
  sys.modules["openpilot.system"] = system
  openpilot.common = common
  openpilot.system = system
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


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("candidate", type=Path)
  args = parser.parse_args()
  extension = args.candidate.resolve(strict=True)
  assert sys.version_info[:2] == (3, 12), sys.version
  module = load_binding(extension)
  with tempfile.TemporaryDirectory(prefix="pq35-sidebar-temp-") as directory:
    root = Path(directory) / "params"
    params = module.Params(str(root))
    params.check_key(KEY)
    assert params.get_type(KEY) == module.ParamKeyType.INT
    assert params.get_default_value(KEY) == 0
    assert KEY.encode() in params.all_keys(module.ParamKeyFlag.PERSISTENT)
    assert KEY.encode() in params.all_keys(module.ParamKeyFlag.BACKUP)

    # The default is registry metadata; each stored value must survive every
    # manager startup clear and a fresh Params instance, byte for byte.
    for value in range(5):
      params.put(KEY, value)
      target = Path(params.get_param_path(KEY))
      expected = str(value).encode("ascii")
      assert target.read_bytes() == expected, (value, target.read_bytes())
      params.clear_all(module.ParamKeyFlag.CLEAR_ON_MANAGER_START)
      del params
      params = module.Params(str(root))
      assert params.get(KEY) == value
      assert Path(params.get_param_path(KEY)).read_bytes() == expected

    # Unknown raw files are removed by manager cleanup while this registered
    # persistent key remains. A full clear removes it as expected.
    unknown = Path(params.get_param_path("PQ35UnknownSidebarTempProbe"))
    unknown.write_bytes(b"unknown")
    params.clear_all(module.ParamKeyFlag.CLEAR_ON_MANAGER_START)
    assert not unknown.exists()
    assert Path(params.get_param_path(KEY)).read_bytes() == b"4"
    params.clear_all(module.ParamKeyFlag.ALL)
    del params
    params = module.Params(str(root))
    assert not Path(params.get_param_path(KEY)).exists()

  print(json.dumps({
    "candidate_sha256": hashlib.sha256(extension.read_bytes()).hexdigest(),
    "abi": f"CPython {sys.version_info.major}.{sys.version_info.minor} {os.uname().machine}",
    "key": KEY,
    "type": "INT",
    "flags": ["PERSISTENT", "BACKUP"],
    "default": 0,
    "stored_values_byte_exact_after_manager_clear_and_reopen": list(range(5)),
    "unknown_file_removed_by_manager_clear": True,
    "key_removed_by_all_clear": True,
  }, indent=2))


if __name__ == "__main__":
  main()
