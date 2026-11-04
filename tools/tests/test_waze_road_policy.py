"""Directed Waze road limits control both approaches to a police report."""
import json
from dataclasses import replace

import pytest

from test_waze_policy import event, NOW
from openpilot.sunnypilot.mapd.speed_cameras import find_nearest_control_camera
from openpilot.sunnypilot.mapd.waze_policy import events_to_cameras, filter_fresh_waze


def road_event(**overrides):
  values = dict(source_kind='mobile', source_id='waze:rt:police', raw_type='POLICE', raw_subtype='POLICE_WITH_MOBILE_CAMERA',
                heading_raw=270, speed_raw=None, road_match_status='verified', road_limit_source='waze_practice_features',
                road_segment_id=123, road_from_node_id=1, road_to_node_id=2,
                road_geometry=[[37., 54.999], [37., 55.001]], road_match_distance_m=0.,
                road_direction_coverage='both', road_observed_at_ms=int(NOW*1000), road_limit_expires_at_ms=int((NOW+300)*1000),
                road_limit_variants=[dict(travel_direction='forward', limit_kph=50., heading_deg=0., direction_basis='per_direction_road_limit'),
                                     dict(travel_direction='reverse', limit_kph=70., heading_deg=180., direction_basis='per_direction_road_limit')])
  values.update(overrides)
  return event(**values)


def test_mobile_report_expands_both_road_approaches_with_respective_limits():
  all_cameras = events_to_cameras([road_event()], NOW)
  warning, = [c for c in all_cameras if not c.allowed_auto_control]
  assert warning.direction_deg is None and warning.direction_type is None
  cameras = [c for c in all_cameras if c.allowed_auto_control]
  assert len(cameras) == 2
  assert [(c.direction_deg, c.speed_limit_kph) for c in cameras] == [(0., 50.), (180., 70.)]
  assert all(c.allowed_auto_control for c in cameras)
  assert len({c.source_id for c in cameras}) == 2
  assert all(json.loads(c.source_params['waze_event'])['heading_raw'] == 270 for c in cameras)


def test_equal_road_limits_apply_both_sides_without_claiming_camera_facing():
  variants = [dict(travel_direction='both', limit_kph=50., heading_deg=None, direction_basis='equal_limits_both_directions')]
  all_cameras = events_to_cameras([road_event(road_limit_variants=variants)], NOW)
  camera, = [c for c in all_cameras if c.allowed_auto_control]
  assert camera.allowed_auto_control and camera.direction_type == 0 and camera.direction_deg is None
  for heading in (0, 180):
    match = find_nearest_control_camera([camera], 54.9999 if heading == 0 else 55.0001, 37., minimum_speed_kph=1, heading_deg=heading)
    assert match is not None


def test_road_limit_expiry_demotes_fresh_report_to_warning_without_renewing_report():
  raw = road_event()
  original = events_to_cameras([raw], NOW)
  assert sum(c.allowed_auto_control for c in original) == 2
  # Fixed report stays fresh long enough to isolate the independent road expiry.
  raw.update(source_kind='fixed', raw_type='CAMERA', raw_subtype='SPEED_CAMERA')
  original = events_to_cameras([raw], NOW)
  expired, = events_to_cameras([raw], NOW+300)
  assert expired.speed_limit_kph is None and not expired.allowed_auto_control
  assert filter_fresh_waze(original, NOW+300) == []
  assert json.loads(expired.source_params['waze_event'])['observed_at_ms'] == int(NOW*1000)


@pytest.mark.parametrize('change', [dict(road_match_status='ambiguous'), dict(road_match_distance_m=13),
    dict(road_direction_coverage='forward'), dict(road_limit_expires_at_ms=int((NOW+301)*1000)),
    dict(road_segment_id=True), dict(road_limit_source='osm'), dict(road_geometry=[])])
def test_invalid_road_evidence_stays_warning_only(change):
  cameras = events_to_cameras([road_event(**change)], NOW)
  assert len(cameras) == 1 and not cameras[0].allowed_auto_control


def test_camera_fields_cannot_disagree_with_attached_road_evidence():
  camera = next(c for c in events_to_cameras([road_event()], NOW) if c.allowed_auto_control)
  for changed in (replace(camera, speed_limit_kph=60), replace(camera, direction_deg=180), replace(camera, source_id='waze:wrong'),
                  replace(camera, direction_type=False)):
    assert not changed.allowed_auto_control



def explicit_camera_event(**changes):
  values = dict(source_kind='fixed', source_id='waze:fixed:synthetic', raw_type='CAMERA', raw_subtype='SPEED_CAMERA',
                road_match_basis='explicit_camera_segment', road_camera_global_id=356390, road_camera_segment_id=123,
                road_camera_direction='FWD', road_camera_types=['SPEED', 'RED_LIGHT'], road_camera_coordinates=[37., 55.],
                road_camera_observed_at_ms=int(NOW*1000), road_camera_expires_at_ms=int((NOW+300)*1000),
                road_direction_coverage='forward',
                road_limit_variants=[dict(travel_direction='forward', limit_kph=50., heading_deg=0., direction_basis='explicit_camera_direction')])
  values.update(changes)
  return road_event(**values)


def test_explicit_camera_association_keeps_road_limit_separate_from_raw_threshold():
  camera, = events_to_cameras([explicit_camera_event()], NOW)
  assert camera.allowed_auto_control and camera.speed_limit_kph == 50 and camera.direction_deg == 0
  assert json.loads(camera.source_params['waze_event'])['speed_raw'] is None


@pytest.mark.parametrize('changes', [dict(road_camera_global_id=True), dict(road_camera_segment_id=456),
    dict(road_camera_direction='REV'), dict(road_camera_types=['DUMMY']), dict(road_camera_coordinates=[37.001,55.]),
    dict(road_camera_expires_at_ms=int((NOW+2)*1000)), dict(road_camera_observed_at_ms=int((NOW+1)*1000)),
    dict(road_match_basis='unique_geometry')])
def test_inconsistent_explicit_camera_link_does_not_authorize_control(changes):
  cameras = events_to_cameras([explicit_camera_event(**changes)], NOW)
  assert len(cameras) == 1 and not cameras[0].allowed_auto_control


@pytest.mark.parametrize('status', ['verified_approach', 'unknown', None])
def test_mobile_warning_never_filters_approach_using_report_facing(status):
  raw = road_event(road_match_status='unknown', direction_status=status, heading_raw=180)
  warning, = events_to_cameras([raw], NOW)
  assert warning.direction_deg is None and warning.direction_type is None and not warning.allowed_auto_control


def test_mobile_opposite_carriageway_uses_its_own_limit_and_projected_control_point():
  from test_waze_road_match import corridor_roads, event as mobile_event, NOW as observed_ms
  from openpilot.sunnypilot.mapd.waze.road_match import annotate_mobile_corridor
  raw = mobile_event()
  matched = annotate_mobile_corridor(raw, corridor_roads(), observed_ms)
  assert len(matched) == 2
  cameras = events_to_cameras(matched, observed_ms/1000)
  warnings = [c for c in cameras if not c.allowed_auto_control]
  controls = [c for c in cameras if c.allowed_auto_control]
  assert len(warnings) == 1 and len(controls) == 2
  assert warnings[0].longitude == raw['longitude'] and warnings[0].direction_deg is None
  assert sorted(c.speed_limit_kph for c in controls) == [50., 70.]
  opposite = next(c for c in controls if c.speed_limit_kph == 70)
  evidence = json.loads(opposite.source_params['waze_event'])
  assert opposite.longitude != raw['longitude']
  assert evidence['longitude'] == raw['longitude'] and evidence['heading_raw'] == raw['heading_raw']
  assert evidence['road_match_basis'] == 'mobile_parallel_carriageway'


def test_unproven_parallel_target_cannot_supply_control_coordinates():
  from test_waze_road_match import corridor_roads, event as mobile_event, NOW as observed_ms
  from openpilot.sunnypilot.mapd.waze.road_match import annotate_mobile_corridor
  opposite = annotate_mobile_corridor(mobile_event(), corridor_roads(), observed_ms)[1]
  for changes in (dict(control_longitude=37.), dict(road_primary_street_id=True), dict(road_match_basis='unique_geometry')):
    invalid = dict(opposite, **changes)
    cameras = events_to_cameras([invalid], observed_ms/1000)
    assert len(cameras) == 1 and not cameras[0].allowed_auto_control
    assert cameras[0].longitude == invalid['longitude']
