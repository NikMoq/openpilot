"""Synthetic native evidence through real map wire, resolver and planner."""
import time
from types import SimpleNamespace as NS

import pytest

from test_camera_control_selection import map_pipeline as map_pipeline, latitude
from test_camera_resolver_integration import control as control
from test_waze_controls_flow import _pipeline, _two_way_offline_geometry
from test_yandex_policy import event
from openpilot.sunnypilot.mapd.yandex_policy import events_to_cameras, filter_fresh_yandex


def record(now, *, mobile=False, reverse=False, limit=60):
  value = event('mobile' if mobile else 'fixed', 'mobile_control' if mobile else 'speed_control')
  geometry = [[37., latitude(-500)], [37., latitude(6500)]]
  if reverse:
    geometry.reverse()
  value.update(observed_at=now, expires_at=now + (180 if mobile else 300), longitude=37., latitude=latitude(250),
               road_geometry=geometry, road_heading_deg=180. if reverse else 0.,
               position_fraction=(6250 / 7000 if reverse else 750 / 7000),
               route_position_longitude=37., route_position_latitude=latitude(250),
               road_limit_kph=limit, road_limit_raw_mps=limit / 3.6)
  return value


@pytest.mark.parametrize('mobile,reverse,limit,facing', [(False, False, 60, 'in_face'),
                                                        (True, False, 50, 'in_back'),
                                                        (True, True, 70, 'in_face')])
def test_qualified_approach_and_proven_pass_restore_100(map_pipeline, control, monkeypatch, mobile, reverse, limit, facing):
  instance, _, _ = map_pipeline
  if reverse:
    _two_way_offline_geometry(instance)
  instance.last_bearing = instance.sm['gpsLocationExternal'].bearingDeg = 180 if reverse else 0
  emit, _ = _pipeline(map_pipeline, control, monkeypatch)
  raw = record(time.time(), mobile=mobile, reverse=reverse, limit=limit)
  raw[facing] = True
  cameras = events_to_cameras([raw], raw['observed_at'])
  assert sum(c.allowed_auto_control for c in cameras) == 1
  positions = (500, 495, 450, 400, 350, 300, 260, 220, 200) if reverse else (0, 5, 50, 100, 150, 200, 240, 280, 300)
  emit(positions[0], cameras)
  before, target, plan = emit(positions[1], cameras)
  assert before.speedCameraControlValid and target == pytest.approx((limit + 15) / 3.6)
  assert plan.speedCameraControl.rawLimit == pytest.approx(limit / 3.6)
  identity = before.speedCameraControlEventId
  for north in positions[2:]:
    after, target, plan = emit(north, cameras)
  assert after.speedCameraControlPassProven and after.speedCameraControlEventId == identity
  assert target == pytest.approx(100 / 3.6) and plan.vTarget == pytest.approx(100 / 3.6)


def test_source_expiry_never_invents_pass(map_pipeline, control, monkeypatch):
  emit, _ = _pipeline(map_pipeline, control, monkeypatch)
  raw = record(time.time())
  raw['expires_at'] = raw['observed_at'] + 2
  cameras = events_to_cameras([raw], raw['observed_at'])
  emit(0, cameras)
  before, target, _ = emit(5, cameras)
  assert before.speedCameraControlValid and target == pytest.approx(75 / 3.6)
  after, target, plan = emit(10, filter_fresh_yandex(cameras, raw['observed_at'] + 3))
  assert not after.speedCameraControlPassProven
  assert after.speedCameraControlEventId == before.speedCameraControlEventId
  assert target == pytest.approx(75 / 3.6) and plan.speedCameraControl.state != 'passed'


def test_unknown_limit_warns_without_control(map_pipeline, control, monkeypatch):
  emit, _ = _pipeline(map_pipeline, control, monkeypatch)
  raw = record(time.time())
  raw['road_limit_kph'] = raw['road_limit_raw_mps'] = None
  cameras = events_to_cameras([raw], raw['observed_at'])
  emit(0, cameras)
  lmd, target, _ = emit(5, cameras)
  assert lmd.speedCameraAheadValid and lmd.speedCameraSpeedLimit == 0
  assert not lmd.speedCameraControlValid and target == pytest.approx(100 / 3.6)


@pytest.mark.parametrize('disabled', [True, False])
def test_mapd_revokes_retained_warning_on_disable_or_expiry(map_pipeline, monkeypatch, disabled):
  instance, config, _ = map_pipeline
  now = time.time()
  raw = record(now)
  raw['road_limit_kph'] = raw['road_limit_raw_mps'] = None
  cameras = tuple(events_to_cameras([raw], now))
  enabled = [True]
  calls = []
  def tick(*args, **kwargs):
    calls.append(kwargs)
    enabled[0] = kwargs['enabled']
  instance.yandex_updater = NS(tick=tick, snapshot=lambda: cameras if enabled[0] else ())
  config['SpeedCameraYandexEnabled'] = '1'
  assert instance.get_speed_camera() is not None
  assert instance.camera_warning_retained is not None
  assert calls[-1]['heading'] == instance.last_bearing and calls[-1]['position_valid']
  if disabled:
    config['SpeedCameraYandexEnabled'] = '0'
  else:
    monkeypatch.setattr(time, 'time', lambda: now + 301)
  instance.get_speed_camera()
  assert instance.camera_warning_retained is None
  assert instance.camera_match is None
