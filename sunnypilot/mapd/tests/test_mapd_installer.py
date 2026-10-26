import hashlib
import os
import re
from pathlib import Path

import pytest
import requests

from openpilot.sunnypilot.mapd import mapd_installer as installer
from openpilot.sunnypilot.mapd import update_version as version_update


class FakeParams:
  def __init__(self, values=None):
    self.values = dict(values or {})

  def get(self, key):
    return self.values.get(key)

  def put(self, key, value):
    self.values[key] = value

  def remove(self, key):
    self.values.pop(key, None)


class FakeSpinner:
  def update(self, _message):
    pass


class FakeResponse:
  def __init__(self, body):
    self.body = body

  def __enter__(self):
    return self

  def __exit__(self, *_args):
    return False

  def raise_for_status(self):
    pass

  def iter_content(self, chunk_size):
    for offset in range(0, len(self.body), chunk_size):
      yield self.body[offset:offset + chunk_size]


def make_manager(monkeypatch, tmp_path, params=None):
  binary = tmp_path / "mapd"
  monkeypatch.setattr(installer, "MAPD_PATH", str(binary))
  manager = object.__new__(installer.MapdInstallManager)
  manager._params = params or FakeParams()
  manager._spinner = FakeSpinner()
  monkeypatch.setattr(manager, "ensure_directories_exist", lambda: None)
  return manager, binary


def test_official_release_metadata_is_pinned_and_version_parser_compatible():
  assert installer.VERSION == "v1.12.0"
  assert installer.URL == "https://github.com/pfeiferj/mapd/releases/download/v1.12.0/mapd"
  assert installer.EXPECTED_SHA256 == "fdb3b49ee19956e6ce09fdc3373cbba557f1263b2180e9f344c1d4053852284b"

  source = Path(installer.__file__).read_text()
  match = re.search(r"^VERSION\s*=\s*['\"]([^'\"]+)['\"]", source, re.MULTILINE)
  assert match and match.group(1) == installer.VERSION


def test_version_update_workflow_updates_both_release_pins(tmp_path):
  path = tmp_path / "mapd_installer.py"
  path.write_text(
    'VERSION = "v1.0.0"\n'
    'URL = f"https://github.com/pfeiferj/mapd/releases/download/{VERSION}/mapd"\n'
    'EXPECTED_SHA256 = "' + "0" * 64 + '"\n'
  )
  old_mode = path.stat().st_mode & 0o777
  checksum = "a" * 64

  version_update.update_mapd_version("v1.12.0", str(path))
  version_update.update_expected_sha256(checksum, str(path))

  assert version_update.get_current_mapd_version(str(path)) == "v1.12.0"
  assert f'EXPECTED_SHA256 = "{checksum}"' in path.read_text()
  if os.name != "nt":
    assert path.stat().st_mode & 0o777 == old_mode


def test_manager_stamp_clears_stale_version_for_wrong_binary(monkeypatch, tmp_path):
  params = FakeParams({"MapdVersion": "v1.0.0"})
  _, binary = make_manager(monkeypatch, tmp_path, params)
  binary.write_bytes(b"wrong executable")

  assert installer.update_installed_version(installer.VERSION, params) is False
  assert "MapdVersion" not in params.values


def test_verified_prebuilt_binary_can_stamp_metadata(monkeypatch, tmp_path):
  params = FakeParams()
  _, binary = make_manager(monkeypatch, tmp_path, params)
  payload = b"verified official mapd fixture"
  binary.write_bytes(payload)
  monkeypatch.setattr(installer, "EXPECTED_SHA256", hashlib.sha256(payload).hexdigest())

  assert installer.update_installed_version(installer.VERSION, params) is True
  assert params.values["MapdVersion"] == "v1.12.0"


def test_download_needed_repairs_metadata_only_for_verified_binary(monkeypatch, tmp_path):
  params = FakeParams({"MapdVersion": "v1.0.0"})
  manager, binary = make_manager(monkeypatch, tmp_path, params)
  payload = b"verified official mapd fixture"
  binary.write_bytes(payload)
  monkeypatch.setattr(installer, "EXPECTED_SHA256", hashlib.sha256(payload).hexdigest())

  assert manager.download_needed() is False
  assert params.values["MapdVersion"] == installer.VERSION


def test_existing_verified_binary_with_old_metadata_does_not_redownload(monkeypatch, tmp_path):
  params = FakeParams({"MapdVersion": "v1.0.0"})
  manager, binary = make_manager(monkeypatch, tmp_path, params)
  payload = b"verified official mapd fixture"
  binary.write_bytes(payload)
  monkeypatch.setattr(installer, "EXPECTED_SHA256", hashlib.sha256(payload).hexdigest())

  def unexpected_download(*_args, **_kwargs):
    pytest.fail("verified existing mapd binary must not be downloaded again")

  monkeypatch.setattr(installer.requests, "get", unexpected_download)
  manager.check_and_download()

  assert binary.read_bytes() == payload
  assert params.values["MapdVersion"] == installer.VERSION


def test_wrong_release_hash_preserves_binary_and_does_not_stamp(monkeypatch, tmp_path):
  params = FakeParams({"MapdVersion": "v1.0.0"})
  manager, binary = make_manager(monkeypatch, tmp_path, params)
  binary.write_bytes(b"old unverified binary")
  expected = b"correct official release fixture"
  monkeypatch.setattr(installer, "EXPECTED_SHA256", hashlib.sha256(expected).hexdigest())
  monkeypatch.setattr(installer.requests, "get", lambda *_args, **_kwargs: FakeResponse(b"wrong bytes"))

  with pytest.raises(ValueError, match="SHA-256 mismatch"):
    manager.download()

  assert binary.read_bytes() == b"old unverified binary"
  assert "MapdVersion" not in params.values
  assert not Path(str(binary) + ".tmp").exists()


def test_failed_download_does_not_stamp_or_replace_existing_binary(monkeypatch, tmp_path):
  params = FakeParams({"MapdVersion": "v1.0.0"})
  manager, binary = make_manager(monkeypatch, tmp_path, params)
  binary.write_bytes(b"old unverified binary")
  monkeypatch.setattr(installer, "DOWNLOAD_ATTEMPTS", 3)
  monkeypatch.setattr(installer.time, "sleep", lambda _seconds: None)
  calls = []

  def fail_request(*_args, **_kwargs):
    calls.append(1)
    raise requests.ConnectionError("fixture network failure")

  monkeypatch.setattr(installer.requests, "get", fail_request)
  with pytest.raises(RuntimeError, match="Failed to install verified mapd binary"):
    manager.download()

  assert binary.read_bytes() == b"old unverified binary"
  assert len(calls) == 3
  assert "MapdVersion" not in params.values
  assert not Path(str(binary) + ".tmp").exists()


def test_truncated_download_retries_then_keeps_old_binary_unstamped(monkeypatch, tmp_path):
  class TruncatedResponse(FakeResponse):
    def iter_content(self, chunk_size):
      yield b"partial"
      raise requests.exceptions.ChunkedEncodingError("fixture truncated response")

  params = FakeParams({"MapdVersion": "v1.0.0"})
  manager, binary = make_manager(monkeypatch, tmp_path, params)
  binary.write_bytes(b"old unverified binary")
  monkeypatch.setattr(installer, "DOWNLOAD_ATTEMPTS", 2)
  monkeypatch.setattr(installer.time, "sleep", lambda _seconds: None)
  calls = []

  def truncated_request(*_args, **_kwargs):
    calls.append(1)
    return TruncatedResponse(b"")

  monkeypatch.setattr(installer.requests, "get", truncated_request)
  with pytest.raises(RuntimeError, match="Failed to install verified mapd binary"):
    manager.download()

  assert len(calls) == 2
  assert binary.read_bytes() == b"old unverified binary"
  assert "MapdVersion" not in params.values
  assert not Path(str(binary) + ".tmp").exists()


def test_verified_release_download_replaces_binary_then_stamps(monkeypatch, tmp_path):
  params = FakeParams()
  manager, binary = make_manager(monkeypatch, tmp_path, params)
  payload = b"verified official mapd fixture"
  monkeypatch.setattr(installer, "EXPECTED_SHA256", hashlib.sha256(payload).hexdigest())
  calls = []

  def get(url, **kwargs):
    calls.append((url, kwargs))
    return FakeResponse(payload)

  monkeypatch.setattr(installer.requests, "get", get)
  manager.download()

  assert binary.read_bytes() == payload
  if os.name != "nt":
    assert binary.stat().st_mode & 0o111
  assert params.values["MapdVersion"] == installer.VERSION
  assert calls == [(installer.URL, {
    "stream": True,
    "timeout": (installer.DOWNLOAD_CONNECT_TIMEOUT, installer.DOWNLOAD_READ_TIMEOUT),
  })]
