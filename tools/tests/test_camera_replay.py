"""Portable tests: python -m pytest -c tools/tests/camera_replay_pytest.ini tools/tests/test_camera_replay.py -q

Only namespace paths are shimmed; the existing speed_cameras.py executes intact.
No root conftest, vehicle hardware, cereal, or native extensions are needed.
"""

import builtins
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
if 'openpilot' not in sys.modules:
  namespace = ModuleType('openpilot')
  namespace.__path__ = [str(ROOT)]
  sys.modules['openpilot'] = namespace

from openpilot.tools import camera_replay as replay


@pytest.mark.parametrize('scenario', list(replay.build_scenarios().values()), ids=lambda scenario: scenario.name)
def test_scenario_oracles(scenario):
  assert replay.validate_scenario(scenario)


def test_approach_passes_camera_and_clears_all_fields():
  frames = replay.replay_scenario(replay.build_scenarios()['approach'])
  distances = [frame[replay.SERVICE]['speedCameraAheadDistance'] for frame in frames[:-1]]
  assert distances == pytest.approx([800, 300, 100, 25], abs=0.01)
  assert all(a > b for a, b in zip(distances, distances[1:], strict=False))
  assert frames[-1]['valid'] is True  # valid GPS, camera is now behind
  assert frames[-1][replay.SERVICE] == replay.empty_map_data()


@pytest.mark.parametrize(('direction', 'kind', 'heading', 'expected'), [
  (180, 1, 0, False), (180, 2, 0, True), (180, 0, 0, True),
  (60, 1, 0, True), (60.01, 1, 0, False), (1, 1, 359, True),
  (90, 2, 0, False),
])
def test_direction_semantics_and_tolerance(direction, kind, heading, expected):
  frame = replay.resolve_sample((replay._camera(100, direction, kind),), replay._sample(0, 0, heading_deg=heading))
  assert frame[replay.SERVICE]['speedCameraAheadValid'] is expected


def test_nearer_behind_and_opposite_cameras_do_not_mask_valid_ahead():
  frame = replay.validate_scenario(replay.build_scenarios()['direction_selection'])[0]
  assert frame[replay.SERVICE]['speedCameraAheadDistance'] == pytest.approx(300, abs=0.01)
  assert frame[replay.SERVICE]['speedCameraSpeedLimit'] == pytest.approx(80 / 3.6)


def test_stale_fix_is_cleared_even_with_known_camera_then_recovers():
  fresh, boundary, stale, recovered = replay.replay_scenario(replay.build_scenarios()['stale_gps'])
  assert fresh[replay.SERVICE]['speedCameraAheadValid']
  assert boundary['gps_age_s'] == 2 and boundary[replay.SERVICE]['speedCameraAheadValid']
  assert stale['gps_age_s'] == 3 and stale['reason'] == 'gps_stale' and not stale['valid']
  assert stale[replay.SERVICE] == replay.empty_map_data()
  assert recovered['valid'] and recovered[replay.SERVICE]['speedCameraAheadDistance'] == pytest.approx(50, abs=0.01)


@pytest.mark.parametrize(('overrides', 'reason'), [
  ({'fix_time_s': None}, 'gps_missing'), ({'gps_valid': False}, 'gps_invalid'),
  ({'fix_time_s': 0.01}, 'gps_future'), ({'fix_time_s': float('nan')}, 'gps_invalid'),
  ({'latitude': float('nan')}, 'gps_invalid'), ({'longitude': 181}, 'gps_invalid'),
  ({'latitude': -91}, 'gps_invalid'), ({'heading_deg': None}, 'heading_missing'),
  ({'heading_deg': float('inf')}, 'heading_missing'), ({'fix_time_s': -2.001}, 'gps_stale'),
])
def test_unreliable_fix_or_heading_never_reuses_camera(overrides, reason):
  sample = replace(replay._sample(0, -100), **overrides)
  frame = replay.resolve_sample((replay._camera(),), sample)
  assert frame['reason'] == reason
  assert frame[replay.SERVICE] == replay.empty_map_data()
  json.dumps(frame, allow_nan=False)


def test_current_pure_helpers_are_used(monkeypatch):
  calls = []
  original = replay._cameras.find_nearest_camera

  def record(*args, **kwargs):
    calls.append(kwargs)
    return original(*args, **kwargs)

  monkeypatch.setattr(replay._cameras, 'find_nearest_camera', record)
  scenario = replay.build_scenarios()['stale_gps']
  replay.validate_scenario(scenario)
  assert Path(replay._cameras.__file__).resolve() == ROOT / 'sunnypilot/mapd/speed_cameras.py'
  assert len(calls) == 3  # stale frame must not consult the resolver
  assert all(call['max_distance_m'] == 5_000 for call in calls)


def test_no_control_speed_limits_are_populated():
  for scenario in replay.build_scenarios().values():
    for frame in replay.validate_scenario(scenario):
      data = frame[replay.SERVICE]
      assert not data['speedLimitValid'] and not data['speedLimitAheadValid']
      assert data['speedLimit'] == data['speedLimitAhead'] == data['speedLimitAheadDistance'] == 0


def test_dryrun_cli_is_deterministic_and_needs_no_package_links():
  command = [sys.executable, '-I', str(ROOT / 'tools/camera_replay.py'), '--scenario', 'all', '--dryrun']
  first = subprocess.run(command, check=True, capture_output=True, text=True, cwd=ROOT.parent)
  second = subprocess.run(command, check=True, capture_output=True, text=True, cwd=ROOT.parent)
  assert first.stdout == second.stdout and first.stderr == second.stderr == ''
  report = json.loads(first.stdout)
  assert report['validation'] == 'passed' and report['mode'] == 'dryrun'
  assert len(report['scenarios']) == 10


def test_dryrun_does_not_import_cereal(monkeypatch, capsys):
  original = builtins.__import__

  def guarded(name, *args, **kwargs):
    assert name != 'cereal' and not name.startswith('cereal.')
    return original(name, *args, **kwargs)

  monkeypatch.setattr(builtins, '__import__', guarded)
  monkeypatch.setattr(replay.importlib, 'import_module', lambda name: pytest.fail(f'Unexpected import: {name}'))
  assert replay.main(['--scenario', 'all']) == 0
  assert json.loads(capsys.readouterr().out)['validation'] == 'passed'


@pytest.mark.parametrize('platform', ['win32', 'darwin'])
def test_publisher_refuses_non_linux_before_cereal_import(monkeypatch, platform):
  monkeypatch.setattr(replay.sys, 'platform', platform)
  monkeypatch.setattr(replay.importlib, 'import_module', lambda name: pytest.fail(f'Unexpected import: {name}'))
  with pytest.raises(RuntimeError, match='requires Linux'):
    replay.publish_scenarios([replay.build_scenarios()['approach']])


class FakeMessaging:
  def __init__(self, fail_send=False, missing_schema=False):
    self.messages = []
    self.channels = []
    self.fail_send = fail_send
    self.missing_schema = missing_schema

  def new_message(self, service):
    assert service == 'liveMapDataSP'
    return SimpleNamespace(valid=False, liveMapDataSP=object() if self.missing_schema else SimpleNamespace())

  def PubMaster(self, channels):
    self.channels.append(channels)
    return self

  def send(self, service, message):
    assert service == 'liveMapDataSP'
    if self.fail_send:
      self.fail_send = False
      raise KeyboardInterrupt
    self.messages.append(message)


def fake_linux(monkeypatch, messaging):
  monkeypatch.setattr(replay.sys, 'platform', 'linux')

  def load(name):
    assert name == 'cereal.messaging'
    return messaging

  monkeypatch.setattr(replay.importlib, 'import_module', load)
  monkeypatch.setattr(replay.time, 'sleep', lambda seconds: None)


def test_publisher_sends_only_camera_service_and_final_clear(monkeypatch):
  messaging = FakeMessaging()
  fake_linux(monkeypatch, messaging)
  scenario = replay.build_scenarios()['stale_gps']
  replay.publish_scenarios([scenario], repeat=2)
  assert messaging.channels == [['liveMapDataSP']]
  assert len(messaging.messages) == 9
  stale = messaging.messages[2]
  assert not stale.valid and vars(stale.liveMapDataSP) == replay.empty_map_data()
  active = messaging.messages[0].liveMapDataSP
  assert active.speedCameraSpeedLimit == pytest.approx(60 / 3.6)
  assert active.speedCameraAheadDistance == pytest.approx(100, abs=0.01)
  assert not messaging.messages[-1].valid
  assert vars(messaging.messages[-1].liveMapDataSP) == replay.empty_map_data()


def test_publisher_clears_after_interruption(monkeypatch):
  messaging = FakeMessaging(fail_send=True)
  fake_linux(monkeypatch, messaging)
  with pytest.raises(KeyboardInterrupt):
    replay.publish_scenarios([replay.build_scenarios()['approach']])
  assert len(messaging.messages) == 1
  assert not messaging.messages[0].valid
  assert vars(messaging.messages[0].liveMapDataSP) == replay.empty_map_data()


def test_publisher_paces_samples_from_scenario_clock(monkeypatch):
  messaging = FakeMessaging()
  fake_linux(monkeypatch, messaging)
  clock = [100.0]
  sent_at = []
  original_send = messaging.send

  def sleep(seconds):
    assert seconds >= 0
    clock[0] += seconds

  def send(service, message):
    sent_at.append(clock[0])
    original_send(service, message)

  monkeypatch.setattr(replay.time, 'monotonic', lambda: clock[0])
  monkeypatch.setattr(replay.time, 'sleep', sleep)
  monkeypatch.setattr(messaging, 'send', send)
  replay.publish_scenarios([replay.build_scenarios()['stale_gps']])
  assert sent_at == [100.0, 102.0, 103.0, 104.0, 105.0]


def test_schema_mismatch_fails_before_opening_socket(monkeypatch):
  messaging = FakeMessaging(missing_schema=True)
  fake_linux(monkeypatch, messaging)
  with pytest.raises(AttributeError):
    replay.publish_scenarios([replay.build_scenarios()['approach']])
  assert messaging.channels == messaging.messages == []


def test_failed_validation_prevents_import_and_publication(monkeypatch):
  monkeypatch.setattr(replay.sys, 'platform', 'linux')
  monkeypatch.setattr(replay.importlib, 'import_module', lambda name: pytest.fail(f'Unexpected import: {name}'))
  scenario = replay.build_scenarios()['opposite_direction']
  broken = replace(scenario, expected=(replay.Expected('camera_ahead', 100, 60),))
  with pytest.raises(ValueError, match='validation failed'):
    replay.publish_scenarios([broken])


@pytest.mark.parametrize('times', [(), (1,), (0, 0), (0, -1), (0, float('nan'))])
def test_bad_timelines_rejected(times):
  scenario = replay.build_scenarios()['approach']
  with pytest.raises(ValueError):
    replay.replay_scenario(replace(scenario, samples=tuple(replay._sample(t, -100) for t in times)))


@pytest.mark.parametrize('args', [
  ['--scenario', 'unknown'], ['--publish', '--dryrun'], ['--repeat', '2'],
  ['--publish', '--repeat', '0'], ['--publish', '--repeat', '101'],
])
def test_bad_cli_options_rejected(args):
  with pytest.raises(SystemExit) as error:
    replay.main(args)
  assert error.value.code == 2
