"""Deterministic worker lifecycle tests with injected acquisition and policy."""
import importlib.util
import math
from pathlib import Path
import threading
from types import SimpleNamespace as NS
import types

import pytest


@pytest.fixture
def module(monkeypatch):
  policy = types.ModuleType('openpilot.sunnypilot.mapd.yandex_policy')
  def cameras(events, now):
    return [NS(source_id=e['source_id'], source_params=e, allowed_auto_control=e.get('verified', False))
            for e in events if e['observed_at'] <= now < e['expires_at']]
  policy.events_to_cameras = cameras
  monkeypatch.setitem(__import__('sys').modules, policy.__name__, policy)
  spec = importlib.util.spec_from_file_location('_yandex_updater_test', Path(__file__).resolve().parents[2] /
                                               'sunnypilot/mapd/yandex_updater.py')
  loaded = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(loaded)
  return loaded


class Params:
  def __init__(self):
    self.values, self.writes = {}, []

  def put(self, key, value):
    self.values[key] = value
    self.writes.append((key, value))


class Backend:
  def __init__(self):
    self.calls, self.events = [], ()
    self.complete, self.errors, self.error = True, (), None

  def fetch_window(self, lat, lon, heading, *, should_continue):
    self.calls.append((lat, lon, heading, should_continue))
    if self.error:
      raise self.error
    return NS(events=self.events, complete=self.complete, errors=self.errors)


def event(identity='yandex:native:1', **extra):
  return dict(source_id=identity, observed_at=1000, expires_at=1180, **extra)


def setup(module, tmp_path, backend=None):
  clock, mono = [1000], [10]
  params, backend = Params(), backend or Backend()
  updater = module.YandexCameraUpdater(params, tmp_path, backend=backend, clock=lambda: clock[0], monotonic=lambda: mono[0])
  return updater, params, backend, clock, mono


def tick(u, **extra):
  return u.tick(extra.pop('lat', 55), extra.pop('lon', 37), heading=extra.pop('heading', 0),
                enabled=extra.pop('enabled', True), position_valid=extra.pop('position_valid', True), **extra)


def finish(u):
  u._worker.join(3)
  assert not u._worker.is_alive()


def test_validation_statuses_and_unchanged_params(module, tmp_path):
  u, p, b, _, _ = setup(module, tmp_path)
  assert not tick(u, enabled=False)
  assert not tick(u, position_valid=False)
  assert p.values['SpeedCameraYandexStatus'] == 'waiting for localized position'
  assert not tick(u, heading=None)
  before = len(p.writes)
  assert not tick(u, heading=None)
  assert len(p.writes) == before
  assert not tick(u, lat=float('nan'))
  assert not b.calls


def test_success_expiry_no_disk_payload(module, tmp_path):
  u, p, b, clock, _ = setup(module, tmp_path)
  b.events = (event(verified=True),)
  assert tick(u)
  finish(u)
  assert len(u.snapshot()) == 1
  assert p.values['SpeedCameraYandexStatus'] == 'ready: 1 warnings; 1 verified limits'
  assert p.values['SpeedCameraYandexCount'] == '1'
  # Expiry must update the count even when refresh is unavailable/backing off.
  u._next_attempt = 999999
  clock[0] = 1180
  assert not tick(u)
  assert u.snapshot() == ()
  assert p.values['SpeedCameraYandexCount'] == '0'
  writes = len(p.writes)
  assert u.snapshot() == () and len(p.writes) == writes
  assert list(tmp_path.iterdir()) == []


def test_refresh_movement_heading_and_gap(module, tmp_path):
  u, _, b, _, mono = setup(module, tmp_path)
  assert tick(u)
  finish(u)
  assert not tick(u, heading=30)
  mono[0] += 5
  assert tick(u, heading=30)
  finish(u)
  mono[0] += 5
  assert not tick(u, heading=40)
  assert tick(u, lat=55.02, heading=30)
  finish(u)
  mono[0] += 60
  assert tick(u, lat=55.02, heading=30)
  finish(u)
  assert len(b.calls) == 4


def test_partial_merge_preserves_old_expiry(module, tmp_path):
  u, p, b, clock, mono = setup(module, tmp_path)
  b.events = (event(),)
  tick(u)
  finish(u)
  mono[0] += 60
  b.events, b.complete = (event('yandex:native:2'),), False
  tick(u)
  finish(u)
  assert len(u.snapshot()) == 2
  assert u._events[0]['expires_at'] == 1180
  assert p.values['SpeedCameraYandexError'] == 'incomplete_acquisition'
  clock[0] = 1180
  assert u.snapshot() == ()


@pytest.mark.parametrize('category', ['creds_unavailable', 'credentials_unavailable'])
def test_error_backoff_sanitized_credentials(module, tmp_path, category):
  u, p, b, _, mono = setup(module, tmp_path)
  error = RuntimeError('SECRET_API_KEY_AND_SERVER_URL')
  error.category = category
  b.error = error
  tick(u)
  finish(u)
  assert p.values['SpeedCameraYandexStatus'] == 'credentials unavailable'
  assert all('SECRET' not in value for value in p.values.values())
  mono[0] += 29
  assert not tick(u)
  mono[0] += 1
  assert tick(u)
  finish(u)
  assert u._next_attempt == mono[0] + 60


def test_disable_reenable_discards_late_result_and_finally(module, tmp_path):
  started, release = threading.Event(), threading.Event()
  class Blocking(Backend):
    def fetch_window(self, lat, lon, heading, *, should_continue):
      self.calls.append(should_continue)
      if len(self.calls) == 1:
        started.set()
        assert release.wait(3)
        return NS(events=(event('old'),), complete=True, errors=())
      return NS(events=(event('new'),), complete=True, errors=())
  b = Blocking()
  u, _, _, _, _ = setup(module, tmp_path, b)
  assert tick(u)
  assert started.wait(2)
  old_worker = u._worker
  assert not tick(u, enabled=False)
  assert not b.calls[0]()
  assert u.snapshot() == ()
  assert tick(u)
  finish(u)
  deadline = u._next_attempt
  release.set()
  old_worker.join(2)
  assert [c.source_id for c in u.snapshot()] == ['new']
  assert u._next_attempt == deadline


@pytest.mark.parametrize('events', [(event(),) * 5001, ({'bad': float('nan')},), ({'payload': 'x' * (8 * 1024 * 1024)},)])
def test_invalid_payload_rejected(module, tmp_path, events):
  u, p, b, _, _ = setup(module, tmp_path)
  b.events = events
  assert tick(u)
  finish(u)
  assert u.snapshot() == ()
  assert p.values['SpeedCameraYandexStatus'] == 'error'


def test_partial_retains_distinct_directed_road_variants(module, tmp_path):
  u, _, b, _, mono = setup(module, tmp_path)
  b.events = (event(road_geometry=[[1, 2], [2, 2]], road_heading_deg=90),)
  tick(u)
  finish(u)
  mono[0] += 60
  b.events = (event(road_geometry=[[2, 2], [1, 2]], road_heading_deg=270),)
  b.complete = False
  tick(u)
  finish(u)
  assert len(u._events) == 2


def test_error_backoff_cap(module, tmp_path):
  u, _, b, _, mono = setup(module, tmp_path)
  b.error = OSError('untrusted server text')
  for expected in (30, 60, 120, 240, 300, 300):
    assert tick(u)
    finish(u)
    assert u._next_attempt - mono[0] == expected
    mono[0] = u._next_attempt



def test_real_policy_mobile_variants_count_one_physical_report(module, tmp_path, monkeypatch):
  # The updater fixture installs a narrow policy stub; the shared real-policy
  # fixture must import its actual module even when this file runs alone.
  monkeypatch.delitem(__import__('sys').modules, 'openpilot.sunnypilot.mapd.yandex_policy', raising=False)
  from test_yandex_policy import event as native_event
  spec = importlib.util.spec_from_file_location('_yandex_real_count_policy', Path(__file__).resolve().parents[2] /
                                               'sunnypilot/mapd/yandex_policy.py')
  policy = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(policy)
  monkeypatch.setitem(__import__('sys').modules, 'openpilot.sunnypilot.mapd.yandex_policy', policy)
  module.events_to_cameras = policy.events_to_cameras
  u, p, b, _, _ = setup(module, tmp_path)
  forward = native_event('mobile', 'mobile_control')
  forward.update(observed_at=1000, expires_at=1180)
  reverse = dict(forward, road_geometry=list(reversed(forward['road_geometry'])), road_heading_deg=180.)
  b.events = (forward, reverse)
  assert tick(u)
  finish(u)
  cameras = u.snapshot()
  assert len(cameras) == 3  # one physical warning plus two directed control records
  assert {c.source_params['source_id'] for c in cameras} == {forward['source_id']}
  assert p.values['SpeedCameraYandexCount'] == '1'
  assert p.values['SpeedCameraYandexStatus'] == 'ready: 1 warnings; 2 verified limits'



@pytest.mark.parametrize('reenable', [False, True])
def test_snapshot_conversion_cannot_publish_prior_enabled_generation(module, tmp_path, reenable):
  u, p, b, _, _ = setup(module, tmp_path)
  b.events = (event('old'),)
  tick(u)
  finish(u)
  started, release = threading.Event(), threading.Event()
  original = module.events_to_cameras
  def blocked(events, now):
    if threading.current_thread().name == 'snapshot-race':
      started.set()
      assert release.wait(3)
    return original(events, now)
  module.events_to_cameras = blocked
  returned = []
  thread = threading.Thread(target=lambda: returned.append(u.snapshot()), name='snapshot-race')
  thread.start()
  assert started.wait(2)
  assert not tick(u, enabled=False)
  if reenable:
    b.events = (event('new'), event('second'))
    assert tick(u)
    finish(u)
  release.set()
  thread.join(3)
  assert not thread.is_alive() and returned == [()]
  assert p.values['SpeedCameraYandexCount'] == ('2' if reenable else '0')
  if reenable:
    assert {c.source_id for c in u.snapshot()} == {'new', 'second'}


def replace_synthetic_configuration(root, generation):
  directory = root / 'private'
  directory.mkdir(exist_ok=True)
  temporary = directory / 'replacement.json'
  temporary.write_text('synthetic configuration revision ' + str(generation))
  temporary.replace(directory / 'client.json')


def test_configuration_replacement_resets_backoff_without_clock_advance(module, tmp_path):
  u, p, b, _, mono = setup(module, tmp_path)
  b.error = OSError('synthetic unavailable')
  assert tick(u)
  finish(u)
  assert u._next_attempt > mono[0] and not tick(u)
  replace_synthetic_configuration(tmp_path, 1)
  b.error, b.events = None, (event('replacement'),)
  assert tick(u)
  finish(u)
  assert len(b.calls) == 2 and u._failures == 0
  assert [camera.source_id for camera in u.snapshot()] == ['replacement']
  assert p.values['SpeedCameraYandexError'] == ''
  # An unchanged file does not repeatedly invalidate or bypass the 60s gap.
  assert not tick(u) and len(b.calls) == 2


def test_same_size_atomic_replacement_discards_running_prior_generation(module, tmp_path):
  replace_synthetic_configuration(tmp_path, 1)
  started, release = threading.Event(), threading.Event()

  class Blocking(Backend):
    def fetch_window(self, lat, lon, heading, *, should_continue):
      self.calls.append(should_continue)
      if len(self.calls) == 1:
        started.set()
        assert release.wait(3)
        return NS(events=(event('old'),), complete=True, errors=())
      return NS(events=(event('replacement'),), complete=True, errors=())

  backend = Blocking()
  u, p, _, _, _ = setup(module, tmp_path, backend)
  assert tick(u) and started.wait(2)
  old_worker = u._worker
  replace_synthetic_configuration(tmp_path, 2)  # Equal byte count, different inode.
  assert tick(u)
  finish(u)
  assert not backend.calls[0]()
  assert [camera.source_id for camera in u.snapshot()] == ['replacement']
  deadline = u._next_attempt
  release.set()
  old_worker.join(2)
  assert not old_worker.is_alive()
  assert [camera.source_id for camera in u.snapshot()] == ['replacement']
  assert u._next_attempt == deadline and p.values['SpeedCameraYandexCount'] == '1'


def test_configuration_change_clears_existing_cameras_before_position_recovery(module, tmp_path):
  u, p, b, _, _ = setup(module, tmp_path)
  b.events = (event('old'),)
  assert tick(u)
  finish(u)
  assert len(u.snapshot()) == 1
  replace_synthetic_configuration(tmp_path, 1)
  assert not tick(u, position_valid=False)
  assert u.snapshot() == () and p.values['SpeedCameraYandexCount'] == '0'
  assert p.values['SpeedCameraYandexUpdatedAt'] == '0'
  assert u._point is None and u._heading is None and u._failures == 0
  assert tick(u)
  finish(u)


def test_repeated_key_changes_preserve_two_old_worker_cap(module, tmp_path):
  release = threading.Event()
  started = [threading.Event(), threading.Event()]

  class Blocking(Backend):
    def fetch_window(self, lat, lon, heading, *, should_continue):
      index = len(self.calls)
      self.calls.append(should_continue)
      if index < 2:
        started[index].set()
        assert release.wait(3)
      return NS(events=(event(str(index)),), complete=True, errors=())

  backend = Blocking()
  u, _, _, _, _ = setup(module, tmp_path, backend)
  assert tick(u) and started[0].wait(2)
  replace_synthetic_configuration(tmp_path, 1)
  assert tick(u) and started[1].wait(2)
  replace_synthetic_configuration(tmp_path, 2)
  assert not tick(u) and len(backend.calls) == 2
  assert not any(check() for check in backend.calls)
  assert u.snapshot() == ()
  release.set()
  for worker in u._workers:
    worker.join(2)
    assert not worker.is_alive()
  assert tick(u)
  finish(u)
  assert [camera.source_id for camera in u.snapshot()] == ['2']


def test_missing_file_baseline_and_removal_are_stable(module, tmp_path):
  u, _, _, _, _ = setup(module, tmp_path)
  assert tick(u)
  finish(u)
  generation = u._generation
  assert not tick(u) and u._generation == generation
  replace_synthetic_configuration(tmp_path, 1)
  assert tick(u)
  finish(u)
  (tmp_path / 'private' / 'client.json').unlink()
  assert tick(u)
  finish(u)
  generation = u._generation
  assert not tick(u) and u._generation == generation


def test_owned_backend_recreated_after_configuration_replacement(module, tmp_path, monkeypatch):
  instances = []
  native_provider = types.ModuleType('openpilot.sunnypilot.mapd.yandex.native_provider')

  class Provider(Backend):
    def __init__(self, path):
      super().__init__()
      instances.append(self)
      self.events = (event(str(len(instances))),)

  native_provider.NativeYandexProvider = Provider
  monkeypatch.setitem(__import__('sys').modules, native_provider.__name__, native_provider)
  updater = module.YandexCameraUpdater(Params(), tmp_path, clock=lambda: 1000, monotonic=lambda: 10)
  assert tick(updater)
  finish(updater)
  assert len(instances) == 1
  replace_synthetic_configuration(tmp_path, 1)
  assert tick(updater)
  finish(updater)
  assert len(instances) == 2 and updater.backend is instances[1]
  assert [camera.source_id for camera in updater.snapshot()] == ['2']



def context_event(points):
  return event(approach_geometry=points, approach_source='yandex_native_route',
               approach_observed_at=1000, approach_expires_at=1180)


def test_gentle_departure_refreshes_before_time_or_turn_threshold(module, tmp_path):
  u, _, b, _, mono = setup(module, tmp_path)
  b.events = (context_event([[37, 54.99], [37, 55.01]]),)
  tick(u)
  finish(u)
  mono[0] += 4
  assert not tick(u, lon=37.0004, heading=20)
  mono[0] += 1
  assert tick(u, lon=37.0004, heading=20)
  finish(u)
  assert len(b.calls) == 2


def test_following_curve_and_twelve_meter_jitter_do_not_refresh(module, tmp_path):
  u, _, b, _, mono = setup(module, tmp_path)
  # Northern segment bends east by 20 degrees; this is still the witnessed road.
  endpoint = [37 + .001 * math.tan(math.radians(20)) / math.cos(math.radians(55)), 55.001]
  b.events = (context_event([[37, 54.999], [37, 55], endpoint]),)
  tick(u)
  finish(u)
  mono[0] += 5
  assert not tick(u, lat=55.0005, lon=(37 + endpoint[0]) / 2, heading=20)
  assert not tick(u, lat=54.9995, lon=37.0001, heading=0)  # about 6.4m jitter
  assert len(b.calls) == 1


def test_reverse_context_cannot_block_matching_forward_context(module, tmp_path):
  u, _, b, _, mono = setup(module, tmp_path)
  forward = [[37, 54.99], [37, 55.01]]
  b.events = (context_event(list(reversed(forward))), dict(context_event(forward), source_id='forward'))
  tick(u)
  finish(u)
  mono[0] += 5
  assert not tick(u, lat=55.0001, heading=0)
  assert tick(u, lat=55.0001, heading=40)  # heading leaves every compatible context
  finish(u)


def test_context_limits_and_missing_fallback(module, tmp_path):
  u, _, b, _, mono = setup(module, tmp_path)
  b.events = tuple(dict(context_event([[37+i*.001, 54.99], [37+i*.001, 55.01]]), source_id=str(i)) for i in range(10))
  tick(u)
  finish(u)
  assert len(u._approach_contexts) == 4
  u._approach_contexts = ()
  mono[0] += 5
  assert not tick(u, lon=37.0004, heading=20)
