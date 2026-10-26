"""Offline producer/publication tests; only synthetic data unless env opts in.

python -B -m pytest -c tools/tests/camera_replay_pytest.ini -p no:cacheprovider tools/tests/test_speedcam_snapshot.py -q
Optional read-only full-file test: SPEEDCAM_BKM_FIXTURE=/outside/repository/SpeedCam.bkm
"""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('_speedcam_snapshot_tested', ROOT / 'tools/speedcam_snapshot.py')
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)
NOW = 1_800_000_000
HEADER = b'2|Radars|1251\r\n'
ROW = b'18059|RU-1|55.75|37.62|1709|60|1713|1|1705|90|\r\n'


@pytest.fixture(scope='module')
def body():
  # Preserve duplicate labels, unfamiliar type/region, empty type and zero speed.
  different = [ROW,
               b'18950|RU-2|50|30|1709|40|\r\n',
               b'18958|RU-3|60|40|1709|0|\r\n',
               b'18952|RU-4|60|40|1709|0|\r\n',
               b'|RU-unknown|55|37|1709|0|\r\n',
               '999999|BY-Неизвестный|53.9|27.5|1709|0|\r\n'.encode('cp1251')]
  return HEADER + b''.join(different) + ROW * (snapshot.MIN_RECORDS - len(different))


@pytest.fixture(autouse=True)
def offline(monkeypatch):
  snapshot._helpers()
  database = sys.modules['openpilot.sunnypilot.mapd.camera_database']
  def forbidden(*args, **kwargs):
    pytest.fail('test attempted real network/GitHub access')
  monkeypatch.setattr(database, 'urlopen', forbidden)
  monkeypatch.setattr(snapshot.GitHubRelease, '_run', staticmethod(forbidden))
  monkeypatch.setattr(snapshot, '_unix_now', lambda: NOW)


def provide(monkeypatch, body):
  snapshot._helpers()
  calls = []
  def fake(url, timeout_s, max_bytes):
    calls.append((url, timeout_s, max_bytes))
    return body
  monkeypatch.setattr(sys.modules['openpilot.sunnypilot.mapd.camera_database'], 'fetch_snapshot', fake)
  return calls


@pytest.fixture
def built(monkeypatch, tmp_path, body):
  provide(monkeypatch, body)
  manifest = snapshot.build_snapshot(tmp_path)
  return tmp_path, manifest


def test_fixed_source_raw_bytes_all_records_and_manifest(monkeypatch, tmp_path, body):
  calls = provide(monkeypatch, body)
  manifest = snapshot.build_snapshot(tmp_path)
  digest = hashlib.sha256(body).hexdigest()
  assert calls == [(snapshot.SOURCE_URL, 120, 64 * 1024 * 1024)]
  assert manifest == {
    'schema_version': 1, 'source_url': snapshot.SOURCE_URL, 'fetched_at': NOW, 'generated_at': NOW,
    'record_count': 10_000, 'sha256': digest, 'size_bytes': len(body), 'asset_name': f'speedcam-rus-{digest}.bkm',
  }
  assert (tmp_path / manifest['asset_name']).read_bytes() == body
  assert snapshot.verify_snapshot(tmp_path)[0] == manifest


@pytest.mark.parametrize('invalid', [b'', HEADER, HEADER + ROW,
    b'<html>200 maintenance</html>', b'2|Radars|999\n' + ROW,
    HEADER + ROW * 9999, HEADER + ROW * 10000 + b'18059|broken\n',
    (HEADER + ROW * 10000).rstrip(b'\r\n')],
    ids=['empty', 'header-only', 'one-row', 'html', 'unknown-header', 'below-minimum', 'bad-tail', 'missing-newline'])
def test_invalid_or_tiny_200_preserves_previous_artifacts(monkeypatch, built, invalid):
  directory, manifest = built
  before = {p.name: p.read_bytes() for p in directory.iterdir()}
  provide(monkeypatch, invalid)
  with pytest.raises(ValueError):
    snapshot.build_snapshot(directory)
  assert {p.name: p.read_bytes() for p in directory.iterdir()} == before
  assert manifest['record_count'] == 10000


def test_oversize_rejected_before_parse(monkeypatch, tmp_path):
  provide(monkeypatch, b'x' * (snapshot.MAX_BYTES + 1))
  with pytest.raises(ValueError, match='64 MiB'):
    snapshot.build_snapshot(tmp_path)
  assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('failure', [TimeoutError('interrupted'), OSError('read failure')])
def test_transport_failure_never_replaces_manifest_or_retries(monkeypatch, built, failure):
  directory, _ = built
  before = {p.name: p.read_bytes() for p in directory.iterdir()}
  calls = []
  def fail(*args, **kwargs):
    calls.append(args)
    raise failure
  monkeypatch.setattr(sys.modules['openpilot.sunnypilot.mapd.camera_database'], 'fetch_snapshot', fail)
  with pytest.raises(type(failure)):
    snapshot.build_snapshot(directory)
  assert len(calls) == 1
  assert {p.name: p.read_bytes() for p in directory.iterdir()} == before


def test_immutable_local_name_cannot_be_overwritten(built, monkeypatch, body):
  directory, manifest = built
  asset = directory / manifest['asset_name']
  asset.write_bytes(b'corrupt old artifact')
  before = (directory / 'manifest.json').read_bytes()
  provide(monkeypatch, body)
  with pytest.raises(ValueError, match='immutable'):
    snapshot.build_snapshot(directory)
  assert asset.read_bytes() == b'corrupt old artifact'
  assert (directory / 'manifest.json').read_bytes() == before


class FakeRelease:
  def __init__(self, failure=None):
    self.events = []
    self.failure = failure
    self.data = {1: {'name': 'manifest.json', 'body': b'previous-manifest'},
                 2: {'name': 'previous-snapshot.bkm', 'body': b'previous-raw'}}

  def ensure(self):
    self.events.append(('ensure',))

  def assets(self):
    return [dict(id=key, name=value['name'], state='uploaded', size=len(value['body'])) for key, value in self.data.items()]

  def upload(self, path):
    self.events.append(('upload', path.name))
    content = path.read_bytes()
    assert content
    kind = 'bkm' if path.suffix == '.bkm' else 'manifest'
    if self.failure == f'{kind}_upload':
      raise OSError('synthetic upload failure')
    if self.failure == f'{kind}_checksum':
      content = b'!' + content[1:]
    assert not any(v['name'] == path.name for v in self.data.values())
    self.data[max(self.data) + 1] = {'name': path.name, 'body': content}

  def download(self, asset, directory):
    self.events.append(('download', asset['name']))
    path = directory / asset['name']
    path.write_bytes(self.data[asset['id']]['body'])
    return path

  def rename(self, asset_id, name):
    self.events.append(('rename', asset_id, name))
    if self.failure == 'backup_rename' and asset_id == 1:
      raise OSError('synthetic backup failure')
    if self.failure == 'commit_rename' and asset_id != 1 and name == 'manifest.json':
      raise OSError('synthetic manifest switch failure')
    assert not any(v['name'] == name for key, v in self.data.items() if key != asset_id)
    self.data[asset_id]['name'] = name
    if self.failure == 'lost_ack' and name == 'manifest.json':
      raise TimeoutError('rename succeeded but acknowledgement lost')

  def manifest_body(self):
    return next(v['body'] for v in self.data.values() if v['name'] == 'manifest.json')


def test_publication_uploads_and_verifies_raw_before_switching_manifest(built, body):
  directory, manifest = built
  remote = FakeRelease()
  assert snapshot.publish_snapshot(directory, 'owner/repo', release=remote) == manifest
  events = remote.events
  assert events[1] == ('upload', manifest['asset_name'])
  assert events[2] == ('download', manifest['asset_name'])
  assert events[3][0] == 'upload' and events[3][1].startswith('manifest.next-')
  assert events[4] == ('download', events[3][1])
  assert events[-2][0] == 'rename' and events[-2][2].startswith('manifest.previous-')
  assert events[-1][0] == 'rename' and events[-1][2] == 'manifest.json'
  assert json.loads(remote.manifest_body()) == manifest
  assert remote.data[1]['body'] == b'previous-manifest'
  assert remote.data[2]['body'] == b'previous-raw'
  assert next(v['body'] for v in remote.data.values() if v['name'] == manifest['asset_name']) == body


@pytest.mark.parametrize('failure', ['bkm_upload', 'bkm_checksum', 'manifest_upload', 'manifest_checksum', 'backup_rename', 'commit_rename'])
def test_publication_failure_keeps_previous_manifest_and_raw(built, failure):
  directory, _ = built
  remote = FakeRelease(failure)
  with pytest.raises((OSError, ValueError)):
    snapshot.publish_snapshot(directory, 'owner/repo', release=remote)
  assert remote.manifest_body() == b'previous-manifest'
  assert remote.data[2]['body'] == b'previous-raw'


def test_ambiguous_success_is_confirmed_without_undoing_new_manifest(built):
  directory, manifest = built
  remote = FakeRelease('lost_ack')
  assert snapshot.publish_snapshot(directory, 'owner/repo', release=remote) == manifest
  assert json.loads(remote.manifest_body()) == manifest


def test_existing_hash_asset_is_verified_not_overwritten(built, body):
  directory, manifest = built
  remote = FakeRelease()
  remote.data[3] = {'name': manifest['asset_name'], 'body': body}
  snapshot.publish_snapshot(directory, 'owner/repo', release=remote)
  assert ('upload', manifest['asset_name']) not in remote.events
  assert ('download', manifest['asset_name']) in remote.events


def test_corrupt_existing_hash_asset_aborts_without_replacing_it(built, body):
  directory, manifest = built
  remote = FakeRelease()
  remote.data[3] = {'name': manifest['asset_name'], 'body': b'!' + body[1:]}
  with pytest.raises(ValueError, match='checksum'):
    snapshot.publish_snapshot(directory, 'owner/repo', release=remote)
  assert remote.manifest_body() == b'previous-manifest'
  assert not any(event[0] == 'upload' for event in remote.events)


@pytest.mark.parametrize(('key', 'value'), [
  ('schema_version', True), ('source_url', 'https://example.invalid/mirror'),
  ('sha256', '0' * 64), ('asset_name', '../outside.bkm'), ('size_bytes', 0),
  ('record_count', 10001), ('fetched_at', NOW - snapshot.MAX_AGE_S - 1),
  ('fetched_at', NOW + snapshot.CLOCK_SKEW_S + 1), ('fetched_at', False),
  ('generated_at', NOW - 1), ('generated_at', NOW + snapshot.CLOCK_SKEW_S + 1),
])
def test_invalid_or_stale_manifest_aborts_before_any_github_mutation(built, key, value):
  directory, manifest = built
  manifest[key] = value
  if key == 'fetched_at' and type(value) is int and value > NOW:
    manifest['generated_at'] = value
  (directory / 'manifest.json').write_text(json.dumps(manifest))
  remote = FakeRelease()
  with pytest.raises(ValueError):
    snapshot.publish_snapshot(directory, 'owner/repo', release=remote)
  assert remote.events == []
  assert remote.manifest_body() == b'previous-manifest'


@pytest.mark.parametrize('offset', [-snapshot.MAX_AGE_S, snapshot.CLOCK_SKEW_S])
def test_timestamp_window_boundaries(built, offset):
  directory, manifest = built
  manifest['fetched_at'] = NOW + offset
  manifest['generated_at'] = max(NOW, manifest['fetched_at'])
  (directory / 'manifest.json').write_text(json.dumps(manifest))
  assert snapshot.verify_snapshot(directory)[0] == manifest


def test_tampered_raw_file_cannot_start_publication(built):
  directory, manifest = built
  (directory / manifest['asset_name']).write_bytes(b'')
  remote = FakeRelease()
  with pytest.raises(ValueError):
    snapshot.publish_snapshot(directory, 'owner/repo', release=remote)
  assert remote.events == []


def test_gh_adapter_does_not_clobber_delete_or_mark_latest(monkeypatch, tmp_path):
  calls = []
  def gh(*args):
    calls.append(args)
    if args[:2] == ('api', 'repos/owner/repo/releases/tags/speedcamonline-rus'):
      return b'{"id":7,"draft":false,"prerelease":false}'
    if '--slurp' in args:
      return b'[[{"id":1}],[{"id":2}]]'
    return b'{}'
  monkeypatch.setattr(snapshot.GitHubRelease, '_run', staticmethod(gh))
  remote = snapshot.GitHubRelease('owner/repo')
  remote.ensure()
  assert remote.assets() == [{'id': 1}, {'id': 2}]
  remote.upload(tmp_path / 'asset.bkm')
  remote.rename(1, 'manifest.json')
  assert ('release', 'edit', snapshot.RELEASE_TAG, '--repo', 'owner/repo', '--latest=false') in calls
  assert all('--clobber' not in c and 'DELETE' not in c for c in calls)


def test_gh_auth_failure_does_not_create_a_release(monkeypatch):
  calls = []
  def denied(*args):
    calls.append(args)
    raise subprocess.CalledProcessError(1, ['gh'], stderr=b'gh: Forbidden (HTTP 403)')
  monkeypatch.setattr(snapshot.GitHubRelease, '_run', staticmethod(denied))
  with pytest.raises(subprocess.CalledProcessError):
    snapshot.GitHubRelease('owner/repo').ensure()
  assert len(calls) == 1


def test_workflow_gates_publish_on_tests_fetch_and_checksum():
  workflow = yaml.load((ROOT / '.github/workflows/speedcam-database.yml').read_text(), Loader=yaml.BaseLoader)
  assert workflow['permissions'] == {'contents': 'write'}
  assert workflow['concurrency'] == {'group': 'speedcamonline-rus', 'cancel-in-progress': 'false'}
  assert workflow['on']['push']['branches'] == ['dev']
  assert workflow['on']['schedule'] == [{'cron': '17 */6 * * *'}]
  assert 'workflow_dispatch' in workflow['on']
  job = workflow['jobs']['publish']
  assert job['runs-on'] == 'ubuntu-24.04'
  steps = job['steps']
  assert steps[0]['uses'] == 'actions/checkout@11d5960a326750d5838078e36cf38b85af677262'
  assert steps[0]['with'] == {'ref': 'dev', 'persist-credentials': 'false'}
  assert steps[1]['uses'] == 'actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065'
  assert steps[1]['with']['python-version'] == '3.11'
  assert '-m pytest' in steps[2]['run']
  assert ' build ' in steps[3]['run'] and ' verify ' in steps[4]['run'] and ' publish ' in steps[5]['run']
  assert steps[5]['env'] == {'GH_TOKEN': '${{ github.token }}'}
  assert '$GITHUB_REPOSITORY' in steps[5]['run']
  assert all('if' not in step and 'continue-on-error' not in step for step in steps)
  assert all('secrets.' not in str(step) for step in steps)


def test_optional_user_fixture_is_read_only():
  filename = os.environ.get('SPEEDCAM_BKM_FIXTURE')
  if not filename:
    pytest.skip('set SPEEDCAM_BKM_FIXTURE to an existing offline user BKM')
  file = Path(filename)
  before = file.stat()
  body = snapshot._read_bounded(file)
  assert snapshot.validate_body(body) == 74016
  assert len(body) == 5305735
  assert hashlib.sha256(body).hexdigest() == '0ccaba30044729081e36cc006817d81501ad52dbe9eaf2ddbf35aa5751482c6c'
  assert file.stat().st_mtime_ns == before.st_mtime_ns
