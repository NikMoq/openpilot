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


def test_legacy_manual_override_input_cannot_latch_source_authority():
  source = CameraSpeedSource()
  item = camera()
  update(source, [item], 0, 0)
  chosen = update(source, [item], 10, 1)
  overridden = update(source, [item], 20, 2, override=chosen.camera_id)
  assert overridden.valid and overridden.state == 'ACTIVE'
  assert update(source, [item], 30, 3).state == 'ACTIVE'


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


# Production-style route evidence: exact geometry, not a fake passed flag.
def local_road(*, parallel=False):
  from openpilot.sunnypilot.mapd.camera_osm_bridge import OfflineRoadSnapshot, RoadEdge
  from openpilot.sunnypilot.mapd.camera_speed_source import xy
  a, b = point(-100), point(1200)
  edges = [RoadEdge('road:0', 'road', a, b, math.hypot(*xy(b, a)))]
  if parallel:
    c, d = point(-100, 5), point(1200, 5)
    edges.append(RoadEdge('parallel:0', 'parallel', c, d, math.hypot(*xy(d, c))))
  return OfflineRoadSnapshot(edges, [(54.9, 36.9, 55.1, 37.1)], point())


def route_update(source, records, north, t, *, roads=None, heading=0., valid=True):
  return source.update(records, *point(north), heading, gps_valid=valid,
                       fix_time_s=NOW+t, now_s=NOW+t, reaction_distance_m=900,
                       road_graph=roads)


def test_recovered_route_proves_missed_crossing_after_two_progressing_behind_fixes():
  source, roads = CameraSpeedSource(), local_road()
  records = [camera(100), camera(500, source='next-same-limit')]
  route_update(source, records, 0, 0, roads=roads)
  first = route_update(source, records, 10, 1, roads=roads)
  assert first.valid
  gap = route_update(source, records, 80, 3, roads=roads, valid=False)
  assert not gap.valid and gap.camera_id == first.camera_id
  recovered = route_update(source, records, 130, 4, roads=roads)
  assert not recovered.pass_proven and recovered.camera_id == first.camera_id
  # Duplicate timestamps and stationary fresh samples cannot create progress.
  assert not route_update(source, records, 140, 4, roads=roads).pass_proven
  passed = route_update(source, records, 140, 5, roads=roads)
  assert passed.pass_proven and passed.state == 'PASSED' and passed.camera_id == first.camera_id
  confirming = route_update(source, records, 150, 6, roads=roads)
  assert not confirming.valid
  next_camera = route_update(source, records, 160, 7, roads=roads)
  assert next_camera.valid and next_camera.camera_id != first.camera_id
  assert next_camera.camera == records[1]


@pytest.mark.parametrize('gap_kind', ['gps', 'ambiguous_geometry'])
def test_recovered_route_does_not_need_an_observed_zero_after_short_gap(gap_kind):
  source, roads = CameraSpeedSource(), local_road()
  records = [camera(100)]
  route_update(source, records, 0, 0, roads=roads)
  chosen = route_update(source, records, 10, 1, roads=roads)
  bad_roads = local_road(parallel=True) if gap_kind == 'ambiguous_geometry' else roads
  gap = route_update(source, records, 80, 3, roads=bad_roads, valid=gap_kind != 'gps')
  assert not gap.valid and gap.camera_id == chosen.camera_id
  assert not route_update(source, records, 130, 4, roads=roads).pass_proven
  assert route_update(source, records, 140, 5, roads=roads).pass_proven


def test_recovery_stationary_samples_and_receding_motion_cannot_prove_passage():
  source, roads = CameraSpeedSource(), local_road()
  records = [camera(100)]
  route_update(source, records, 0, 0, roads=roads)
  chosen = route_update(source, records, 10, 1, roads=roads)
  route_update(source, records, 80, 3, roads=roads, valid=False)
  for t in range(4, 10):
    state = route_update(source, records, 150, t, roads=roads)
    assert not state.pass_proven and state.camera_id == chosen.camera_id
  assert not route_update(source, records, 140, 10, roads=roads).pass_proven
  assert not route_update(source, records, 145, 11, roads=roads).pass_proven
  assert route_update(source, records, 155, 12, roads=roads).pass_proven


@pytest.mark.parametrize('failure', ['gps', 'ambiguous', 'opposing'])
def test_recovered_behind_positions_still_need_reliable_unique_forward_road(failure):
  source, roads = CameraSpeedSource(), local_road()
  records = [camera(100)]
  route_update(source, records, 0, 0, roads=roads)
  chosen = route_update(source, records, 10, 1, roads=roads)
  route_update(source, records, 80, 3, roads=roads, valid=False)
  for t, north in enumerate((130, 140, 150, 160), 4):
    state = route_update(source, records, north, t,
                         roads=local_road(parallel=True) if failure == 'ambiguous' else roads,
                         valid=failure != 'gps', heading=180 if failure == 'opposing' else 0)
    assert not state.pass_proven and not state.valid and state.camera_id == chosen.camera_id
