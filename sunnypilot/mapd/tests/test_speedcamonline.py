"""Offline SCO parser/download contract tests; no hardware or network access.

Portable Windows run: bootstrap openpilot namespace paths BEFORE pytest
collection, then select -c tools/tests/camera_replay_pytest.ini --noconftest.
Pytest may import mapd/__init__.py before this test module, so the in-module
shim alone is not a replacement for the pre-collection namespace bootstrap.

The seven-column header and first coordinate pair match the retained SCO iGo
prefix in workspace/results/speedcamonline_research (outside this checkout).
Fixtures below are small synthetic records, not a bundled provider database.
"""

import hashlib
import io
import itertools
import sys
from email.message import Message
from http.client import HTTPException, IncompleteRead
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[3]
if sys.platform == 'win32':
  for name, directory in (
    ('openpilot', ROOT),
    ('openpilot.sunnypilot', ROOT / 'sunnypilot'),
    ('openpilot.sunnypilot.mapd', ROOT / 'sunnypilot/mapd'),
  ):
    if name not in sys.modules:
      namespace = ModuleType(name)
      namespace.__path__ = [str(directory)]
      sys.modules[name] = namespace

from openpilot.sunnypilot.mapd import camera_database as cache
from openpilot.sunnypilot.mapd import speed_cameras as cameras


HEADER = 'IDX,X,Y,TYPE,SPEED,DIRTYPE,DIRECTION\r\n'
VALID_ROW = '4,39.0990694,51.6482993,1,60,1,97'
URL = 'https://speedcamonline.example.invalid/igo/Rus/'


def snapshot(*rows: str, encoding: str = 'utf-8', bom: bool = False) -> bytes:
  body = (HEADER + ''.join(row + '\r\n' for row in rows)).encode(encoding)
  return b'\xef\xbb\xbf' + body if bom else body


class Response(io.BytesIO):
  """HTTP-like stream; supports complete, chunked, and prematurely ended bodies."""

  def __init__(self, body: bytes, *, length: int | None = None, chunk_size: int | None = None,
               fail_after: int | None = None, error: Exception | None = None,
               content_type: str = 'text/plane; charset=windows-1251'):
    super().__init__(body)
    self.headers = Message()
    self.headers['Content-Type'] = content_type
    if length is None:
      self.headers['Transfer-Encoding'] = 'chunked'
    else:
      self.headers['Content-Length'] = str(length)
    self.status = 200
    self.chunk_size = chunk_size
    self.fail_after = fail_after
    self.error = error

  def read(self, size: int = -1) -> bytes:
    if self.fail_after is not None and self.tell() >= self.fail_after:
      raise self.error or TimeoutError('synthetic interrupted download')
    if self.chunk_size is not None:
      size = self.chunk_size if size < 0 else min(size, self.chunk_size)
    return super().read(size)

  def getheader(self, name, default=None):
    return self.headers.get(name, default)


@pytest.fixture(autouse=True)
def forbid_real_network(monkeypatch):
  def blocked(*args, **kwargs):
    pytest.fail('SCO tests must never open a real URL')

  monkeypatch.setattr(cache, 'urlopen', blocked)


@pytest.fixture
def previous_cache(tmp_path):
  path = tmp_path / 'speed_cameras.json'
  old = cache.CameraDatabase('known-good', 1_700_000_000.0, (
    cameras.SpeedCamera(55.75, 37.62, 80, 0, 'speedcamonline:1', direction_type=1),
  ))
  cache.save_database(str(path), old)
  return path, old, path.read_bytes()


def stub_response(monkeypatch, response):
  calls = []

  def open_response(request, timeout):
    calls.append((request.full_url, timeout))
    assert request.full_url == URL
    return response

  monkeypatch.setattr(cache, 'urlopen', open_response)
  return calls


def assert_previous_cache_unchanged(previous):
  path, old, before = previous
  assert path.read_bytes() == before
  assert cache.load_database(str(path)) == old
  assert list(path.parent.glob('.speed_cameras_*.tmp')) == []


@pytest.mark.parametrize(('encoding', 'bom'), [('utf-8', False), ('utf-8', True), ('cp1251', False)])
def test_real_sco_header_xy_coordinates_and_direction(encoding, bom):
  parsed = cameras.parse_camera_snapshot(snapshot(VALID_ROW, encoding=encoding, bom=bom))
  assert len(parsed) == 1
  camera = parsed[0]
  assert camera.longitude == pytest.approx(39.0990694)
  assert camera.latitude == pytest.approx(51.6482993)
  assert camera.speed_limit_kph == 60
  assert camera.direction_type == 1
  assert camera.direction_deg == 97
  assert camera.camera_type == 'speedcamonline:1'


@pytest.mark.parametrize(('encoding', 'bom'), [('cp1251', False), ('utf-8', True)])
def test_non_ascii_type_metadata_does_not_break_decoding(encoding, bom):
  # Numeric exports themselves are ASCII in both encodings. An unknown label
  # exercises the actual CP1251 fallback without inventing a speed-control type.
  parsed = cameras.parse_camera_snapshot(snapshot('1,37.62,55.75,Камера,0,0,0', encoding=encoding, bom=bom))
  assert len(parsed) == 1
  assert parsed[0].camera_type == 'speedcamonline:Камера'
  assert not parsed[0].allowed_auto_control
  assert not parsed[0].speed_limit_kph


def test_unicode_bom_text_input():
  parsed = cameras.parse_camera_snapshot('\ufeff' + HEADER + VALID_ROW + '\r\n')
  assert len(parsed) == 1 and parsed[0].longitude == pytest.approx(39.0990694)


@pytest.mark.parametrize(('kind', 'direction', 'heading', 'selected'), [
  (0, 180, 0, True), (1, 0, 0, True), (1, 180, 0, False),
  (2, 180, 0, True), (2, 90, 0, False),
  (1, 60, 0, True), (1, 60.01, 0, False), (1, 1, 359, True),
])
def test_igo_direction_zero_one_two_and_heading_wrap(kind, direction, heading, selected):
  parsed = cameras.parse_camera_snapshot(snapshot(f'1,37.62,55.751,1,60,{kind},{direction}'))
  assert len(parsed) == 1
  assert parsed[0].direction_type == kind
  match = cameras.find_nearest_camera(parsed, 55.75, 37.62, heading_deg=heading, max_distance_m=500)
  assert (match is not None) is selected


@pytest.mark.parametrize('kind', [0, 1, 2])
def test_any_direction_flag_still_rejects_camera_behind(kind):
  parsed = cameras.parse_camera_snapshot(snapshot(f'1,37.62,55.749,1,60,{kind},0'))
  assert cameras.find_nearest_camera(parsed, 55.75, 37.62, heading_deg=0, max_distance_m=500) is None


def test_unknown_zero_speed_remains_a_display_event_without_numeric_target():
  parsed = cameras.parse_camera_snapshot(snapshot('1,37.62,55.751,1,0,0,0'))
  match = cameras.find_nearest_camera(parsed, 55.75, 37.62, heading_deg=0)
  assert match is not None
  assert match.camera.speed_limit_kph in (None, 0)
  assert not (match.camera.allowed_auto_control and (match.camera.speed_limit_kph or 0) > 0)


@pytest.mark.parametrize('kind', [3, 4, 6, 7, 999])
def test_non_speed_or_unsupported_types_never_gain_auto_control(kind):
  # A numeric SPEED supplied on an unsupported type must not grant authority.
  parsed = cameras.parse_camera_snapshot(snapshot(f'1,37.62,55.751,{kind},90,0,0'))
  assert len(parsed) == 1  # keep the displayable event rather than hide it
  assert not parsed[0].allowed_auto_control
  assert not (parsed[0].allowed_auto_control and (parsed[0].speed_limit_kph or 0) > 0)


@pytest.mark.parametrize('kind', [1, 2, 5])
def test_supported_positive_speed_types_retain_provider_identity(kind):
  parsed = cameras.parse_camera_snapshot(snapshot(f'1,37.62,55.751,{kind},60,1,0'))
  assert parsed[0].camera_type == f'speedcamonline:{kind}'
  assert parsed[0].allowed_auto_control and parsed[0].speed_limit_kph == 60


@pytest.mark.parametrize('header', [
  'IDX,Y,TYPE,SPEED,DIRTYPE,DIRECTION',
  'IDX,X,Y,TYPE,SPEED,DIRECTION',
  'IDX,X,X,TYPE,SPEED,DIRTYPE,DIRECTION',
  'IDX;X;Y;TYPE;SPEED;DIRTYPE;DIRECTION',
  'THIS IS NOT A CAMERA EXPORT',
])
def test_malformed_header_is_not_accepted_as_a_snapshot(header):
  with pytest.raises(ValueError):
    cameras.parse_camera_snapshot((header + '\r\n' + VALID_ROW + '\r\n').encode())


BAD_ROWS = [
  pytest.param('5,37.62,55.75,1,60,1', id='truncated-fields'),
  pytest.param('5,37.62,55.75,1,60,1,0,EXTRA', id='extra-fields'),
  pytest.param('5,37.62,nan,1,60,1,0', id='nan-latitude'),
  pytest.param('5,inf,55.75,1,60,1,0', id='infinite-longitude'),
  pytest.param('5,181,55.75,1,60,1,0', id='longitude-out-of-range'),
  pytest.param('5,37.62,91,1,60,1,0', id='latitude-out-of-range'),
  pytest.param('5,37.62,55.75,1,60,3,0', id='unknown-direction-type'),
  pytest.param('5,37.62,55.75,1,60,1,361', id='direction-out-of-range'),
  pytest.param('5,37.62,55.75,1,-1,1,0', id='negative-speed'),
  pytest.param('5,37.62,55.75,1,nan,1,0', id='nan-speed'),
  pytest.param('5,37.62,55.75,1,broken,1,0', id='corrupt-speed'),
  pytest.param('5,37.62,55.75,1,60,1,"0', id='unterminated-quote'),
]


@pytest.mark.parametrize('bad_row', BAD_ROWS)
def test_malformed_row_after_valid_prefix_cannot_replace_good_cache(monkeypatch, previous_cache, bad_row):
  response = Response(snapshot(VALID_ROW, bad_row))
  stub_response(monkeypatch, response)
  with pytest.raises((ValueError, OSError, HTTPException)):
    cache.download_database(URL, str(previous_cache[0]), 'bad-update')
  assert response.closed
  assert_previous_cache_unchanged(previous_cache)


@pytest.mark.parametrize('body', [
  pytest.param(b'', id='empty'),
  pytest.param(b'\r\n  \r\n', id='whitespace'),
  pytest.param(HEADER.encode(), id='header-only'),
  pytest.param(b'\x98\x00\xff', id='corrupt-bytes'),
  pytest.param(b'<html>server error</html>', id='html-with-text-content-type'),
  pytest.param((HEADER + VALID_ROW + '\r\n5,37.62,55.').encode(), id='truncated-last-record'),
])
def test_empty_corrupt_or_truncated_download_keeps_cache(monkeypatch, previous_cache, body):
  response = Response(body)
  stub_response(monkeypatch, response)
  with pytest.raises((ValueError, OSError, HTTPException)):
    cache.download_database(URL, str(previous_cache[0]), 'bad-update')
  assert response.closed
  assert_previous_cache_unchanged(previous_cache)


@pytest.mark.parametrize('error', [TimeoutError('read timeout'), IncompleteRead(b'', 100)])
def test_transport_interruption_after_valid_records_keeps_cache(monkeypatch, previous_cache, error):
  body = snapshot(VALID_ROW)
  response = Response(body, chunk_size=len(body), fail_after=len(body), error=error)
  stub_response(monkeypatch, response)
  with pytest.raises((ValueError, OSError, HTTPException)):
    cache.download_database(URL, str(previous_cache[0]), 'interrupted-update')
  assert response.closed
  assert_previous_cache_unchanged(previous_cache)


def test_premature_eof_with_complete_rows_and_content_length_keeps_cache(monkeypatch, previous_cache):
  # Losing complete records at EOF cannot be detected by CSV validation alone.
  body = snapshot(VALID_ROW)
  response = Response(body, length=len(body) + 100)
  stub_response(monkeypatch, response)
  with pytest.raises((ValueError, OSError, HTTPException)):
    cache.download_database(URL, str(previous_cache[0]), 'short-update')
  assert_previous_cache_unchanged(previous_cache)


def test_html_content_type_keeps_cache_even_if_body_looks_like_csv(monkeypatch, previous_cache):
  response = Response(snapshot(VALID_ROW), content_type='text/html')
  stub_response(monkeypatch, response)
  with pytest.raises(ValueError):
    cache.download_database(URL, str(previous_cache[0]), 'html-update')
  assert_previous_cache_unchanged(previous_cache)


def test_size_limit_preserves_cache(monkeypatch, previous_cache):
  body = snapshot(VALID_ROW)
  stub_response(monkeypatch, Response(body))
  with pytest.raises(ValueError):
    cache.download_database(URL, str(previous_cache[0]), 'oversized-update', max_bytes=len(body) - 1)
  assert_previous_cache_unchanged(previous_cache)


def test_wallclock_deadline_preserves_cache_without_sleep(monkeypatch, previous_cache):
  response = Response(snapshot(VALID_ROW), chunk_size=10)
  stub_response(monkeypatch, response)
  times = itertools.count(10.0, 1.0)
  monkeypatch.setattr(cache.time, 'monotonic', lambda: next(times))
  with pytest.raises(TimeoutError):
    cache.download_database(URL, str(previous_cache[0]), 'slow-update', timeout_s=2)
  assert response.closed
  assert_previous_cache_unchanged(previous_cache)


@pytest.mark.parametrize(('length_header', 'chunk_size', 'bom'), [(True, None, False), (False, 7, True)])
def test_complete_download_round_trip_and_provenance(monkeypatch, previous_cache, length_header, chunk_size, bom):
  body = snapshot(VALID_ROW, '17,39.1805538,51.7129494,5,40,2,180', bom=bom)
  response = Response(body, length=len(body) if length_header else None, chunk_size=chunk_size)
  calls = stub_response(monkeypatch, response)
  downloaded = cache.download_database(URL, str(previous_cache[0]), 'sco-test-v1', timeout_s=3)
  assert calls == [(URL, 3)]
  assert response.closed and len(downloaded.cameras) == 2
  loaded = cache.load_database(str(previous_cache[0]))
  assert loaded == downloaded
  assert loaded.version == 'sco-test-v1'
  assert loaded.provider == 'speedcamonline.example.invalid'
  assert loaded.sha256 == hashlib.sha256(body).hexdigest()
  assert loaded.cameras[0].longitude == pytest.approx(39.0990694)
  assert loaded.cameras[0].allowed_auto_control
  assert loaded.cameras[1].direction_type == 2


def test_unknown_speed_and_non_speed_policy_survive_cache_round_trip(monkeypatch, previous_cache):
  body = snapshot('1,37.62,55.75,1,0,0,0', '2,37.63,55.76,3,90,0,0')
  stub_response(monkeypatch, Response(body))
  cache.download_database(URL, str(previous_cache[0]), 'display-only-cases')
  loaded = cache.load_database(str(previous_cache[0]))
  assert loaded is not None and len(loaded.cameras) == 2
  assert not loaded.cameras[0].speed_limit_kph
  assert not loaded.cameras[1].allowed_auto_control


def test_atomic_replace_failure_preserves_cache_and_removes_temporary_file(monkeypatch, previous_cache):
  stub_response(monkeypatch, Response(snapshot(VALID_ROW)))

  def fail_replace(*args):
    raise OSError('synthetic atomic replace failure')

  monkeypatch.setattr(cache.os, 'replace', fail_replace)
  with pytest.raises(OSError, match='atomic replace failure'):
    cache.download_database(URL, str(previous_cache[0]), 'uncommitted-update')
  assert_previous_cache_unchanged(previous_cache)
