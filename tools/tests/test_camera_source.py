"""Offline same-provider delivery contract; all HTTP entry points are stubbed.

python -B -m pytest -c tools/tests/camera_replay_pytest.ini \
  -p no:cacheprovider tools/tests/test_camera_source.py -q
"""

import hashlib
import io
import json
import sys
from email.message import Message
from pathlib import Path
from types import ModuleType

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
from openpilot.sunnypilot.mapd import camera_source as source
from openpilot.sunnypilot.mapd.cityguide import parse_cityguide_snapshot


NOW = 1_800_000_000.0
HEADER = b'2|Radars|1251\n'
ROW = b'18059|RU-4|55|37|1709|60|1713|0|1705|180|1706|100|\n'


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
  def blocked(*args, **kwargs):
    pytest.fail('Source tests must not access a real HTTP endpoint')
  monkeypatch.setattr(cache, 'urlopen', blocked)


@pytest.fixture(scope='module')
def full_asset():
  return HEADER + b''.join(ROW.replace(b'RU-4', f'RU-{index}'.encode()) for index in range(10_000))


@pytest.fixture
def previous(tmp_path):
  path = tmp_path / 'cameras.json'
  database = cache.CameraDatabase('known-good', NOW - 86400, tuple(parse_cityguide_snapshot(HEADER + ROW)))
  cache.save_database(str(path), database)
  return path, path.read_bytes(), database


def unchanged(previous):
  path, raw, database = previous
  assert path.read_bytes() == raw
  assert cache.load_database(str(path)) == database
  assert not list(path.parent.glob('.speed_cameras_*.tmp'))


class Delivery:
  def __init__(self, body):
    digest = hashlib.sha256(body).hexdigest()
    self.asset = body
    self.manifest = {'schema_version': 1, 'source_url': source.DEFAULT_SPEED_CAMERA_URL,
                     'fetched_at': NOW - 3600, 'record_count': 10000, 'sha256': digest,
                     'size_bytes': len(body), 'asset_name': f'speedcam-rus-{digest}.bkm'}
    self.raw_manifest = None
    self.calls = []
    self.clock = 0.0
    self.direct_elapsed, self.manifest_elapsed, self.asset_elapsed = 30.0, 1.0, 1.0
    self.manifest_error = self.asset_error = None

  def direct(self, url, path, version, timeout_s, max_bytes):
    self.calls.append(('direct', url, timeout_s, max_bytes))
    self.clock += self.direct_elapsed
    raise TimeoutError('synthetic direct source timeout')

  def fetch(self, url, timeout_s, max_bytes):
    self.calls.append(('fetch', url, timeout_s, max_bytes))
    if url == source.MIRROR_MANIFEST_URL:
      self.clock += self.manifest_elapsed
      if self.manifest_error:
        raise self.manifest_error
      return self.raw_manifest if self.raw_manifest is not None else json.dumps(self.manifest).encode()
    assert url == source.MIRROR_RELEASE_BASE + self.manifest['asset_name'], 'unexpected asset URL'
    self.clock += self.asset_elapsed
    if self.asset_error:
      raise self.asset_error
    return self.asset

  def replace_asset_and_digest(self, body):
    self.asset = body
    digest = hashlib.sha256(body).hexdigest()
    self.manifest.update(size_bytes=len(body), sha256=digest, asset_name=f'speedcam-rus-{digest}.bkm')


@pytest.fixture
def delivery(monkeypatch, full_asset):
  result = Delivery(full_asset)
  monkeypatch.setattr(source, 'download_database', result.direct)
  monkeypatch.setattr(source, 'fetch_snapshot', result.fetch)
  monkeypatch.setattr(source.time, 'time', lambda: NOW)
  monkeypatch.setattr(source.time, 'monotonic', lambda: result.clock)
  return result


def run(delivery, previous, **kwargs):
  return source.download_camera_database(source.DEFAULT_SPEED_CAMERA_URL, str(previous[0]), 'caller-version', **kwargs)


def test_fixed_same_provider_fallback_persists_original_age_version_metadata(delivery, previous):
  result = run(delivery, previous)
  assert result.provider == 'speedcamonline.ru' and result.version == 'caller-version'
  assert result.fetched_at == NOW - 3600
  assert result.sha256 == delivery.manifest['sha256']
  assert len(result.cameras) == 10000
  assert result.cameras[0].source_id == 'RU-0' and result.cameras[0].allowed_auto_control
  assert cache.load_database(str(previous[0])) == result
  assert cache.cache_age_seconds(str(previous[0]), now=NOW) == 3600
  assert delivery.calls == [
    ('direct', source.DEFAULT_SPEED_CAMERA_URL, 30, 64 * 1024 * 1024),
    ('fetch', source.MIRROR_MANIFEST_URL, 90, source.MANIFEST_MAX_BYTES),
    ('fetch', source.MIRROR_RELEASE_BASE + delivery.manifest['asset_name'], 89, len(delivery.asset)),
  ]


@pytest.mark.parametrize('field', ['schema_version', 'source_url', 'fetched_at', 'record_count',
                                  'sha256', 'size_bytes', 'asset_name'])
def test_missing_manifest_fields_keep_cache(delivery, previous, field):
  del delivery.manifest[field]
  with pytest.raises(ValueError):
    run(delivery, previous)
  assert len(delivery.calls) == 2
  unchanged(previous)


@pytest.mark.parametrize('field,value', [
  ('schema_version', 2), ('schema_version', True), ('schema_version', 1.0),
  ('source_url', 'https://other.invalid/'),
  ('source_url', source.DEFAULT_SPEED_CAMERA_URL.rstrip('/')),
  ('fetched_at', '1800000000'), ('fetched_at', None), ('fetched_at', True),
  ('fetched_at', float('nan')), ('fetched_at', float('inf')), ('fetched_at', -1),
  ('fetched_at', NOW - 7 * 86400 - 0.01), ('fetched_at', NOW + 300.01),
  ('record_count', 9999), ('record_count', 10000.0), ('record_count', True),
  ('size_bytes', 0), ('size_bytes', -1), ('size_bytes', 100.0), ('size_bytes', True),
  ('size_bytes', 64 * 1024 * 1024 + 1),
  ('sha256', '0' * 63), ('sha256', 'G' * 64), ('sha256', None),
  ('asset_name', '../SpeedCam.bkm'), ('asset_name', 'https://other.invalid/asset.bkm'),
  ('asset_name', 'speedcam-rus-' + '0' * 64 + '.bkm'),
])
def test_invalid_manifest_fields_reject_before_asset_fetch(delivery, previous, field, value):
  delivery.manifest[field] = value
  with pytest.raises(ValueError):
    run(delivery, previous)
  assert len(delivery.calls) == 2
  unchanged(previous)


@pytest.mark.parametrize('raw', [b'{}', b'[]', b'null', b'{"schema_version":1,', b'\xff',
                               b'{"schema_version":1,"schema_version":1}', b' ' * 65537],
                         ids=['empty', 'array', 'null', 'truncated-json', 'bad-encoding', 'duplicate', 'oversized'])
def test_bad_json_duplicates_encoding_and_oversized_manifest_fail_closed(delivery, previous, raw):
  delivery.raw_manifest = raw
  with pytest.raises(ValueError):
    run(delivery, previous)
  assert len(delivery.calls) == 2
  unchanged(previous)


@pytest.mark.parametrize('offset', [-7 * 86400, 300])
def test_timestamp_boundaries_are_inclusive_without_rejuvenation(delivery, previous, offset):
  delivery.manifest['fetched_at'] = NOW + offset
  assert run(delivery, previous).fetched_at == NOW + offset


def test_manifest_urls_are_never_used_as_fetch_targets(delivery, previous):
  delivery.manifest.update(asset_url='https://other.invalid/evil', manifest_url='file:///evil')
  run(delivery, previous)
  assert [call[1] for call in delivery.calls[1:]] == [
    source.MIRROR_MANIFEST_URL, source.MIRROR_RELEASE_BASE + delivery.manifest['asset_name']]


@pytest.mark.parametrize('failure', ['short', 'oversized', 'hash', 'count', 'small', 'truncated', 'bad-row', 'html'])
def test_asset_integrity_and_strict_parser_failures_never_replace_cache(delivery, previous, failure):
  if failure == 'short':
    delivery.asset = delivery.asset[:-1]
  elif failure == 'oversized':
    delivery.asset += b'\n'
  elif failure == 'hash':
    delivery.asset = delivery.asset.replace(b'1709|60', b'1709|80', 1)
  elif failure == 'count':
    delivery.manifest['record_count'] = 10001
  elif failure == 'small':
    delivery.replace_asset_and_digest(HEADER + ROW)
  elif failure == 'truncated':
    delivery.replace_asset_and_digest(delivery.asset[:-1])
  elif failure == 'bad-row':
    delivery.replace_asset_and_digest(delivery.asset + b'18059|bad|nan|37|1709|60|\n')
  elif failure == 'html':
    delivery.replace_asset_and_digest(b'<html>upstream failure</html>\n')
  with pytest.raises(ValueError):
    run(delivery, previous)
  unchanged(previous)


@pytest.mark.parametrize('phase', ['manifest', 'asset'])
def test_mirror_transport_failures_preserve_old_cache(delivery, previous, phase):
  setattr(delivery, phase + '_error', TimeoutError('synthetic mirror timeout'))
  with pytest.raises(TimeoutError):
    run(delivery, previous)
  unchanged(previous)


@pytest.mark.parametrize('timeout', [120, 1000])
def test_overall_budget_is_capped_and_does_not_restart_between_requests(delivery, previous, timeout):
  delivery.manifest_elapsed = 80
  run(delivery, previous, timeout_s=timeout)
  assert [call[2] for call in delivery.calls] == [30, 90, 10]


@pytest.mark.parametrize('phase', ['direct', 'manifest', 'asset'])
def test_exhausted_total_budget_never_commits_or_starts_another_request(delivery, previous, phase):
  if phase == 'direct':
    delivery.direct_elapsed = 120
  elif phase == 'manifest':
    delivery.manifest_elapsed = 90
  else:
    delivery.asset_elapsed = 89
  with pytest.raises(TimeoutError, match='total deadline'):
    run(delivery, previous)
  assert len(delivery.calls) == {'direct': 1, 'manifest': 2, 'asset': 3}[phase]
  unchanged(previous)


def test_caller_smaller_budget_is_respected(delivery, previous):
  delivery.direct_elapsed = 5
  with pytest.raises(TimeoutError):
    run(delivery, previous, timeout_s=5)
  assert delivery.calls[0][2] == 5
  assert len(delivery.calls) == 1
  unchanged(previous)


def test_direct_success_never_looks_up_mirror(delivery, previous, monkeypatch):
  calls = []
  def direct(url, path, version, timeout_s, max_bytes):
    calls.append((url, path, version, timeout_s))
    return previous[2]
  monkeypatch.setattr(source, 'download_database', direct)
  assert run(delivery, previous) is previous[2]
  assert calls == [(source.DEFAULT_SPEED_CAMERA_URL, str(previous[0]), 'caller-version', 30)]
  assert delivery.calls == []


@pytest.mark.parametrize('url', ['https://custom.invalid/cameras', source.DEFAULT_SPEED_CAMERA_URL.rstrip('/'),
                              source.DEFAULT_SPEED_CAMERA_URL + '?all=1',
                              source.DEFAULT_SPEED_CAMERA_URL.replace('https:', 'http:')])
def test_custom_url_has_no_fallback_and_preserves_failure(delivery, previous, url):
  with pytest.raises(TimeoutError, match='direct source timeout'):
    source.download_camera_database(url, str(previous[0]), 'custom', timeout_s=50)
  assert delivery.calls == [('direct', url, 50, 64 * 1024 * 1024)]
  unchanged(previous)


@pytest.mark.parametrize('kwargs', [{'timeout_s': 0}, {'timeout_s': float('nan')}, {'timeout_s': float('inf')},
                                  {'max_bytes': 0}, {'max_bytes': 1.5}])
def test_invalid_options_never_request_or_replace(delivery, previous, kwargs):
  with pytest.raises(ValueError):
    run(delivery, previous, **kwargs)
  assert delivery.calls == []
  unchanged(previous)


class Response(io.BytesIO):
  def __init__(self, body):
    super().__init__(body)
    self.status = 200
    self.headers = Message()
    self.headers['Content-Length'] = str(len(body))
    self.headers['Content-Type'] = 'application/json'


def test_public_fetch_snapshot_returns_exact_bytes_without_parsing_or_saving(monkeypatch):
  body = b'{"producer":"raw transport only"}\n'
  response = Response(body)
  monkeypatch.setattr(cache, 'urlopen', lambda request, timeout: response)
  def forbidden(*args, **kwargs):
    pytest.fail('fetch_snapshot must not parse or save')
  monkeypatch.setattr(cache, 'parse_camera_snapshot', forbidden)
  monkeypatch.setattr(cache, 'save_database', forbidden)
  assert cache.fetch_snapshot('https://example.invalid/raw', timeout_s=1, max_bytes=1024) == body
  assert response.closed


def test_real_direct_import_uses_public_fetch_helper_without_fallback(previous, monkeypatch):
  response = Response(HEADER + ROW)
  calls = []
  def open_response(request, timeout):
    calls.append((request.full_url, timeout))
    return response
  monkeypatch.setattr(cache, 'urlopen', open_response)
  def forbidden(*args, **kwargs):
    pytest.fail('successful direct import must not fetch a mirror')
  monkeypatch.setattr(source, 'fetch_snapshot', forbidden)
  result = source.download_camera_database(source.DEFAULT_SPEED_CAMERA_URL, str(previous[0]), 'direct')
  assert calls[0][0] == source.DEFAULT_SPEED_CAMERA_URL and 0 < calls[0][1] <= 30
  assert result.version == 'direct' and result.provider == 'speedcamonline.ru'
  assert cache.load_database(str(previous[0])) == result
