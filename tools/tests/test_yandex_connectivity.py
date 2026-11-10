"""No real sockets/credentials: verify exact endpoint, verified TLS and isolation."""
import importlib
from pathlib import Path
import ssl
import sys
from types import ModuleType

import pytest

PACKAGE = '_yandex_connectivity_test'
package = ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[2] / 'sunnypilot/mapd/yandex')]
sys.modules[PACKAGE] = package
connectivity = importlib.import_module(PACKAGE + '.connectivity')


class Stream:
  def __init__(self):
    self.closes = 0

  def close(self):
    self.closes += 1


@pytest.fixture(autouse=True)
def forbid_real_network(monkeypatch):
  def forbidden(*args, **kwargs):
    pytest.fail('Connectivity unit tests must not open sockets')
  monkeypatch.setattr(connectivity.socket, 'create_connection', forbidden)


def test_probe_targets_yandex_with_default_verified_tls_and_closes_both(monkeypatch):
  tcp, tls, calls = Stream(), Stream(), []
  real_factory = ssl.create_default_context
  context = real_factory()
  assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname

  class Context:
    def wrap_socket(self, connection, *, server_hostname):
      assert connection is tcp and server_hostname == 'proxy.mob.maps.yandex.net'
      calls.append('tls')
      return tls

  def connect(address, *, timeout):
    assert address == ('proxy.mob.maps.yandex.net', 443) and timeout == 2
    calls.append('tcp')
    return tcp

  monkeypatch.setattr(connectivity.socket, 'create_connection', connect)
  monkeypatch.setattr(connectivity.ssl, 'create_default_context', lambda: Context())
  assert connectivity.probe_yandex_endpoint() is True
  assert calls == ['tcp', 'tls'] and tcp.closes == tls.closes == 1


@pytest.mark.parametrize('error', [OSError('SECRET_URL'), TimeoutError('SECRET_URL')])
def test_tcp_failure_is_offline_without_tls(monkeypatch, error):
  def connect(*args, **kwargs):
    raise error
  monkeypatch.setattr(connectivity.socket, 'create_connection', connect)
  monkeypatch.setattr(connectivity.ssl, 'create_default_context', lambda: pytest.fail('No TLS after TCP failure'))
  assert connectivity.probe_yandex_endpoint() is False


def test_certificate_rejection_closes_tcp(monkeypatch):
  tcp = Stream()
  class Context:
    def wrap_socket(self, *args, **kwargs):
      raise ssl.SSLCertVerificationError('SECRET_HOST')
  monkeypatch.setattr(connectivity.socket, 'create_connection', lambda *args, **kwargs: tcp)
  monkeypatch.setattr(connectivity.ssl, 'create_default_context', lambda: Context())
  assert connectivity.probe_yandex_endpoint() is False
  assert tcp.closes == 1


def test_proxy_uses_only_isolated_probe_mode(monkeypatch):
  calls = []
  isolated = ModuleType(PACKAGE + '.isolated')
  def run(mode, **kwargs):
    calls.append((mode, kwargs))
    return {'ok': True, 'online': True}
  isolated.run_isolated = run
  monkeypatch.setitem(sys.modules, isolated.__name__, isolated)
  def permitted():
    return True
  assert connectivity.ConnectivityProbe()(should_continue=permitted) is True
  assert calls == [('probe', {'timeout_s': 5, 'should_continue': permitted})]


@pytest.mark.parametrize('result', [None, {}, {'online': True}, {'ok': True, 'online': 'yes'},
                                  {'ok': True, 'online': 1}, {'ok': False, 'online': True}, {'ok': True, 'online': False}])
def test_malformed_or_offline_child_result_is_not_online(monkeypatch, result):
  monkeypatch.setattr(connectivity, '_run_probe', lambda _check: result)
  assert connectivity.ConnectivityProbe()() is False


@pytest.mark.parametrize('category', ['network_error', 'timeout', 'worker_timeout', 'worker_failed', 'invalid_response'])
def test_network_worker_failure_is_offline(monkeypatch, category):
  def run(_check):
    raise connectivity.NativeTransportError(category)
  monkeypatch.setattr(connectivity, '_run_probe', run)
  assert connectivity.ConnectivityProbe()() is False


def test_cancelled_probe_never_starts_or_publishes_late_success(monkeypatch):
  monkeypatch.setattr(connectivity, '_run_probe', lambda _check: pytest.fail('Cancelled probe started'))
  with pytest.raises(connectivity.AcquisitionCancelled):
    connectivity.ConnectivityProbe()(should_continue=lambda: False)
  checks = iter([True, False])
  monkeypatch.setattr(connectivity, '_run_probe', lambda _check: {'ok': True, 'online': True})
  with pytest.raises(connectivity.AcquisitionCancelled):
    connectivity.ConnectivityProbe()(should_continue=lambda: next(checks))


def test_nonnetwork_worker_error_and_cancellation_are_not_masked(monkeypatch):
  def run(_check):
    raise connectivity.NativeTransportError('worker_protocol')
  monkeypatch.setattr(connectivity, '_run_probe', run)
  with pytest.raises(connectivity.NativeTransportError, match='worker_protocol'):
    connectivity.ConnectivityProbe()()
  def cancelled(_check):
    raise connectivity.AcquisitionCancelled('cancelled')
  monkeypatch.setattr(connectivity, '_run_probe', cancelled)
  with pytest.raises(connectivity.AcquisitionCancelled):
    connectivity.ConnectivityProbe()()


def test_cancellation_wins_over_worker_timeout(monkeypatch):
  def run(_check):
    raise connectivity.NativeTransportError('worker_timeout')
  checks = iter([True, False])
  monkeypatch.setattr(connectivity, '_run_probe', run)
  with pytest.raises(connectivity.AcquisitionCancelled):
    connectivity.ConnectivityProbe()(should_continue=lambda: next(checks))
