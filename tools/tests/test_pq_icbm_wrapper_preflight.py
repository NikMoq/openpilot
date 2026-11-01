"""Execute the real wrapper main with hardware entry points replaced by stubs."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from test_pq_icbm_release import fixture_release, release

ROOT = Path(__file__).resolve().parents[2]


class StopAttempt(BaseException):
  """Leave the wrapper loop without falling into its retry handlers."""


def wrapper_main(namespace):
  source = ROOT / 'selfdrive/pandad/pandad.py'
  tree = ast.parse(source.read_text())
  main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
  exec(compile(ast.fix_missing_locations(ast.Module(body=[main], type_ignores=[])), str(source), 'exec'), namespace)
  return namespace['main']


def attempt(root, params, directory, observe_retry_delay=False):
  calls = []
  observations = []
  class LegacyParams:
    def get_param_path(self):
      return params.get_param_path()
    def remove(self, name):
      assert name == 'PandaSignatures'
    def put(self, *args):
      raise AssertionError('Unknown-key Params.put is forbidden')
  def hardware(name):
    def call(*args, **kwargs):
      calls.append(name)
      identity = directory / release.KEY
      observations.append(identity.read_text() if identity.exists() else None)
      raise StopAttempt()
    return call
  def failed(*args, **kwargs):
    calls.append('failure_logged')
    if not observe_retry_delay:
      raise StopAttempt()
  def sleep(seconds):
    calls.append(('sleep', seconds))
    raise StopAttempt()
  namespace = {
    'release_preflight': release.release_preflight, 'BASEDIR': root,
    'Params': LegacyParams, 'signal': NS(signal=lambda *args: None, SIGINT=2),
    'cloudlog': NS(info=lambda *args, **kwargs: None, event=lambda *args, **kwargs: None, exception=failed),
    'time': NS(monotonic=lambda: 100., sleep=sleep),
    'PandaDFU': NS(list=hardware('dfu_list')),
    'Panda': NS(list=hardware('panda_list')),
    'HARDWARE': NS(recover_internal_panda=hardware('recover'), reset_internal_panda=hardware('reset')),
    'flash_panda': hardware('flash'),
    'usb1': NS(USBErrorNoDevice=type('UsbGone', (Exception,), {}), USBErrorPipe=type('UsbPipe', (Exception,), {})),
    'PandaProtocolMismatch': type('ProtocolMismatch', (Exception,), {}),
  }
  with pytest.raises(StopAttempt):
    wrapper_main(namespace)()
  return calls, observations


@pytest.mark.parametrize('corrupt', [release.FIRMWARE, release.DAEMON, release.MANIFEST])
def test_corrupt_release_fails_wrapper_attempt_before_any_hardware(tmp_path, corrupt):
  params, directory = fixture_release(tmp_path)
  (directory / release.KEY).write_text('stale expected identity')
  with (tmp_path / corrupt).open('ab') as stream:
    stream.write(b'corrupt')
  calls, observations = attempt(tmp_path, params, directory)
  assert calls == ['failure_logged']
  assert observations == []
  assert not (directory / release.KEY).exists()


def test_absent_manifest_revokes_stale_identity_then_preserves_original_hardware_flow(tmp_path):
  params, directory = fixture_release(tmp_path)
  (tmp_path / release.MANIFEST).unlink()
  (directory / release.KEY).write_text('stale expected identity')
  calls, observations = attempt(tmp_path, params, directory)
  assert calls == ['dfu_list']
  assert observations == [None]
  assert not (directory / release.KEY).exists()


def test_valid_pair_identity_is_provisioned_before_first_hardware_call(tmp_path):
  params, directory = fixture_release(tmp_path)
  calls, observations = attempt(tmp_path, params, directory)
  assert calls == ['dfu_list']
  assert observations == [bytes(range(128)).hex()]
  assert (directory / release.KEY).read_text() == observations[0]


def test_each_wrapper_attempt_rechecks_changed_artifacts(tmp_path):
  params, directory = fixture_release(tmp_path)
  assert release.release_preflight(tmp_path, params) is True
  (tmp_path / release.DAEMON).write_bytes(b'changed after previous attempt')
  calls, observations = attempt(tmp_path, params, directory)
  assert calls == ['failure_logged']
  assert observations == []
  assert not (directory / release.KEY).exists()


def test_unreadable_manifest_metadata_revokes_identity_without_hardware(tmp_path, monkeypatch):
  params, directory = fixture_release(tmp_path)
  (directory / release.KEY).write_text('stale expected identity')
  real_lstat = Path.lstat
  def lstat(path, *args, **kwargs):
    if path == tmp_path / release.MANIFEST:
      raise PermissionError('fixture only')
    return real_lstat(path, *args, **kwargs)
  monkeypatch.setattr(Path, 'lstat', lstat)
  calls, observations = attempt(tmp_path, params, directory)
  assert calls == ['failure_logged']
  assert observations == []
  assert not (directory / release.KEY).exists()


@pytest.mark.parametrize('corrupt', [release.FIRMWARE, release.DAEMON, release.MANIFEST])
def test_returning_error_logger_still_delays_preflight_retry_before_hardware(tmp_path, corrupt):
  params, directory = fixture_release(tmp_path)
  (directory / release.KEY).write_text('stale expected identity')
  with (tmp_path / corrupt).open('ab') as stream:
    stream.write(b'corrupt')
  calls, observations = attempt(tmp_path, params, directory, observe_retry_delay=True)
  assert calls == ['failure_logged', ('sleep', 1)]
  assert observations == []
  assert not (directory / release.KEY).exists()


def test_broken_manifest_symlink_is_invalid_present_release_not_absent(tmp_path):
  params, directory = fixture_release(tmp_path)
  manifest = tmp_path / release.MANIFEST
  manifest.unlink()
  try:
    manifest.symlink_to(tmp_path / 'nonexistent-release-manifest')
  except OSError:
    pytest.skip('Platform does not permit fixture symlinks')
  (directory / release.KEY).write_text('stale expected identity')
  calls, observations = attempt(tmp_path, params, directory, observe_retry_delay=True)
  assert calls == ['failure_logged', ('sleep', 1)]
  assert observations == []
  assert not (directory / release.KEY).exists()
