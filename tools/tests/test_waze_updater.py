"""Worker scheduling, provider isolation and actual map publisher merge boundaries."""

import json
import threading
from types import SimpleNamespace as NS

import pytest

from test_waze_policy import event, NOW
from openpilot.sunnypilot.mapd.waze_updater import WazeCameraUpdater
from test_camera_control_selection import map_pipeline as map_pipeline, latitude
from openpilot.sunnypilot.mapd.waze_policy import events_to_cameras
from openpilot.sunnypilot.mapd.waze.clock import utc_now_s


class Params:
  def __init__(self):
    self.values = {}

  def put(self, key, value):
    self.values[key] = str(value)


class Backend:
  def __init__(self):
    self.calls = []
    self.fixed_error = False

  def fetch_fixed(self, *args):
    self.calls.append('fixed')
    if self.fixed_error:
      raise OSError('secret server response should never appear')
    return NS(events=[event()], complete=True, unknown_count=0, loaded_tiles=1, requested_tiles=1)

  def fetch_mobile(self, *args):
    self.calls.append('mobile')
    return NS(
      events=[event(source_kind='mobile', source_id='waze:mobile:post', raw_type='POLICE', raw_subtype='POLICE_WITH_MOBILE_CAMERA')],
      complete=True,
      unknown_count=0,
    )


def updater(tmp_path, backend=None):
  params = Params()
  backend = backend or Backend()
  clock = [NOW]
  mono = [1000.0]
  u = WazeCameraUpdater(params, tmp_path / 'waze', backend=backend, clock=lambda: clock[0], monotonic=lambda: mono[0])
  return u, params, backend, clock, mono


def refresh(u):
  assert u.tick(55.0, 37.0, enabled=True, position_valid=True)
  u._worker.join(3)
  assert not u._worker.is_alive()


def test_disabled_or_unlocalized_never_contacts_network(tmp_path):
  u, p, b, _, _ = updater(tmp_path)
  assert not u.tick(55, 37, enabled=False, position_valid=True)
  assert not u.tick(55, 37, enabled=True, position_valid=False)
  assert b.calls == [] and u.snapshot() == ()


def test_refresh_persists_bounded_metadata_and_reuses_schedule(tmp_path):
  u, p, b, _, _ = updater(tmp_path)
  refresh(u)
  assert b.calls == ['fixed', 'mobile'] and len(u.snapshot()) == 2
  assert not u.tick(55, 37, enabled=True, position_valid=True)
  stored = json.loads((tmp_path / 'waze/events.json').read_text())
  assert stored['schema_version'] == 1 and len(stored['events']) == 2
  assert p.values['SpeedCameraWazeCount'] == '2'
  assert '0 verified limits' in p.values['SpeedCameraWazeStatus']


def test_mobile_expiry_does_not_rejuvenate_on_restart(tmp_path):
  u, p, b, clock, _ = updater(tmp_path)
  refresh(u)
  clock[0] += 181
  assert len(u.snapshot()) == 1
  restarted = WazeCameraUpdater(p, tmp_path / 'waze', backend=b, clock=lambda: clock[0])
  restarted._enabled = True
  assert len(restarted.snapshot()) == 1


def test_partial_failure_keeps_other_provider_and_sanitizes_error(tmp_path):
  b = Backend()
  b.fixed_error = True
  u, p, _, _, _ = updater(tmp_path, b)
  refresh(u)
  assert [c.source_id for c in u.snapshot()] == ['waze:mobile:post']
  assert p.values['SpeedCameraWazeStatus'].startswith('partial')
  assert p.values['SpeedCameraWazeError'] == 'fixed:OSError'


def test_disable_during_request_discards_late_result_without_blocking(tmp_path):
  started = threading.Event()
  release = threading.Event()

  class Slow(Backend):
    def fetch_fixed(self, *args):
      started.set()
      assert release.wait(3)
      return super().fetch_fixed(*args)

  u, p, b, _, _ = updater(tmp_path, Slow())
  assert u.tick(55, 37, enabled=True, position_valid=True)
  assert started.wait(2)
  assert not u.tick(55, 37, enabled=False, position_valid=True)
  release.set()
  u._worker.join(3)
  assert u.snapshot() == () and not (tmp_path / 'waze/events.json').exists()
  assert p.values['SpeedCameraWazeStatus'] == 'disabled'


def test_incomplete_fixed_coverage_is_not_successful_empty_refresh(tmp_path):
  class Partial(Backend):
    def fetch_fixed(self, *args):
      return NS(events=[], complete=True, unknown_count=0, loaded_tiles=1, requested_tiles=2)

  u, p, _, _, _ = updater(tmp_path, Partial())
  refresh(u)
  assert p.values['SpeedCameraWazeError'] == 'fixed:ValueError'
  assert len(u.snapshot()) == 1


def test_waze_index_survives_missing_russian_database_and_repeated_ticks(map_pipeline):
  instance, config, _ = map_pipeline
  now = utc_now_s()
  waze = tuple(events_to_cameras([event(latitude=latitude(250), observed_at_ms=int(now * 1000), expires_at_ms=int((now + 3600) * 1000))], now))
  config['SpeedCameraWazeEnabled'] = '1'
  instance.waze_updater = NS(tick=lambda *a, **k: False, snapshot=lambda: waze)
  first = instance.get_speed_camera()
  index = instance.camera_index
  assert first is not None and first.camera.source_id.startswith('waze:')
  assert instance.camera_database is None
  assert instance.get_speed_camera() is not None and instance.camera_index is index


def test_disabling_waze_removes_only_waze_candidates(map_pipeline):
  instance, config, publish = map_pipeline
  now = utc_now_s()
  waze = tuple(events_to_cameras([event(latitude=latitude(200), observed_at_ms=int(now * 1000), expires_at_ms=int((now + 3600) * 1000))], now))
  enabled = [True]

  def tick(*a, **k):
    enabled[0] = k['enabled']

  config['SpeedCameraWazeEnabled'] = '1'
  instance.waze_updater = NS(tick=tick, snapshot=lambda: waze if enabled[0] else ())
  from test_camera_control_selection import bkm_row

  publish(bkm_row(18059, 500))
  assert len(tuple(instance.camera_index)) == 2
  config['SpeedCameraWazeEnabled'] = '0'
  instance.get_speed_camera()
  assert len(tuple(instance.camera_index)) == 1
  assert tuple(instance.camera_index)[0].camera_type.startswith('speedcamonline')


@pytest.mark.parametrize('raw', ['[]', 'null', '42', '{"schema_version":1,"events":null}'])
def test_valid_json_wrong_cache_shape_never_crashes_startup(tmp_path, raw):
  folder = tmp_path / 'waze'
  folder.mkdir()
  (folder / 'events.json').write_text(raw)
  u = WazeCameraUpdater(Params(), folder, backend=Backend(), clock=lambda: NOW)
  assert u.snapshot() == ()


def test_failed_refresh_never_updates_success_timestamp(tmp_path):
  class Failing(Backend):
    def fetch_fixed(self, *a):
      raise OSError()

    def fetch_mobile(self, *a):
      raise OSError()

  u, p, _, _, _ = updater(tmp_path, Failing())
  refresh(u)
  assert 'SpeedCameraWazeUpdatedAt' not in p.values
  assert not (tmp_path / 'waze/events.json').exists()


def test_snapshot_and_disable_do_not_wait_for_bulk_disk_serialization(tmp_path, monkeypatch):
  import openpilot.sunnypilot.mapd.waze_updater as module

  original = module._prepare_snapshot
  started = threading.Event()
  release = threading.Event()

  def delayed(*args):
    started.set()
    assert release.wait(3)
    return original(*args)

  monkeypatch.setattr(module, '_prepare_snapshot', delayed)
  u, p, b, _, _ = updater(tmp_path)
  assert u.tick(55, 37, enabled=True, position_valid=True)
  assert started.wait(2)
  assert len(u.snapshot()) == 2
  assert not u.tick(55, 37, enabled=False, position_valid=True)
  release.set()
  u._worker.join(3)
  assert not u._worker.is_alive()
  assert not (tmp_path / 'waze/events.json').exists()
  assert p.values['SpeedCameraWazeStatus'] == 'disabled'
