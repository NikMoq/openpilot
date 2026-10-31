"""Offline camera lifecycle and GPS regression tests; no road/network fixtures.

The obsolete topology-only tests were removed with the discarded graph source.
Coordinates/direction alone do not prove which parallel road a vehicle occupies.
"""
import math
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
for name, relative in [('openpilot', '.'), ('openpilot.sunnypilot', 'sunnypilot'), ('openpilot.sunnypilot.mapd', 'sunnypilot/mapd')]:
  if name not in sys.modules:
    module = ModuleType(name)
    module.__path__ = [str(ROOT / relative)]
    sys.modules[name] = module

from openpilot.sunnypilot.mapd.camera_speed_source import CameraSpeedSource, camera_identity, camera_gps_reliable
from openpilot.sunnypilot.mapd.speed_cameras import SpeedCamera


def point(north, east=0):
  return 55 + math.degrees(north / 6371000), 37 + math.degrees(east / (6371000 * math.cos(math.radians(55))))


def camera(meters, identity='a', **kwargs):
  values = dict(speed_limit_kph=60, direction_deg=0, direction_type=1,
                camera_type='speedcamonline:1', source_id=identity)
  values.update(kwargs)
  return SpeedCamera(*point(meters), **values)


def update(source, cameras, north, heading=0., **kwargs):
  source.test_time = getattr(source, 'test_time', 1000.) + 1
  return source.update(cameras, *point(north), heading, gps_valid=True,
                       fix_time_s=source.test_time, now_s=source.test_time, **kwargs)


def acquire(source, cameras, north=50, heading=0., **kwargs):
  first = update(source, cameras, north, heading, **kwargs)
  assert not first.valid  # A single GPS sample never confirms an approach.
  second = update(source, cameras, north + (10 if heading == 0 else -10), heading, **kwargs)
  assert second.valid
  return second


def test_reaction_pass_two_equal_limits_and_gps_loss():
  cameras = [camera(400), camera(800, 'b')]
  source = CameraSpeedSource()
  first = acquire(source, cameras)
  assert first.state == 'APPROACHING' and first.distance_m == pytest.approx(340, abs=.1)
  assert update(source, cameras, 110).state == 'ACTIVE'
  lost = source.update(cameras, 0., 0., 0., gps_valid=False)
  assert lost.camera_id == first.camera_id and not lost.valid and not lost.pass_proven
  recovered = update(source, cameras, 120)
  assert not recovered.pass_proven
  for north in (160, 200, 240, 280, 320, 360, 390, 430):
    assert not update(source, cameras, north).pass_proven
  passed = update(source, cameras, 440)
  assert passed.state == 'PASSED' and passed.pass_proven and passed.camera_id == first.camera_id
  update(source, cameras, 450)
  second = update(source, cameras, 460)
  assert second.valid and second.camera_id == camera_identity(cameras[1])


def test_unknown_limit_and_opposing_camera_never_acquired():
  invalid = camera(400, speed_limit_kph=None)
  opposing = camera(400, direction_deg=180)
  source = CameraSpeedSource()
  assert not update(source, [invalid, opposing], 100).valid
  assert not update(source, [invalid, opposing], 110).valid


def test_active_latches_across_threshold_jitter_and_invalid_setting_defaults():
  source = CameraSpeedSource()
  acquire(source, [camera(400)], north=110)
  assert update(source, [camera(400)], 100).state == 'ACTIVE'
  source = CameraSpeedSource()
  assert acquire(source, [camera(400)], reaction_distance_m=49).state == 'APPROACHING'


def test_duplicate_stale_future_backward_and_jump_fixes():
  source = CameraSpeedSource()
  cameras = [camera(400)]
  acquire(source, cameras, north=350)
  first = source.update(cameras, *point(430), 0., gps_valid=True, fix_time_s=1003., now_s=1003.)
  assert not first.pass_proven
  repeated = source.update(cameras, *point(440), 0., gps_valid=True, fix_time_s=1003., now_s=1003.)
  assert not repeated.pass_proven
  for fix, now in [(1002., 1003.), (1004., 1003.), (1004., 1007.)]:
    result = source.update(cameras, *point(450), 0., gps_valid=True, fix_time_s=fix, now_s=now)
    assert not result.valid and not result.pass_proven
  source = CameraSpeedSource()
  acquire(source, cameras, north=100)
  jump = update(source, cameras, 430)
  assert jump.reason == 'position_jump' and not jump.pass_proven


@pytest.mark.parametrize('direction_type,second', [(2, None), (1, 180)])
def test_supported_reverse_direction_retained(direction_type, second):
  cam = camera(400, direction_type=direction_type, second_direction_deg=second)
  source = CameraSpeedSource()
  selected = acquire(source, [cam], north=600, heading=180.)
  assert selected.distance_m == pytest.approx(190, abs=.1)
  for north in (560, 530, 500, 470, 440, 410, 390, 360):
    result = update(source, [cam], north, 180.)
  passed = update(source, [cam], 350, 180.)
  assert passed.pass_proven


def test_identity_does_not_depend_on_limit_or_user_offset():
  first = camera(400, identity='same')
  corrected = camera(400, identity='same', speed_limit_kph=80)
  other = camera(800, identity='same')
  assert camera_identity(first) == camera_identity(corrected)
  assert camera_identity(first) != camera_identity(other)


@pytest.mark.parametrize('changes', [dict(horizontalAccuracy=31), dict(horizontalAccuracy=0),
  dict(bearingAccuracyDeg=46), dict(speed=.5), dict(unixTimestampMillis=997000),
  dict(unixTimestampMillis=1001000), dict(latitude=91), dict(longitude=float('nan')), dict(hasFix=False)])
def test_raw_gps_quality_is_camera_specific(changes):
  gps = dict(latitude=55., longitude=37., bearingDeg=0., horizontalAccuracy=5.,
             bearingAccuracyDeg=5., speed=10., unixTimestampMillis=1000000, hasFix=True)
  assert camera_gps_reliable(SimpleNamespace(**gps), 1000.)
  gps.update(changes)
  assert not camera_gps_reliable(SimpleNamespace(**gps), 1000.)


@pytest.mark.parametrize('accuracy,bearing',[(25.,35.),(30.,45.)])
def test_raw_gps_quality_accepts_supported_uncertainty_bounds(accuracy,bearing):
  gps = SimpleNamespace(latitude=55.,longitude=37.,bearingDeg=0.,horizontalAccuracy=accuracy,
                        bearingAccuracyDeg=bearing,speed=10.,unixTimestampMillis=1000000,hasFix=True)
  assert camera_gps_reliable(gps,1000.)
