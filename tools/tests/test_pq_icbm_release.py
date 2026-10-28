import importlib.util
import hashlib
import json
from pathlib import Path
import struct

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("release", ROOT / "selfdrive/pandad/pq_icbm_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def fixture_release(tmp_path):
  firmware = struct.pack("<I", 12) + b"VERS\x02\x00\x00\x00" + bytes(range(128))
  for path, data in ((release.FIRMWARE, firmware), (release.DAEMON, b"daemon")):
    p = tmp_path / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
  manifest = {"schema": 1, "files": {name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
              for name in (release.FIRMWARE, release.DAEMON)}, "panda_signature_hex": firmware[-128:].hex()}
  (tmp_path / "selfdrive/pandad/pq_icbm_release.json").write_text(json.dumps(manifest))
  directory = tmp_path / "params"
  directory.mkdir()
  class OldParams:
    def get_param_path(self):
      return str(directory)
    def put(self, *args):
      raise AssertionError("Old extension must not receive unknown keys")
  return OldParams(), directory


@pytest.mark.skipif(not (ROOT / "selfdrive/pandad/pq_icbm_release.json").exists(),
                    reason="Source candidate does not package a matched Panda/pandad release manifest")
def test_packaged_release():
  assert len(release.verified_signature(ROOT)) == 256


def test_old_params_atomic_replacement(tmp_path):
  params, directory = fixture_release(tmp_path)
  for _ in range(2):
    release.prepare_pairing(tmp_path, params)
    assert (directory / release.KEY).read_text() == bytes(range(128)).hex()
  assert sorted(p.name for p in directory.iterdir()) == [release.KEY]


@pytest.mark.parametrize("name", [release.FIRMWARE, release.DAEMON, "selfdrive/pandad/pq_icbm_release.json"])
def test_corruption_removes_stale_pairing(tmp_path, name):
  params, directory = fixture_release(tmp_path)
  release.prepare_pairing(tmp_path, params)
  with (tmp_path / name).open("ab") as stream:
    stream.write(b"corrupt")
  with pytest.raises(ValueError):
    release.prepare_pairing(tmp_path, params)
  assert not (directory / release.KEY).exists()
