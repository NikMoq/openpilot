"""Deployment regression: updated sources must not ship with the stable UI."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[2]


def checker():
  spec = importlib.util.spec_from_file_location('waze_package_check', ROOT / 'tools/ui_build/check_waze_package.py')
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def package_copy(destination):
  module = checker()
  for relative in (*module.ARTIFACTS, *module.SOURCES, module.MANIFEST):
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / relative, target)
  return module


def test_tracked_waze_device_package_contains_controls_and_current_params():
  assert checker().validate(ROOT)['status'] == 'PASS'


def test_native_source_edit_requires_rebuilt_package(tmp_path):
  module = package_copy(tmp_path)
  source = tmp_path / 'common/params_keys.h'
  source.write_bytes(source.read_bytes() + b'\n// synthetic source edit\n')
  with pytest.raises(ValueError, match='without rebuilding'):
    module.validate(tmp_path)


def test_matching_manifest_cannot_make_an_old_registry_pass(tmp_path):
  module = package_copy(tmp_path)
  ui = tmp_path / 'selfdrive/ui/ui'
  # Emulate a packaged UI whose registry has no Waze enable key. Even a freshly
  # recorded artifact hash must not authorize this incomplete executable.
  ui.write_bytes(ui.read_bytes().replace(b'SpeedCameraWazeEnabled', b'SpeedCameraGoneEnabled'))
  manifest_path = tmp_path / module.MANIFEST
  manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
  manifest['artifacts']['selfdrive/ui/ui'] = hashlib.sha256(ui.read_bytes()).hexdigest()
  manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
  with pytest.raises(ValueError, match='lacks the Waze parameter registry'):
    module.validate(tmp_path)
