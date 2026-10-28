"""Source staging provenance and fail-before-output regression tests."""
import hashlib
import json
from pathlib import Path
import pytest
from stage_sources import source_path, stage_sources


def inputs(tmp_path):
  foundation, port = tmp_path / 'foundation', tmp_path / 'port'
  path = foundation / 'common/example.h'
  path.parent.mkdir(parents=True)
  path.write_bytes(b'// foundation\n')
  port.mkdir()
  manifest = tmp_path / 'manifest.sha256'
  manifest.write_text(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  common/example.h\n')
  return foundation, port, tmp_path / 'staged', manifest


def test_overlay_and_foundation_provenance(tmp_path):
  foundation, port, output, manifest = inputs(tmp_path)
  overlay = port / 'common/example.h'
  overlay.parent.mkdir()
  overlay.write_bytes(b'// current port\n')
  stage_sources(foundation, port, output, manifest)
  assert (output / 'common/example.h').read_bytes() == overlay.read_bytes()
  provenance = json.loads((output / 'staged_inputs.json').read_text())['common/example.h']
  assert provenance['origin'] == 'current-port'
  assert provenance['sha256'] == hashlib.sha256(overlay.read_bytes()).hexdigest()
  assert provenance['foundation_sha256'] == hashlib.sha256(b'// foundation\n').hexdigest()


def test_bad_foundation_rejected_even_when_overlay_exists(tmp_path):
  foundation, port, output, manifest = inputs(tmp_path)
  (foundation / 'common/example.h').write_bytes(b'tampered')
  (port / 'common').mkdir()
  (port / 'common/example.h').write_bytes(b'legitimate overlay')
  with pytest.raises(ValueError, match='hash mismatch'):
    stage_sources(foundation, port, output, manifest)
  assert not output.exists()


@pytest.mark.parametrize('relative', [
  'common/__pycache__/file.py', 'common/cache.pyc', 'common/module.so',
  'selfdrive/pandad/tests/bootstub.bin', 'selfdrive/pandad/pandad',
  'cereal/messaging/bridge', 'arm_obj.previous/file.cc', '../escape.h',
])
def test_manifest_cannot_reintroduce_binary_cache_or_traversal(tmp_path, relative):
  foundation, port, output, manifest = inputs(tmp_path)
  manifest.write_text(f'{"0" * 64}  {relative}\n')
  with pytest.raises(ValueError, match='permitted source path'):
    stage_sources(foundation, port, output, manifest)
  assert not output.exists()


def test_binary_overlay_with_source_extension_is_rejected(tmp_path):
  foundation, port, output, manifest = inputs(tmp_path)
  (port / 'common').mkdir()
  (port / 'common/example.h').write_bytes(b'\x7fELF\0binary')
  with pytest.raises(ValueError, match='Binary content'):
    stage_sources(foundation, port, output, manifest)
  assert not output.exists()


def test_current_source_manifest_contains_only_permitted_paths():
  for line in Path(__file__).with_name('source_inputs.sha256').read_text().splitlines():
    source_path(line.split(None, 1)[1].strip())
