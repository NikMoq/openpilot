"""Synthetic Yandex policy checks; no network or native runtime."""
import copy
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
for name, relative in [('openpilot', '.'), ('openpilot.sunnypilot', 'sunnypilot'), ('openpilot.sunnypilot.mapd', 'sunnypilot/mapd')]:
  if name not in sys.modules:
    module = ModuleType(name)
    module.__path__ = [str(ROOT / relative)]
    sys.modules[name] = module

from openpilot.sunnypilot.mapd.speed_cameras import SpeedCamera
from openpilot.sunnypilot.mapd.yandex_policy import events_to_cameras, filter_fresh_yandex, merge_yandex_sources, yandex_control_evidence

NOW = 1800000000


def event(kind='fixed', tag='speed_control'):
  return dict(source='yandex', source_kind=kind, source_id='yandex:native:synthetic',
              source_sdk='4.45.0-full', tags=[tag], observed_at=NOW, expires_at=NOW+(180 if kind == 'mobile' else 300),
              ttl_basis='local_policy_cap', longitude=38., latitude=45.0005,
              road_geometry=[[38., 45.], [38., 45.001]], road_heading_deg=0., position_index=0, position_fraction=.5,
              route_position_longitude=38., route_position_latitude=45.0005, report_route_distance_m=0.,
              road_limit_raw_mps=16.66666603088379, road_limit_kph=60, road_limit_raw_unit='m/s', road_limit_unit='km/h',
              speed_limit_confidence='native_rle_verified_against_sdk_edges',
              road_limit_direction_basis='acquired_route_forward_edge', auto_control_eligible=False)


def test_fixed_admission_and_expiry():
  camera, = events_to_cameras([event()], NOW)
  assert yandex_control_evidence(camera)
  assert filter_fresh_yandex([camera], NOW+299.999) == [camera]
  assert not filter_fresh_yandex([camera], NOW+300)
  assert camera.direction_type == 1 and camera.direction_deg == 0


@pytest.mark.parametrize('tag', ['mobile_control', 'police_patrol'])
@pytest.mark.parametrize('facing', ['in_face', 'in_back'])
def test_mobile_warning_all_approaches_and_directed_control(tag, facing):
  record = event('mobile', tag)
  record[facing] = True
  warning, control = events_to_cameras([record], NOW)
  assert warning.direction_deg is None and warning.direction_type is None and warning.speed_limit_kph is None
  assert not yandex_control_evidence(warning) and yandex_control_evidence(control)
  assert not filter_fresh_yandex([warning, control], NOW+180)


@pytest.mark.parametrize('key,value', [('road_limit_kph', None), ('road_limit_raw_unit', 'km/h'),
                                      ('road_limit_raw_mps', 60), ('speed_limit_confidence', 'guessed'),
                                      ('source_sdk', 'unknown'), ('report_route_distance_m', 31),
                                      ('road_heading_deg', 180), ('position_fraction', .1),
                                      ('road_limit_direction_basis', 'matched_mobile_parallel_route')])
def test_incomplete_or_mismatched_evidence_is_warning_only(key, value):
  record = event()
  record[key] = value
  cameras = events_to_cameras([record], NOW)
  assert cameras and not any(yandex_control_evidence(c) for c in cameras)
  assert all(c.speed_limit_kph is None for c in cameras)


@pytest.mark.parametrize('field,value', [('latitude', 46), ('longitude', 39), ('speed_limit_kph', 70),
                                        ('direction_deg', 180), ('direction_type', 0), ('source_id', 'yandex:fake'),
                                        ('camera_type', 'yandex:mobile:mobile_control'), ('end_latitude', 45)])
def test_camera_field_tampering_rejected(field, value):
  camera, = events_to_cameras([event()], NOW)
  tampered = replace(camera, **{field: value})
  assert not yandex_control_evidence(tampered)
  assert not filter_fresh_yandex([tampered], NOW)


def test_serialized_evidence_tampering_and_size_cap():
  camera, = events_to_cameras([event()], NOW)
  record = json.loads(camera.source_params['yandex_event'])
  record['longitude'] += .01
  tampered = replace(camera, source_params={'yandex_event': json.dumps(record)})
  assert not yandex_control_evidence(tampered)
  record = event()
  record['extra'] = 'x' * 16384
  assert not events_to_cameras([record], NOW)


def test_generic_police_and_non_camera_tags():
  police, = events_to_cameras([event('warning', 'police')], NOW)
  assert police.camera_type == 'yandex:warning:police'
  assert police.speed_limit_kph is None and not yandex_control_evidence(police)
  for tag in ('school', 'accident', 'traffic_control'):
    assert not events_to_cameras([event('warning', tag)], NOW)


def test_opposite_edges_have_distinct_ids_and_merge_preserves_authority():
  forward = event()
  reverse = copy.deepcopy(forward)
  reverse['road_geometry'].reverse()
  reverse['road_heading_deg'] = 180.
  cameras = events_to_cameras([forward, reverse], NOW)
  assert len(cameras) == 2 and all(yandex_control_evidence(c) for c in cameras)
  primary = SpeedCamera(45.0005, 38., 50., camera_type='speedcamonline:1', source_id='russian')
  merged = merge_yandex_sources([primary], cameras, NOW)
  assert merged[0] is primary and len(merged) == 3
  assert merge_yandex_sources([primary], cameras, NOW+300) == [primary]


def test_unknown_nearby_warning_cannot_replace_qualified_base():
  primary = SpeedCamera(45.0005, 38., 50., camera_type='speedcamonline:1', source_id='russian')
  warnings = events_to_cameras([event('warning', 'police')], NOW)
  assert merge_yandex_sources([primary], warnings, NOW) == [primary]



def parallel_event():
  value = event('mobile', 'mobile_control')
  value.update(road_limit_direction_basis='matched_mobile_parallel_route', match_unique=True,
               original_road_geometry=[[38., 45.001], [38., 45.]], original_road_heading_deg=180.,
               original_route_position_longitude=38., original_route_position_latitude=45.0005,
               original_position_fraction=.5, original_report_route_distance_m=0.,
               original_road_limit_kph=90)
  return value


@pytest.mark.parametrize('facing', ['in_face', 'in_back'])
def test_parallel_mobile_uses_current_forward_60_not_original_reverse_90(facing):
  record = parallel_event()
  record[facing] = True
  warning, control = events_to_cameras([record], NOW)
  assert warning.speed_limit_kph is None
  assert control.speed_limit_kph == 60 and control.direction_deg == 0
  assert yandex_control_evidence(control) and control.allowed_auto_control


@pytest.mark.parametrize('key,value', [('match_unique', False), ('match_unique', 1),
                                      ('original_road_heading_deg', 90), ('original_position_fraction', .1),
                                      ('original_report_route_distance_m', 31),
                                      ('original_route_position_latitude', 46),
                                      ('original_road_geometry', [[38, 45], [38, 45]]),
                                      ('road_geometry', [[38, 45], [38, 45]]), ('source_kind', 'fixed')])
def test_parallel_provenance_tampering_is_warning_only(key, value):
  record = parallel_event()
  record[key] = value
  assert not any(yandex_control_evidence(c) for c in events_to_cameras([record], NOW))


def test_parallel_perpendicular_geometry_rejected_even_with_matching_bearing():
  record = parallel_event()
  record.update(original_road_geometry=[[37.9995, 45.0005], [38.0005, 45.0005]], original_road_heading_deg=90.)
  assert not any(yandex_control_evidence(c) for c in events_to_cameras([record], NOW))


def test_parallel_over_40m_gap_rejected_with_honest_distance():
  from openpilot.sunnypilot.mapd.yandex.native_snapshot import _distance
  record = parallel_event()
  record['road_geometry'] = [[38.001, 45.], [38.001, 45.001]]
  record['route_position_longitude'] = 38.001
  record['report_route_distance_m'] = _distance((38., 45.0005), (38.001, 45.0005))
  assert record['report_route_distance_m'] > 40
  assert not any(yandex_control_evidence(c) for c in events_to_cameras([record], NOW))



def test_parallel_nearby_distinct_carriageway_and_original_distance_gate():
  from openpilot.sunnypilot.mapd.yandex.native_snapshot import _distance
  record = parallel_event()
  delta = .00025  # about 20m east at this latitude
  record['road_geometry'] = [[38. + delta, 45.], [38. + delta, 45.001]]
  record['route_position_longitude'] = 38. + delta
  record['report_route_distance_m'] = _distance((38., 45.0005), (38. + delta, 45.0005))
  assert 15 < record['report_route_distance_m'] < 30
  assert any(yandex_control_evidence(c) for c in events_to_cameras([record], NOW))
  # Even a physically close new road cannot rescue an unrelated original route.
  record['original_road_geometry'] = [[37.999, 45.001], [37.999, 45.]]
  record['original_route_position_longitude'] = 37.999
  record['original_report_route_distance_m'] = _distance((38., 45.0005), (37.999, 45.0005))
  assert not any(yandex_control_evidence(c) for c in events_to_cameras([record], NOW))
