"""Linux transport and private-file checks, using public synthetic credentials."""
import base64
import importlib
import json
import os
from pathlib import Path
import sys
from types import ModuleType

import pytest
import requests

package = ModuleType('yandex_transport_test_package')
package.__path__ = [str(Path(__file__).resolve().parents[2] / 'sunnypilot/mapd/yandex')]
sys.modules[package.__name__] = package
transport = importlib.import_module(package.__name__ + '.native_transport')
PUBLIC_TEST_API_KEY = '00000000-0000-0000-0000-000000000000'
PUBLIC_TEST_SIGNING_KEY = b'0123456789abcdef'
PARAMS = {'rll': '1,2~3,4', 'results': '1', 'lang': 'ru_RU'}


class Response:
  def __init__(self, status=200, body=b'\x08\x01', content_type='application/x-protobuf'):
    self.status_code, self.body = status, body
    self.headers = {'Content-Type': content_type}
    self.closed = False

  def iter_content(self, size):
    for offset in range(0, len(self.body), size):
      yield self.body[offset:offset + size]

  def close(self):
    self.closed = True


class Session(requests.Session):
  def __init__(self, response=None, error=None):
    super().__init__()
    self.response, self.error = response or Response(), error
    self.calls = []

  def send(self, request, **options):
    self.calls.append((request, dict(request.headers), options))
    if self.error:
      raise self.error
    return self.response


def client(session=None, **kwargs):
  return transport.NativeHTTPTransport(PUBLIC_TEST_API_KEY, PUBLIC_TEST_SIGNING_KEY, session=session or Session(), **kwargs)


def credentials(tmp_path, **changes):
  path = tmp_path / 'private.json'
  value = {'schema_version': 1, 'sdk_build': '4.45.0-full', 'api_key': PUBLIC_TEST_API_KEY,
           'signing_key_b64': base64.b64encode(PUBLIC_TEST_SIGNING_KEY).decode('ascii')}
  value.update(changes)
  path.write_text(json.dumps(value))
  path.chmod(0o600)
  return path


def test_verified_https_no_redirects_and_headers_not_retained_in_response_request():
  session = Session()
  instance = client(session)
  assert instance('GET', 'route', PARAMS, b'') == b'\x08\x01'
  request, headers, options = session.calls[0]
  assert headers['X-YMapKit-Api-Key'] == PUBLIC_TEST_API_KEY
  assert len(headers['X-YRuntime-Signature']) == 40
  assert 'linux/' in headers['User-Agent'] and 'android/' not in headers['User-Agent']
  assert options == {'timeout': (6, 15), 'allow_redirects': False, 'stream': True, 'verify': True}
  assert session.trust_env is False and session.response.closed
  assert not request.headers


def test_post_signs_exact_binary_body_and_content_type():
  session = Session()
  client(session)('POST', 'conditions', {'segment': '0', 'lang': 'ru_RU'}, b'\x18\x01')
  request, headers, _ = session.calls[0]
  assert request.body == b'\x18\x01'
  assert headers['Content-Type'] == 'application/x-protobuf'


@pytest.mark.parametrize('status', [302, 400, 403, 429, 500])
def test_non_success_status_is_sanitized_and_closed(status):
  session = Session(Response(status=status, body=b'server text must not escape'))
  with pytest.raises(transport.NativeTransportError) as error:
    client(session)('GET', 'route', PARAMS, b'')
  assert str(error.value) == 'http_error' and error.value.status == status
  assert session.response.closed and not session.calls[0][0].headers


def test_timeout_does_not_export_exception_url_or_headers():
  session = Session(error=requests.ReadTimeout('credential-like diagnostic text'))
  with pytest.raises(transport.NativeTransportError, match='^timeout$'):
    client(session)('GET', 'route', PARAMS, b'')
  assert not session.calls[0][0].headers


def test_response_type_and_stream_budget_fail_closed(monkeypatch):
  with pytest.raises(transport.NativeTransportError, match='content_type'):
    client(Session(Response(content_type='text/html')))('GET', 'route', PARAMS, b'')
  monkeypatch.setattr(transport, 'MAX_RESPONSE_BYTES', 3)
  with pytest.raises(transport.NativeTransportError, match='over_budget'):
    client(Session(Response(body=b'1234')))('GET', 'route', PARAMS, b'')


@pytest.mark.parametrize('base', ['http://proxy.mob.maps.yandex.net/mapkit2/driving/2.x/',
                                  'https://example.invalid/mapkit2/driving/2.x/',
                                  'https://user@proxy.mob.maps.yandex.net/mapkit2/driving/2.x/',
                                  'https://proxy.mob.maps.yandex.net/mapkit2/driving/2.x/?secret=x'])
def test_credentials_cannot_be_sent_to_arbitrary_service(base):
  with pytest.raises(transport.NativeTransportError, match='service_configuration'):
    client(base_url=base)


def test_unverified_query_parameters_are_not_added():
  session = Session()
  with pytest.raises(transport.NativeTransportError, match='invalid_request'):
    client(session)('GET', 'route', dict(PARAMS, i18n_prefs='metric'), b'')
  assert not session.calls


def test_close_clears_owned_material_and_prevents_reuse():
  instance = client()
  instance.close()
  assert not instance._api_key and instance._signing_key == bytearray(16)
  with pytest.raises(transport.NativeTransportError, match='closed'):
    instance('GET', 'route', PARAMS, b'')


def test_owner_private_regular_credentials_file(tmp_path):
  assert transport.load_private_credentials(credentials(tmp_path)) == (PUBLIC_TEST_API_KEY, PUBLIC_TEST_SIGNING_KEY)


@pytest.mark.parametrize('change', [{'schema_version': True}, {'sdk_build': 'unverified'}, {'api_key': 'wrong'},
                                   {'signing_key_b64': 'not base64'}, {'extra': 'unexpected'}])
def test_invalid_private_configuration_is_sanitized(tmp_path, change):
  with pytest.raises(transport.NativeTransportError, match='^credentials_unavailable$'):
    transport.load_private_credentials(credentials(tmp_path, **change))


def test_public_or_symlink_credential_file_is_rejected(tmp_path):
  path = credentials(tmp_path)
  path.chmod(0o644)
  with pytest.raises(transport.NativeTransportError):
    transport.load_private_credentials(path)
  path.chmod(0o600)
  alias = tmp_path / 'alias.json'
  alias.symlink_to(path)
  with pytest.raises(transport.NativeTransportError):
    transport.load_private_credentials(alias)


def test_fifo_is_rejected_without_blocking(tmp_path):
  path = tmp_path / 'fifo'
  os.mkfifo(path, 0o600)
  with pytest.raises(transport.NativeTransportError):
    transport.load_private_credentials(path)


def test_slow_drip_fallback_exceeds_total_deadline_and_closes():
  elapsed = [0]

  class DripResponse(Response):
    def iter_content(self, size):
      assert size == 1
      for _ in range(10):
        elapsed[0] += 10  # Every arrival is below the 15s socket idle timeout.
        yield b'x'

  response = DripResponse()
  instance = client(Session(response), monotonic_clock=lambda: elapsed[0])
  with pytest.raises(transport.NativeTransportError, match='^timeout$'):
    instance('GET', 'route', PARAMS, b'')
  assert elapsed[0] == 50 and response.closed


def test_raw_read1_decodes_content_and_checks_deadline():
  elapsed, calls = [0], []

  class Raw:
    def read1(self, size, *, decode_content):
      calls.append((size, decode_content))
      elapsed[0] += 10
      return b'x'

  response = Response()
  response.raw = Raw()
  with pytest.raises(transport.NativeTransportError, match='^timeout$'):
    client(Session(response), monotonic_clock=lambda: elapsed[0])('GET', 'route', PARAMS, b'')
  assert len(calls) == 5 and all(item == (16384, True) for item in calls)
  assert response.closed


def test_raw_read1_urllib3_failure_is_sanitized():
  from urllib3.exceptions import ReadTimeoutError

  class Raw:
    def read1(self, *args, **kwargs):
      raise ReadTimeoutError(None, 'private-url', 'private-body')

  response = Response()
  response.raw = Raw()
  with pytest.raises(transport.NativeTransportError, match='^timeout$'):
    client(Session(response))('GET', 'route', PARAMS, b'')
  assert response.closed


@pytest.mark.parametrize('timeout', [None, (6, None), (6, 16), (7, 15), (True, 15), (0, 15)])
def test_socket_timeout_cannot_disable_deadline_backstop(timeout):
  with pytest.raises(transport.NativeTransportError, match='^invalid_timeout$'):
    client(timeout=timeout)
