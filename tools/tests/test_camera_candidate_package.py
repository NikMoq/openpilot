"""Reject stale native build inputs and architecture mismatches before packaging."""
import hashlib
import importlib.util
from pathlib import Path
import struct

import pytest


spec = importlib.util.spec_from_file_location('camera_candidate_packager', Path(__file__).resolve().parents[1] / 'prepare_camera_candidate.py')
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)


def test_artifact_architecture_and_build_hash_are_required(tmp_path):
  path = tmp_path / 'candidate'
  header = bytearray(20)
  header[:6] = b'\x7fELF\x02\x01'
  struct.pack_into('<H', header, 18, 183)
  path.write_bytes(header)
  candidate.check_artifact(path, {'sha256': candidate.sha(path)})
  with pytest.raises(ValueError, match='hash'):
    candidate.check_artifact(path, {'sha256': '0' * 64})
  struct.pack_into('<H', header, 18, 62)
  path.write_bytes(header)
  with pytest.raises(ValueError, match='AArch64'):
    candidate.check_artifact(path, {'sha256': candidate.sha(path)})


def test_changed_source_and_outside_paths_are_rejected(tmp_path, monkeypatch):
  monkeypatch.setattr(candidate, 'ROOT', tmp_path)
  path = tmp_path / 'input.h'
  path.write_bytes(b'compiled input')
  compiled_hash = hashlib.sha256(path.read_bytes()).hexdigest()
  candidate.check_current_source('input.h', compiled_hash)
  path.write_bytes(b'changed after compile')
  with pytest.raises(ValueError, match='changed'):
    candidate.check_current_source('input.h', compiled_hash)
  with pytest.raises(ValueError, match='changed'):
    candidate.check_current_source('../outside', compiled_hash)


def test_dependency_report_must_match_this_ui():
  manifest = {'sha256': 'current-ui'}
  report = {'candidate_sha256': 'current-ui', 'sdk_dependency_closure_complete': True, 'missing': []}
  candidate.check_ui_abi(report, manifest)
  for change in ({'candidate_sha256': 'another-build'}, {'missing': ['libEGL_adreno.so']},
                 {'sdk_dependency_closure_complete': False}):
    with pytest.raises(ValueError, match='dependency'):
      candidate.check_ui_abi(report | change, manifest)


def test_runtime_overlay_excludes_tests_and_firmware(monkeypatch):
  monkeypatch.setattr(candidate, 'git', lambda *args: '\n'.join([
    'sunnypilot/mapd/camera_speed_source.py', 'cereal/custom.capnp',
    'tools/tests/test_example.py', 'sunnypilot/a/tests/test_example.py',
    'selfdrive/test/example.py', 'panda/board/obj/panda.bin.signed',
    'selfdrive/ui/ui', 'common/params_keys.h']))
  assert candidate.runtime_files() == ['cereal/custom.capnp', 'sunnypilot/mapd/camera_speed_source.py']
