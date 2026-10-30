"""Offline provider metadata plus measured GPS, no graph or network boundary."""
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
from openpilot.sunnypilot.mapd.speed_cameras import SpeedCamera, CameraSpatialIndex

NOW = 1_800_000_000.


def point(north=0., east=0.):
  return (55 + math.degrees(north / 6371000),
          37 + math.degrees(east / (6371000 * math.cos(math.radians(55)))))


def camera(north=900., east=0., direction=0., speed=60, source='one', **kwargs):
  return SpeedCamera(*point(north, east), speed_limit_kph=speed, direction_deg=direction,
                     camera_type='speedcamonline:1', direction_type=kwargs.pop('direction_type', 1),
                     source_id=source, **kwargs)


def update(source, records, north, t, *, east=0., heading=0., valid=True, reaction=900, override=''):
  return source.update(records, *point(north, east), heading, gps_valid=valid,
                       fix_time_s=NOW+t, now_s=NOW+t, reaction_distance_m=reaction, driver_override_id=override)


def test_local_cache_only_selects_actual_limit_at_user900_without_graph():
  item = camera()
  source = CameraSpeedSource()
  first = update(source, CameraSpatialIndex([item]), 0, 0)
  assert not first.valid and not first.camera_id and first.reason == 'confirming_approach'
  second = update(source, CameraSpatialIndex([item]), 10, 1)
  assert second.valid and second.state == 'ACTIVE' and second.camera == item
  assert second.distance_m == pytest.approx(890, abs=.01) and second.camera.speed_limit_kph == 60


def test_cityguide_metadata_range_does_not_cap_user_reaction_distance():
  item = SpeedCamera(*point(900), speed_limit_kph=60, direction_deg=0, camera_type='speedcamonline-cg77:18059',
                     direction_type=1, source_id='cg', source_params={'1705':'180', '1706':'200', '1709':'60', '1713':'1'})
  assert item.allowed_auto_control
  source = CameraSpeedSource()
  update(source, [item], 0, 0)
  selected = update(source, [item], 10, 1)
  assert selected.valid and selected.state == 'ACTIVE' and selected.distance_m > 200


@pytest.mark.parametrize('item', [camera(direction=180), camera(east=60), camera(east=-60),
                                camera(direction=None), camera(speed=None), camera(direction_type=None)])
def test_opposing_lateral_unknown_direction_and_unknown_speed_do_not_acquire(item):
  source = CameraSpeedSource()
  update(source, [item], 0, 0)
  assert not update(source, [item], 10, 1).valid


def test_lateral_motion_and_radial_closeness_alone_do_not_acquire():
  source = CameraSpeedSource()
  item = camera(north=100)
  update(source, [item], 0, 0)
  assert not update(source, [item], 0, 1, east=10).valid


def test_slow_forward_progress_accumulates_without_repeated_fix_activation():
  source = CameraSpeedSource()
  item = camera()
  update(source, [item], 0, 0)
  assert not update(source, [item], 1, 1).valid
  assert not update(source, [item], 2, 1).valid  # Duplicate timestamp cannot count.
  assert update(source, [item], 3, 2).valid


def test_active_latches_reaction_jitter_and_raw20_has_no_early_minimum_filter():
  source = CameraSpeedSource()
  item = camera(north=305, speed=20)
  update(source, [item], 0, 0, reaction=300)
  selected = update(source, [item], 10, 1, reaction=300)
  assert selected.valid and selected.state == 'ACTIVE' and selected.camera.speed_limit_kph == 20
  jitter = update(source, [item], 2, 2, reaction=300)
  assert jitter.valid and jitter.state == 'ACTIVE' and jitter.camera_id == selected.camera_id


def test_two_behind_fixes_require_actual_observed_crossing_and_duplicate_cannot_pass():
  source = CameraSpeedSource()
  item = camera(north=100)
  update(source, [item], 0, 0)
  chosen = update(source, [item], 10, 1)
  assert update(source, [item], 80, 3).valid
  first = update(source, [item], 130, 4)
  assert not first.pass_proven
  assert not update(source, [item], 140, 4).pass_proven
  passed = update(source, [item], 150, 5)
  assert passed.pass_proven and passed.state == 'PASSED' and passed.camera_id == chosen.camera_id
  assert not update(source, [item], 160, 6).valid


def test_gps_gap_cannot_invent_crossing_or_release_retained_camera():
  source = CameraSpeedSource()
  item = camera(north=100)
  update(source, [item], 0, 0)
  chosen = update(source, [item], 10, 1)
  lost = update(source, [item], 80, 3, valid=False)
  assert lost.camera_id == chosen.camera_id and not lost.valid
  assert not update(source, [item], 130, 4).pass_proven
  assert not update(source, [item], 160, 5).pass_proven


def test_jump_and_backwards_fix_do_not_establish_passage():
  source = CameraSpeedSource()
  item = camera(north=100)
  update(source, [item], 0, 0)
  chosen = update(source, [item], 10, 1)
  jumped = update(source, [item], 300, 2)
  assert not jumped.valid and jumped.reason == 'position_jump' and jumped.camera_id == chosen.camera_id
  backwards = update(source, [item], 320, 1)
  assert not backwards.valid and not backwards.pass_proven


def test_second_explicit_bearing_and_bidirectional_metadata_work():
  for item in (camera(direction=180, second_direction_deg=0), camera(direction=180, direction_type=2)):
    source = CameraSpeedSource()
    update(source, [item], 0, 0)
    assert update(source, [item], 10, 1).valid


def test_manual_override_is_bound_to_same_event():
  source = CameraSpeedSource()
  item = camera()
  update(source, [item], 0, 0)
  chosen = update(source, [item], 10, 1)
  overridden = update(source, [item], 20, 2, override=chosen.camera_id)
  assert overridden.valid and overridden.state == 'DRIVER_OVERRIDE'
  assert update(source, [item], 30, 3).state == 'DRIVER_OVERRIDE'


def test_camera_identity_ignores_speed_and_preserves_distinct_same_limit_points():
  assert camera_identity(camera(speed=60)) == camera_identity(camera(speed=70))
  assert camera_identity(camera(north=900)) != camera_identity(camera(north=950))


def test_gps_contract_uses_fresh_raw_accuracy_course_and_speed():
  gps = SimpleNamespace(latitude=55., longitude=37., bearingDeg=0., horizontalAccuracy=3.,
                        bearingAccuracyDeg=2., speed=20., unixTimestampMillis=NOW*1000, hasFix=True)
  assert camera_gps_reliable(gps, NOW)
  for name, value in [('horizontalAccuracy', 30), ('bearingAccuracyDeg', 30), ('speed', 0), ('hasFix', False)]:
    changed = SimpleNamespace(**vars(gps))
    setattr(changed, name, value)
    assert not camera_gps_reliable(changed, NOW)
  assert not camera_gps_reliable(gps, NOW+3)


def test_explicit_all_direction_camera_tolerates_small_course_jitter():
  source = CameraSpeedSource()
  item = camera(direction_type=0)
  update(source, [item], 0, 0)
  assert update(source, [item], 10, 1, heading=.1).valid


@pytest.mark.parametrize('heading', [2., 5., 15., 358., 355., 345.])
def test_accepted_gps_course_uncertainty_does_not_delay900m_reaction(heading):
  source = CameraSpeedSource()
  item = camera(north=920)
  update(source, [item], 0, 0, heading=heading)
  selected = update(source, [item], 20, 1, heading=heading)
  assert selected.valid and selected.state == 'ACTIVE'
  assert selected.distance_m == pytest.approx(900, abs=.01)


def test_slow_one_meter_fixes_can_prove_passage_without_being_stuck_in_hold():
  source = CameraSpeedSource()
  item = camera(north=10)
  update(source, [item], 0, 0)
  assert update(source, [item], 3, 1).valid
  update(source, [item], 9.5, 4)
  update(source, [item], 10.5, 5)
  passed = None
  for step in range(1, 24):
    selected = update(source, [item], 10.5 + step, 5 + step)
    if selected.pass_proven:
      passed = selected
      break
  assert passed is not None and passed.state == 'PASSED'
