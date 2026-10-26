"""Loopback-only transport regression tests; no external HTTP or hardware.

python -B -m pytest -c tools/tests/camera_replay_pytest.ini \
  -p no:cacheprovider tools/tests/test_camera_transport.py -q
"""

import hashlib
import socket
import ssl
import sys
import threading
import time
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from urllib.error import HTTPError

import pytest


ROOT = Path(__file__).resolve().parents[2]
for name, relative in (
  ('openpilot', '.'), ('openpilot.sunnypilot', 'sunnypilot'), ('openpilot.sunnypilot.mapd', 'sunnypilot/mapd'),
):
  if name not in sys.modules:
    namespace = ModuleType(name)
    namespace.__path__ = [str(ROOT / relative)]
    sys.modules[name] = namespace

from openpilot.sunnypilot.mapd import camera_database as cache
from openpilot.sunnypilot.mapd.cityguide import parse_cityguide_snapshot


BODY = b'2|Radars|1251\r\n18059|RU-4|51.6482993|39.0990694|1709|60|1713|0|1705|277|1706|150|\r\n'


@pytest.fixture
def good_cache(tmp_path):
  path = tmp_path / 'cameras.json'
  old = cache.CameraDatabase('known-good', 1_700_000_000, tuple(parse_cityguide_snapshot(BODY)))
  cache.save_database(str(path), old)
  return path, path.read_bytes(), old


def unchanged(previous):
  path, raw, old = previous
  assert path.read_bytes() == raw
  assert cache.load_database(str(path)) == old
  assert not list(path.parent.glob('.speed_cameras_*.tmp'))


@pytest.fixture
def loopback():
  routes = {}
  stopping = threading.Event()

  class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def do_GET(self):
      try:
        routes[self.path](self, stopping)
      except (OSError, HTTPException):
        pass  # The client intentionally aborts rejected/trickling responses.
      self.close_connection = True

    def log_message(self, *args):
      pass

  server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
  server.daemon_threads = False  # Teardown joins actual request workers.
  thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01))
  thread.start()

  def route(serve):
    path = f'/snapshot-{len(routes)}'
    routes[path] = serve
    return f'http://127.0.0.1:{server.server_port}{path}'

  try:
    yield route
  finally:
    stopping.set()
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    assert not thread.is_alive()


def response(body=BODY, status=200, extra=(), framing='length', length=None, terminal_chunk=True):
  def serve(handler, stopping):
    handler.send_response(status)
    handler.send_header('Content-Type', 'text/plain; charset=windows-1251')
    handler.send_header('Connection', 'close')
    if framing == 'length':
      handler.send_header('Content-Length', str(len(body) if length is None else length))
    elif framing == 'chunked':
      handler.send_header('Transfer-Encoding', 'chunked')
    for key, value in extra:
      handler.send_header(key, value)
    handler.end_headers()
    if framing == 'chunked':
      handler.wfile.write(f'{len(body):x}\r\n'.encode() + body + b'\r\n')
      if terminal_chunk:
        handler.wfile.write(b'0\r\n\r\n')
    else:
      handler.wfile.write(body)
  return serve


@pytest.mark.parametrize('status,headers', [
  (206, (('Content-Range', 'bytes 0-85/5305735'),)),
  (206, ()), (201, ()), (204, ()),
  (200, (('Content-Range', 'bytes 0-85/5305735'),)),
  (200, (('content-range', ''),)),
])
def test_only_complete_200_without_content_range_can_replace_cache(loopback, good_cache, status, headers):
  url = loopback(response(status=status, extra=headers))
  with pytest.raises(ValueError, match='HTTP 200|Content-Range'):
    cache.download_database(url, str(good_cache[0]), 'bad-range', timeout_s=1)
  unchanged(good_cache)


@pytest.mark.parametrize('framing', ['length', 'chunked', 'close'])
def test_complete_200_body_framings_preserve_metadata_and_hash(loopback, good_cache, framing):
  url = loopback(response(framing=framing))
  result = cache.download_database(url, str(good_cache[0]), 'complete', timeout_s=1)
  assert result.sha256 == hashlib.sha256(BODY).hexdigest()
  assert result.cameras == tuple(parse_cityguide_snapshot(BODY))
  assert result == cache.load_database(str(good_cache[0]))
  assert result.cameras[0].allowed_auto_control


@pytest.mark.parametrize('framing', ['length', 'chunked', 'close'])
def test_multi_buffer_body_is_fully_read_before_atomic_replacement(loopback, good_cache, framing):
  body = b'2|Radars|1251\r\n' + (BODY.splitlines()[1] + b'\r\n') * 2000
  assert len(body) > 64 * 1024
  url = loopback(response(body=body, framing=framing))
  result = cache.download_database(url, str(good_cache[0]), 'multi-buffer', timeout_s=2)
  assert len(result.cameras) == 2000
  assert result.sha256 == hashlib.sha256(body).hexdigest()
  assert cache.load_database(str(good_cache[0])) == result


@pytest.mark.parametrize('serve', [
  response(length=len(BODY) + 100),
  response(framing='chunked', terminal_chunk=False),
  response(body=BODY[:-1]),
  response(body=BODY + b'18059|cut|55|37|1706|'),
  response(status=404), response(status=500),
])
def test_partial_body_or_http_failure_keeps_good_cache(loopback, good_cache, serve):
  url = loopback(serve)
  with pytest.raises((ValueError, OSError, HTTPException, HTTPError)):
    cache.download_database(url, str(good_cache[0]), 'incomplete', timeout_s=1)
  unchanged(good_cache)


def trickle(phase):
  def serve(handler, stopping):
    prefix = b'HTTP/1.1 200 OK\r\nX-Trickle: '
    suffix = b'\r\nContent-Type: text/plain\r\nContent-Length: ' + str(len(BODY)).encode() + b'\r\n\r\n'
    if phase == 'status':
      wire = prefix + suffix + BODY
    elif phase == 'headers':
      handler.connection.sendall(prefix)
      wire = b'x' * 40 + suffix + BODY
    else:
      handler.connection.sendall(prefix + suffix)
      wire = BODY
    for byte in wire:
      if stopping.wait(0.03):
        return
      handler.connection.sendall(bytes([byte]))
  return serve


@pytest.mark.parametrize('phase', ['status', 'headers', 'body'])
def test_total_deadline_stops_trickles_even_when_every_byte_arrives_before_socket_timeout(
    loopback, good_cache, phase):
  url = loopback(trickle(phase))
  started = time.monotonic()
  with pytest.raises(TimeoutError, match='timeout.*body_bytes=.*wire_bytes='):
    cache.download_database(url, str(good_cache[0]), 'trickling', timeout_s=0.1)
  elapsed = time.monotonic() - started
  assert elapsed < 0.4, f'{phase}: timeout_s=.1 returned after {elapsed:.3f}s'
  unchanged(good_cache)


def test_headers_and_body_share_one_deadline(loopback, good_cache):
  def serve(handler, stopping):
    handler.connection.sendall(b'HTTP/1.1 200 OK\r\nX-Delay: ')
    for _ in range(4):
      if stopping.wait(0.025):
        return
      handler.connection.sendall(b'x')
    handler.connection.sendall(b'\r\nContent-Length: ' + str(len(BODY)).encode() + b'\r\n\r\n')
    for byte in BODY:
      if stopping.wait(0.025):
        return
      handler.connection.sendall(bytes([byte]))
  url = loopback(serve)
  started = time.monotonic()
  with pytest.raises(TimeoutError):
    cache.download_database(url, str(good_cache[0]), 'shared-budget', timeout_s=0.16)
  assert time.monotonic() - started < 0.4
  unchanged(good_cache)


def test_redirects_share_deadline_instead_of_restarting_it(loopback, good_cache):
  target = loopback(trickle('headers'))

  def redirect(handler, stopping):
    if stopping.wait(0.08):
      return
    handler.send_response(302)
    handler.send_header('Location', target)
    handler.send_header('Content-Length', '0')
    handler.end_headers()

  url = loopback(redirect)
  started = time.monotonic()
  with pytest.raises(TimeoutError):
    cache.download_database(url, str(good_cache[0]), 'redirect-budget', timeout_s=0.16)
  assert time.monotonic() - started < 0.4
  unchanged(good_cache)


def test_repeated_timeouts_create_no_downloader_workers(loopback, good_cache):
  # The server alone needs threads. No executor/future/Timer is used by fetch.
  original_threads = {thread.ident for thread in threading.enumerate()}
  url = loopback(trickle('headers'))
  for _ in range(4):
    with pytest.raises(TimeoutError):
      cache.download_database(url, str(good_cache[0]), 'retry', timeout_s=0.06)
  # Allow the server handlers to observe the closed sockets, then assert that
  # only server request threads can have appeared during these requests.
  added = [thread for thread in threading.enumerate() if thread.ident not in original_threads]
  assert all('process_request_thread' in thread.name for thread in added)
  unchanged(good_cache)


def test_redirect_cannot_escape_deadline_aware_http_transport(loopback, good_cache):
  url = loopback(response(body=b'', status=302, extra=(('Location', 'ftp://127.0.0.1/snapshot'),)))
  with pytest.raises(ValueError, match='HTTP or HTTPS'):
    cache.download_database(url, str(good_cache[0]), 'ftp-redirect', timeout_s=1)
  unchanged(good_cache)


@pytest.mark.parametrize('framing', ['length', 'chunked', 'close'])
def test_oversize_body_is_rejected_before_cache_replacement(loopback, good_cache, framing):
  url = loopback(response(framing=framing))
  with pytest.raises(ValueError, match='size limit'):
    cache.download_database(url, str(good_cache[0]), 'oversize', timeout_s=1, max_bytes=len(BODY) - 1)
  unchanged(good_cache)


def test_tls_handshake_stall_uses_remaining_deadline_without_a_worker(good_cache, monkeypatch):
  # A loopback TCP peer accepts ClientHello but never supplies a TLS response.
  # Load the platform trust store outside the measurement: this test isolates
  # network handshake cancellation, not OS certificate-store loading latency.
  context = ssl.create_default_context()
  handler_type = cache._DeadlineHTTPSHandler
  monkeypatch.setattr(cache, '_DeadlineHTTPSHandler', lambda budget: handler_type(budget, context=context))
  stopping = threading.Event()
  accepted = threading.Event()
  listener = socket.socket()
  listener.bind(('127.0.0.1', 0))
  listener.listen(1)
  listener.settimeout(1)

  def serve():
    try:
      with listener.accept()[0] as connection:
        connection.settimeout(1)
        connection.recv(8192)
        accepted.set()
        stopping.wait(1)
    except OSError:
      pass

  thread = threading.Thread(target=serve)
  thread.start()
  started = time.monotonic()
  try:
    with pytest.raises(TimeoutError):
      cache.download_database(f'https://127.0.0.1:{listener.getsockname()[1]}/',
                              str(good_cache[0]), 'tls-stall', timeout_s=0.1)
    assert time.monotonic() - started < 0.4
    assert accepted.is_set(), 'TLS test must reach ClientHello rather than time out during setup'
    unchanged(good_cache)
  finally:
    stopping.set()
    thread.join(timeout=2)
    listener.close()
    assert not thread.is_alive()
