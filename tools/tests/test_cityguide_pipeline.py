"""Offline CityGuide HTTP -> parser -> atomic cache -> lookup integration.

Run from the checkout with the lightweight namespace, without root conftest:
  python -B -m pytest -c tools/tests/camera_replay_pytest.ini --noconftest \
    -p no:cacheprovider tools/tests/test_cityguide_pipeline.py -q -s

Set CITYGUIDE_FULL_FIXTURE to the external user-supplied SpeedCam.bkm to also
exercise all 74,016 records. Never copy that payload into the repository.
All requests use a loopback stdlib server; downloader/parser/cache/lookup are
real production functions. No Cereal, CAN, controls, or external HTTP is used.
"""

import hashlib
import json
import math
import os
import sys
import threading
from collections import Counter
from dataclasses import dataclass
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from urllib.parse import urlsplit

import pytest


ROOT = Path(__file__).resolve().parents[2]
for name, relative in (
  ('openpilot', '.'),
  ('openpilot.sunnypilot', 'sunnypilot'),
  ('openpilot.sunnypilot.mapd', 'sunnypilot/mapd'),
):
  if name not in sys.modules:
    namespace = ModuleType(name)
    namespace.__path__ = [str(ROOT / relative)]
    sys.modules[name] = namespace

from openpilot.sunnypilot.mapd.camera_database import download_database, load_database
from openpilot.sunnypilot.mapd.camera_updater import DEFAULT_SPEED_CAMERA_URL
from openpilot.sunnypilot.mapd.speed_cameras import find_nearest_camera, parse_camera_snapshot


FULL_SIZE = 5_305_735
FULL_SHA256 = '0ccaba30044729081e36cc006817d81501ad52dbe9eaf2ddbf35aa5751482c6c'
FULL_RECORDS = 74_016
HEADER = '2|Radars|1251\n'
FIRST_ROW = '18059|RU-4|51.6482993|39.0990694|1709|60|1713|0|1705|277|1706|150|\n'
MINIMAL_BODY = (HEADER + FIRST_ROW).encode('ascii')
SMALL_TEXT = HEADER + ''.join((
  '18059|behind|54.9999|37|1709|80|\n',
  '18952|Камера|55.0002|37|102|Контроль|1709|0|1713|2|1705|180|1706|100|\n',
  '18059|ahead|55.001|37|1709|60|1713|0|1705|277|1706|150|\n',
  '|unknown-type|55.002|37|1709|60|\n',
  '5|not-igo-type-5|55.003|37|1709|90|\n',
))


@dataclass(frozen=True)
class Response:
  body: bytes
  content_type: str = 'text/plain; charset=windows-1251'
  status: int = 200
  framing: str = 'length'
  declared_length: int | None = None
  complete_chunks: bool = True


@pytest.fixture
def local_http():
  routes = {}
  requests = []

  class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def do_GET(self):
      requests.append(self.path)
      response = routes[self.path]
      self.send_response(response.status)
      self.send_header('Content-Type', response.content_type)
      self.send_header('Connection', 'close')
      if response.framing == 'length':
        self.send_header('Content-Length', str(
          len(response.body) if response.declared_length is None else response.declared_length))
      elif response.framing == 'chunked':
        self.send_header('Transfer-Encoding', 'chunked')
      self.end_headers()
      self.close_connection = True
      try:
        if response.framing == 'chunked':
          for index in range(0, len(response.body), 97):
            chunk = response.body[index:index + 97]
            self.wfile.write(f'{len(chunk):X}\r\n'.encode('ascii') + chunk + b'\r\n')
          if response.complete_chunks:
            self.wfile.write(b'0\r\n\r\n')
        else:
          self.wfile.write(response.body)
      except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        pass  # An intentionally rejected response can be closed before EOF.

    def log_message(self, *_args):
      pass

  server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
  thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
  thread.start()

  def serve(response):
    path = f'/snapshot-{len(routes)}.bkm'
    routes[path] = response
    return f'http://127.0.0.1:{server.server_port}{path}'

  try:
    yield serve, requests
  finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    assert not thread.is_alive()


def fetch(local_http, path, response, version='cg77-offline-test', **kwargs):
  serve, requests = local_http
  url = serve(response)
  result = download_database(url, str(path), version, timeout_s=5, **kwargs)
  assert requests[-1] == urlsplit(url).path
  assert result.provider == urlsplit(url).netloc
  assert result.sha256 == hashlib.sha256(response.body).hexdigest()
  return result


def assert_cache_unchanged(path, original_bytes, original_database):
  assert path.read_bytes() == original_bytes
  assert load_database(str(path)) == original_database
  assert not list(path.parent.glob('.speed_cameras_*.tmp'))


def position_behind(latitude, longitude, travel_bearing, distance_m):
  angle = distance_m / 6_371_000
  bearing = math.radians(travel_bearing + 180)
  latitude, longitude = math.radians(latitude), math.radians(longitude)
  origin_lat = math.asin(math.sin(latitude) * math.cos(angle) + math.cos(latitude) * math.sin(angle) * math.cos(bearing))
  origin_lon = longitude + math.atan2(math.sin(bearing) * math.sin(angle) * math.cos(latitude),
                                     math.cos(angle) - math.sin(latitude) * math.sin(origin_lat))
  return math.degrees(origin_lat), math.degrees(origin_lon)


def test_default_source_is_same_speedcamonline_cityguide_export():
  assert DEFAULT_SPEED_CAMERA_URL == 'https://speedcamonline.ru/cg77/Rus/comment/'


@pytest.mark.parametrize('encoding,bom,header', [
  ('cp1251', False, '1251'),
  ('utf-8', True, '65001'),
])
@pytest.mark.parametrize('framing', ['length', 'close', 'chunked'])
def test_real_http_round_trip_and_nearest_warning_preserve_cityguide_semantics(
    tmp_path, local_http, encoding, bom, header, framing):
  text = SMALL_TEXT.replace('Radars|1251', f'Radars|{header}').replace('\n', '\r\n')
  body = (b'\xef\xbb\xbf' if bom else b'') + text.encode(encoding)
  parsed = parse_camera_snapshot(body)
  path = tmp_path / 'cache.json'
  downloaded = fetch(local_http, path, Response(body, framing=framing))
  loaded = load_database(str(path))
  assert loaded == downloaded
  assert loaded.cameras == tuple(parsed)
  assert len(loaded.cameras) == 5
  assert [camera.camera_type for camera in loaded.cameras] == [
    'speedcamonline-cg77:18059', 'speedcamonline-cg77:18952',
    'speedcamonline-cg77:18059', 'speedcamonline-cg77:unknown', 'speedcamonline-cg77:5',
  ]
  assert [camera.allowed_auto_control for camera in loaded.cameras] == [False, False, True, False, False]
  assert [camera.direction_deg for camera in loaded.cameras] == [None, None, 97, None, None]
  assert [camera.direction_type for camera in loaded.cameras] == [None, None, 1, None, None]
  assert [camera.source_id for camera in loaded.cameras] == ['behind', 'Камера', 'ahead', 'unknown-type', 'not-igo-type-5']
  assert loaded.cameras[1].source_params['102'] == 'Контроль'
  assert loaded.cameras[2].source_params == {'1709': '60', '1713': '0', '1705': '277', '1706': '150'}
  assert all(camera.second_direction_deg is None for camera in loaded.cameras)
  assert all(camera.end_latitude is None and camera.end_longitude is None for camera in loaded.cameras)

  match = find_nearest_camera(loaded.cameras, 55, 37, heading_deg=0, max_distance_m=200)
  assert match is not None and match.camera == loaded.cameras[1]
  assert match.distance_m == pytest.approx(22.239, abs=0.01)
  assert match.camera.speed_limit_kph is None  # Unknown speed stays a warning.
  match = find_nearest_camera(loaded.cameras, 55, 37, heading_deg=180, max_distance_m=200)
  assert match is not None and match.camera == loaded.cameras[0]
  assert match.distance_m == pytest.approx(11.1195, abs=0.01)
  assert find_nearest_camera(loaded.cameras, 55, 37, heading_deg=0, max_distance_m=10) is None


@pytest.mark.parametrize('kind', [18059, 18950, 18958])
def test_normalized_point_and_second_direction_survive_http_cache(tmp_path, local_http, kind):
  body = (HEADER + f'{kind}|point|55.001|37|1709|60|1713|2|1705|180|1707|270|\n').encode()
  path = tmp_path / 'cache.json'
  fetch(local_http, path, Response(body))
  camera, = load_database(str(path)).cameras
  assert camera.allowed_auto_control
  assert (camera.direction_deg, camera.second_direction_deg, camera.direction_type) == (0, 90, 1)
  assert camera.source_params['1705'] == '180' and camera.source_params['1707'] == '270'
  assert find_nearest_camera([camera], 55, 37, heading_deg=0).camera == camera
  assert find_nearest_camera([camera], 55, 37, heading_deg=180) is None
  assert find_nearest_camera([camera], 55.001, 36.999, heading_deg=90).camera == camera
  assert find_nearest_camera([camera], 55.001, 36.999, heading_deg=270) is None


REJECTED_RESPONSES = [
  pytest.param(Response(b''), id='empty-200'),
  pytest.param(Response(b' \r\n\t'), id='whitespace-200'),
  pytest.param(Response(HEADER.encode()), id='header-only-200'),
  pytest.param(Response(b'<html>upstream error</html>\n'), id='html-disguised-as-text'),
  pytest.param(Response(MINIMAL_BODY, content_type='text/html'), id='html-content-type'),
  pytest.param(Response(MINIMAL_BODY[:-1]), id='missing-final-newline'),
  pytest.param(Response(MINIMAL_BODY + b'18059|cut|55|37|1706|'), id='mid-row-truncation'),
  pytest.param(Response(MINIMAL_BODY + b'18059|cut|55|37|1706|\n'), id='incomplete-pair-with-newline'),
  pytest.param(Response(MINIMAL_BODY + b'18059|bad|nan|37|1709|60|\n'), id='corrupt-row-after-good-prefix'),
  pytest.param(Response(b'2|Radars|1251\n18059|\x98|55|37|\n'), id='corrupt-cp1251'),
  pytest.param(Response(MINIMAL_BODY, declared_length=len(MINIMAL_BODY) + 100), id='short-http-complete-row-boundary'),
  pytest.param(Response(MINIMAL_BODY, framing='chunked', complete_chunks=False), id='interrupted-chunked-body'),
  pytest.param(Response(MINIMAL_BODY, status=404), id='http-404'),
  pytest.param(Response(MINIMAL_BODY, status=500), id='http-500'),
]


@pytest.mark.parametrize('response', REJECTED_RESPONSES)
def test_bad_http_update_never_replaces_valid_cache(tmp_path, local_http, response):
  path = tmp_path / 'cache.json'
  original = fetch(local_http, path, Response(MINIMAL_BODY), version='retained-good-version')
  original_bytes = path.read_bytes()
  serve, requests = local_http
  url = serve(response)
  with pytest.raises((ValueError, OSError, HTTPException)):
    download_database(url, str(path), 'rejected-version', timeout_s=2)
  assert requests[-1] == urlsplit(url).path
  assert_cache_unchanged(path, original_bytes, original)
  latitude, longitude = position_behind(51.6482993, 39.0990694, 97, 10)
  match = find_nearest_camera(original.cameras, latitude, longitude, heading_deg=97)
  assert match is not None and match.camera == original.cameras[0]


@pytest.mark.parametrize('framing', ['length', 'close', 'chunked'])
def test_download_size_limit_never_replaces_valid_cache(tmp_path, local_http, framing):
  path = tmp_path / 'cache.json'
  original = fetch(local_http, path, Response(MINIMAL_BODY))
  original_bytes = path.read_bytes()
  serve, _ = local_http
  url = serve(Response(SMALL_TEXT.encode('cp1251'), framing=framing))
  with pytest.raises(ValueError, match='size limit'):
    download_database(url, str(path), 'too-large', timeout_s=2, max_bytes=len(MINIMAL_BODY))
  assert_cache_unchanged(path, original_bytes, original)


def test_successful_update_replaces_cache_with_complete_new_snapshot(tmp_path, local_http):
  path = tmp_path / 'cache.json'
  old = fetch(local_http, path, Response(MINIMAL_BODY), version='old')
  new = fetch(local_http, path, Response(SMALL_TEXT.encode('cp1251')), version='new')
  assert old.sha256 != new.sha256 and len(old.cameras) == 1
  assert new.version == 'new' and len(new.cameras) == 5
  assert load_database(str(path)) == new
  assert not list(tmp_path.glob('.speed_cameras_*.tmp'))


@pytest.fixture
def external_full_body():
  location = os.environ.get('CITYGUIDE_FULL_FIXTURE')
  if not location:
    pytest.skip('Set CITYGUIDE_FULL_FIXTURE to the external 5,305,735-byte user SpeedCam.bkm')
  path = Path(location)
  assert path.is_file(), f'External fixture does not exist: {path}'
  body = path.read_bytes()
  assert len(body) == FULL_SIZE
  assert hashlib.sha256(body).hexdigest() == FULL_SHA256
  return body


def test_external_full_file_real_downloader_cache_reload_and_nearest(tmp_path, local_http, external_full_body):
  body = external_full_body
  # The raw count is independent of the production parser; it is not an
  # assertion that every warning has speed or is eligible for auto control.
  rows = body.decode('cp1251').splitlines()
  assert rows[0] == '2|Radars|1251'
  records = [row for row in rows[1:] if row.strip()]
  assert len(records) == FULL_RECORDS
  raw_types = Counter(f'speedcamonline-cg77:{row.split("|", 1)[0] or "unknown"}' for row in records)
  raw_positive_speed = 0
  raw_eligible = 0
  raw_directions = 0
  expected = []
  for row in records:
    fields = row.split('|')
    parameters = dict(zip(fields[4::2], fields[5::2], strict=False))  # Ignore the optional final delimiter.
    speed = float(parameters.get('1709') or 0)
    raw_positive_speed += speed > 0
    # Independent oracle from the selected point-type policy and raw BKM
    # fields, not production normalization helpers or their eligibility flags.
    directional_point = (fields[0] in ('18059', '18950', '18958') and
                         parameters.get('1713') in ('0', '1', '2') and '1705' in parameters)
    first = (float(parameters['1705']) + 180) % 360 if directional_point else None
    second = (float(parameters['1707']) + 180) % 360 if directional_point and '1707' in parameters else None
    eligible = directional_point and 0 < speed <= 300
    raw_directions += directional_point
    raw_eligible += eligible
    expected.append((fields, parameters, speed or None, first, second, eligible))
  assert raw_positive_speed == 70_164
  assert raw_directions == 60_149
  assert raw_eligible == 59_669
  parsed = parse_camera_snapshot(body)
  assert len(parsed) == FULL_RECORDS

  path = tmp_path / 'full-cache.json'
  downloaded = fetch(local_http, path, Response(body), version='user-cityguide-full')
  loaded = load_database(str(path))
  assert loaded == downloaded
  assert loaded.cameras == tuple(parsed)
  assert loaded.sha256 == FULL_SHA256 and len(loaded.cameras) == FULL_RECORDS
  assert Counter(camera.camera_type for camera in loaded.cameras) == raw_types
  assert raw_types['speedcamonline-cg77:unknown'] == 2
  for camera, (fields, parameters, speed, first, second, eligible) in zip(loaded.cameras, expected, strict=True):
    assert camera.source_id == fields[1]
    assert camera.source_params == parameters
    assert (camera.latitude, camera.longitude) == (float(fields[2]), float(fields[3]))
    assert camera.speed_limit_kph == speed
    assert camera.direction_deg == first and camera.second_direction_deg == second
    assert camera.direction_type == (1 if first is not None else None)
    assert camera.allowed_auto_control == eligible
    assert camera.end_latitude is None and camera.end_longitude is None
  assert sum(camera.allowed_auto_control for camera in loaded.cameras) == raw_eligible
  positive_speed = sum(camera.speed_limit_kph is not None and camera.speed_limit_kph > 0 for camera in loaded.cameras)
  assert positive_speed == raw_positive_speed

  first = loaded.cameras[0]
  assert (first.latitude, first.longitude, first.speed_limit_kph) == pytest.approx((51.6482993, 39.0990694, 60))
  assert first.direction_deg == 97 and first.source_params['1705'] == '277'
  nearest = find_nearest_camera(loaded.cameras, first.latitude, first.longitude, max_distance_m=1)
  assert nearest is not None and nearest.camera == first and nearest.distance_m == pytest.approx(0)
  latitude, longitude = position_behind(first.latitude, first.longitude, 97, 10)
  ahead = find_nearest_camera(loaded.cameras, latitude, longitude, heading_deg=97, max_distance_m=10.1)
  assert ahead is not None and ahead.camera == first and ahead.distance_m == pytest.approx(10, abs=0.01)
  assert find_nearest_camera(loaded.cameras, latitude, longitude, heading_deg=277, max_distance_m=10.1) is None
  print(json.dumps({
    'bytes': len(body), 'sha256': loaded.sha256, 'records': len(loaded.cameras),
    'records_with_positive_speed': positive_speed,
    'auto_control_eligible': sum(camera.allowed_auto_control for camera in loaded.cameras),
    'types': dict(sorted(raw_types.items())), 'nearest_ahead_m': ahead.distance_m,
  }, sort_keys=True))


@pytest.mark.parametrize('failure', ['empty', 'html', 'short-complete-row-boundary'])
def test_external_full_cache_survives_bad_http_download(tmp_path, local_http, external_full_body, failure):
  body = external_full_body
  path = tmp_path / 'full-cache.json'
  original = fetch(local_http, path, Response(body), version='complete-user-snapshot')
  original_bytes = path.read_bytes()
  if failure == 'short-complete-row-boundary':
    # Cut on a valid newline: the parser alone cannot detect this truncation.
    boundary = body.find(b'\n', len(body) // 2) + 1
    assert 0 < boundary < len(body)
    prefix = body[:boundary]
    assert 0 < len(parse_camera_snapshot(prefix)) < FULL_RECORDS
    response = Response(prefix, declared_length=len(body))
  else:
    response = Response(b'' if failure == 'empty' else b'<html>temporarily unavailable</html>\n')
  serve, _ = local_http
  url = serve(response)
  with pytest.raises((ValueError, OSError, HTTPException)):
    download_database(url, str(path), 'incomplete-user-snapshot', timeout_s=5)
  assert_cache_unchanged(path, original_bytes, original)
  assert len(original.cameras) == FULL_RECORDS
