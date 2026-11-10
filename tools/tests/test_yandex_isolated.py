"""Real local child processes; no network, real credentials, or device access."""
import base64
import importlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import ModuleType

import pytest

PACKAGE = '_isolated_native_test'
package = ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[2] / 'sunnypilot/mapd/yandex')]
sys.modules[PACKAGE] = package
isolated = importlib.import_module(PACKAGE + '.isolated')
worker = Path(package.__path__[0]) / 'native_worker.py'


def child(tmp_path, source):
  script = tmp_path / 'child.py'
  script.write_text(source, encoding='utf-8')
  return [sys.executable, str(script)]


def test_valid_protocol_and_child_reaped(tmp_path, monkeypatch):
  processes = []
  original = subprocess.Popen
  def launch(*args, **kwargs):
    result = original(*args, **kwargs)
    processes.append(result)
    return result
  monkeypatch.setattr(isolated.subprocess, 'Popen', launch)
  command = child(tmp_path, 'import json; print(json.dumps({"ok": True, "online": True}))')
  assert isolated.run_isolated('probe', _command=command) == {'ok': True, 'online': True}
  assert len(processes) == 1 and processes[0].poll() == 0


def test_real_native_probe_dns_block_is_killed(tmp_path, monkeypatch):
  entered = tmp_path / 'dns-entered'
  command = child(tmp_path, f'''
import socket,sys,time
from pathlib import Path
sys.path.insert(0, {str(worker.parent.parent)!r})
def blocked(*args, **kwargs):
  Path({str(entered)!r}).touch()
  time.sleep(3600)
socket.getaddrinfo = blocked
sys.argv = ['native_worker.py', '--mode', 'probe']
from yandex.native_worker import main
main()
''')
  processes = []
  original = subprocess.Popen
  def launch(*args, **kwargs):
    result = original(*args, **kwargs)
    processes.append(result)
    return result
  monkeypatch.setattr(isolated.subprocess, 'Popen', launch)
  started = time.monotonic()
  with pytest.raises(isolated.NativeTransportError) as caught:
    isolated.run_isolated('probe', timeout_s=5, _command=command)
  assert caught.value.category == 'worker_timeout'
  assert time.monotonic() - started < 7
  assert processes[0].poll() is not None
  assert entered.exists()


def test_blocked_native_fetch_dns_is_killed_without_secret_output(tmp_path):
  entered = tmp_path / 'fetch-dns-entered'
  credential = tmp_path / 'client.json'
  credential.write_text(json.dumps({'schema_version': 1, 'sdk_build': '4.45.0-full',
                                   'api_key': '00000000-0000-0000-0000-000000000000',
                                   'signing_key_b64': base64.b64encode(b'0123456789abcdef').decode()}), encoding='utf-8')
  credential.chmod(0o600)
  command = child(tmp_path, f'''
import socket,sys,time
from pathlib import Path
sys.path.insert(0, {str(worker.parent.parent)!r})
def blocked(*args, **kwargs):
  Path({str(entered)!r}).touch()
  time.sleep(3600)
socket.getaddrinfo = blocked
sys.argv = ['native_worker.py', '--mode', 'fetch', '--credentials', {str(credential)!r}]
from yandex.native_worker import main
main()
''')
  with pytest.raises(isolated.NativeTransportError, match='^worker_timeout$'):
    isolated.run_isolated('fetch', credentials_path=credential,
                          request={'latitude': 55, 'longitude': 37, 'heading': 0}, timeout_s=5, _command=command)
  assert entered.exists()


def test_cancellation_kills_and_reaps_child(tmp_path, monkeypatch):
  allowed = threading.Event()
  allowed.set()
  processes = []
  original = subprocess.Popen
  def launch(*args, **kwargs):
    result = original(*args, **kwargs)
    processes.append(result)
    return result
  monkeypatch.setattr(isolated.subprocess, 'Popen', launch)
  command = child(tmp_path, 'import time; time.sleep(3600)')
  timer = threading.Timer(.15, allowed.clear)
  timer.start()
  try:
    with pytest.raises(isolated.AcquisitionCancelled):
      isolated.run_isolated('probe', _command=command, should_continue=allowed.is_set)
  finally:
    timer.join()
  assert processes[0].poll() is not None


@pytest.mark.parametrize('output', ['not json', '[]', '{"ok":1}', '{"ok":false,"category":"secret-value","status":999999}'])
def test_malformed_and_untrusted_child_failure_is_sanitized(tmp_path, output):
  command = child(tmp_path, f'print({output!r})')
  with pytest.raises(isolated.NativeTransportError) as caught:
    isolated.run_isolated('probe', _command=command)
  assert str(caught.value) in ('invalid_response', 'worker_failed')
  assert 'secret-value' not in str(caught.value)


def test_oversized_pipe_is_bounded_and_child_reaped(tmp_path, monkeypatch):
  monkeypatch.setattr(isolated, 'MAX_WORKER_BYTES', 4096)
  command = child(tmp_path, 'import sys,time; sys.stdout.write("x"*65536); sys.stdout.flush(); time.sleep(3600)')
  with pytest.raises(isolated.NativeTransportError, match='^response_over_budget$'):
    isolated.run_isolated('probe', _command=command)


def test_actual_entrypoint_reads_missing_credentials_without_network(tmp_path):
  with pytest.raises(isolated.NativeTransportError, match='^credentials_unavailable$'):
    isolated.run_isolated('fetch', credentials_path=tmp_path / 'absent.json',
                          request={'latitude': 55, 'longitude': 37, 'heading': 0})


def test_early_cancel_starts_no_process(monkeypatch):
  monkeypatch.setattr(isolated.subprocess, 'Popen', lambda *_a, **_k: pytest.fail('must not spawn'))
  with pytest.raises(isolated.AcquisitionCancelled):
    isolated.run_isolated('probe', should_continue=lambda: False)


@pytest.mark.parametrize('timeout', [0, -1, 61, float('inf'), True])
def test_invalid_timeout_rejected(timeout):
  with pytest.raises(isolated.NativeTransportError, match='^invalid_request$'):
    isolated.run_isolated('fetch', credentials_path='/unused', timeout_s=timeout)


def test_isolated_provider_preserves_partial_network_flag(monkeypatch):
  monkeypatch.setattr(isolated, 'run_isolated', lambda *_a, **_k: {
    'ok': True, 'events': [], 'complete': False, 'errors': ['reverse:NativeTransportError'], 'network_failure': True})
  result = isolated.IsolatedYandexProvider('/unused').fetch_window(55, 37, 0)
  assert result.network_failure and not result.complete


def test_repeated_outage_recovery_through_real_child_protocol(tmp_path, monkeypatch):
  """Block real child DNS, then recover via the real process/probe adapters."""
  import test_yandex_updater as fixture

  module = fixture.module.__wrapped__(monkeypatch)
  connectivity = importlib.import_module(PACKAGE + '.connectivity')
  credential = tmp_path / 'client.json'
  credential.write_text(json.dumps({'schema_version': 1, 'sdk_build': '4.45.0-full',
                                   'api_key': '00000000-0000-0000-0000-000000000000',
                                   'signing_key_b64': base64.b64encode(b'0123456789abcdef').decode()}), encoding='utf-8')
  credential.chmod(0o600)
  state = tmp_path / 'network-state'
  state.write_text('offline', encoding='ascii')
  command = child(tmp_path, f'''
import socket,sys,time
from pathlib import Path
sys.path.insert(0, {str(worker.parent.parent)!r})
mode, credential = sys.argv[1:]
state = Path({str(state)!r}).read_text()
from yandex.native_provider import NativeYandexProvider, NativeWindowResult
from yandex.native_transport import NativeTransportError
import yandex.connectivity as connectivity
if mode == 'probe':
  connectivity.probe_yandex_endpoint = lambda: state != 'offline'
elif state == 'offline':
  def blocked(*args, **kwargs):
    time.sleep(3600)
  socket.getaddrinfo = blocked
else:
  def fetch(self, latitude, longitude, heading):
    if state == 'bad-key':
      raise NativeTransportError('http_error', 403)
    event = dict(source_id='yandex:native:fixture', observed_at=1000, expires_at=1180,
                 query_latitude=latitude, query_heading=heading)
    return NativeWindowResult((event,), True)
  NativeYandexProvider.fetch_window = fetch
sys.argv = ['native_worker.py', '--mode', mode]
if mode == 'fetch':
  sys.argv += ['--credentials', credential]
from yandex.native_worker import main
main()
''')
  original_run, original_popen = isolated.run_isolated, subprocess.Popen
  processes, diagnostics = [], []
  def launch(*args, **kwargs):
    result = original_popen(*args, **kwargs)
    processes.append(result)
    return result
  def run(mode, **options):
    options['timeout_s'] = 5
    try:
      result = original_run(mode, _command=[*command, mode, str(credential)], **options)
      diagnostics.append((mode, result.get('online', result.get('complete'))))
      return result
    except isolated.NativeTransportError as error:
      diagnostics.append((mode, error.category))
      raise
  monkeypatch.setattr(isolated.subprocess, 'Popen', launch)
  monkeypatch.setattr(isolated, 'run_isolated', run)
  mono = [10.]
  params = fixture.Params()
  updater = module.YandexCameraUpdater(params, tmp_path, backend=isolated.IsolatedYandexProvider(credential),
                                       connectivity_probe=connectivity.ConnectivityProbe(),
                                       clock=lambda: 1000, monotonic=lambda: mono[0])
  def finish_fetch():
    updater._worker.join(7)
    assert not updater._worker.is_alive(), diagnostics
  for cycle in range(3):
    state.write_text('offline', encoding='ascii')
    if cycle:
      mono[0] += 60
    assert fixture.tick(updater, lat=55 + cycle * .1)
    finish_fetch()
    assert updater._network_failure and params.values['SpeedCameraYandexError'] == 'worker_timeout'
    fixture.tick(updater)
    probe = updater._probe_worker
    assert probe is not None
    probe.join(6)
    assert not probe.is_alive() and updater._network_failure
    state.write_text('online', encoding='ascii')
    mono[0] += 10
    assert not fixture.tick(updater, lat=55.001 + cycle * .1, heading=20)
    probe = updater._probe_worker
    assert probe is not None
    probe.join(6)
    assert not probe.is_alive() and not updater._network_failure, diagnostics
    assert fixture.tick(updater, lat=55.001 + cycle * .1, heading=20)
    finish_fetch()
    assert params.values['SpeedCameraYandexStatus'].startswith('ready'), (params.values, diagnostics)
    assert updater._events[0]['query_latitude'] == 55.001 + cycle * .1
    assert updater._events[0]['query_heading'] == 20
  state.write_text('bad-key', encoding='ascii')
  mono[0] += 60
  assert fixture.tick(updater)
  finish_fetch()
  assert not updater._network_failure and not fixture.tick(updater)
  assert updater._probe_worker is None
  assert all(process.poll() is not None for process in processes)
