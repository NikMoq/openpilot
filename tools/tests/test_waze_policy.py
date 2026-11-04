"""Pure Waze source policy tests: no network, registration, vehicle or native IO."""
import math
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
from openpilot.sunnypilot.mapd.waze_policy import events_to_cameras, filter_fresh_waze, merge_camera_sources, waze_control_evidence

NOW = 1_800_000_000.0


def event(**overrides):
  values = dict(source='waze', source_kind='fixed', source_id='waze:fixed:row:1:2:55000000:37000000',
                latitude=55., longitude=37., raw_type='CAMERA', raw_subtype='SPEED_CAMERA', category_raw=2,
                heading_raw=0, speed_raw=60, speed_unit=None, speed_limit_kph=None,
                observed_at_ms=int(NOW*1000), expires_at_ms=int((NOW+43200)*1000), published_at_ms=None)
  values.update(overrides)
  return values


def verified_event(**overrides):
  """Synthetic evidence only: real backend does not currently assert these fields."""
  values = dict(speed_limit_kph=60, speed_unit='km/h', limit_status='verified', limit_source='RoadMapAlert.speed',
                direction_status='verified_approach')
  values.update(overrides)
  return event(**values)


def test_real_backend_shape_stays_warning_only_and_retains_raw_metadata():
  camera = events_to_cameras([event()], NOW)[0]
  assert camera.speed_limit_kph is None and camera.direction_deg is None and camera.direction_type is None
  assert camera.camera_type == 'waze:fixed:speed_camera'
  assert camera.source_params['speed_raw'] == '60' and camera.source_params['raw_subtype'] == 'SPEED_CAMERA'
  assert not waze_control_evidence(camera)


def test_synthetic_verified_fixed_evidence_is_consistent_and_does_not_depend_on_time():
  camera = events_to_cameras([verified_event()], NOW)[0]
  assert waze_control_evidence(camera)
  assert camera.direction_type == 1 and camera.direction_deg == 0
  assert not waze_control_evidence(replace(camera, speed_limit_kph=70))
  assert not waze_control_evidence(replace(camera, direction_deg=180))
  assert not waze_control_evidence(replace(camera, camera_type='speedcamonline:1'))
  assert not waze_control_evidence(replace(camera, direction_type=True))
  assert not waze_control_evidence(replace(camera, direction_deg=False))


@pytest.mark.parametrize('changes', [dict(speed_unit='mph'), dict(limit_status='unverified'), dict(limit_source='nearby_camera'),
                                  dict(direction_status='raw_camera_facing'), dict(heading_raw=None), dict(heading_raw=True),
                                  dict(speed_limit_kph=True), dict(speed_raw=59), dict(category_raw=3),
                                  dict(raw_subtype='DUMMY_CAMERA'), dict(raw_subtype='UNKNOWN'), dict(category_raw=True)])
def test_incomplete_or_conflicting_evidence_never_authorizes(changes):
  camera = events_to_cameras([verified_event(**changes)], NOW)[0]
  assert not waze_control_evidence(camera)


def test_mobile_post_has_no_speed_field_even_if_an_importer_asserts_a_fixed_camera_field():
  camera = events_to_cameras([verified_event(source_kind='mobile', raw_type='POLICE',
      raw_subtype='POLICE_WITH_MOBILE_CAMERA', source_id='waze:rt:post', expires_at_ms=int((NOW+1800)*1000))], NOW)[0]
  assert camera.camera_type == 'waze:mobile:POLICE_WITH_MOBILE_CAMERA'
  assert not waze_control_evidence(camera)


def test_observation_ttl_clamps_backend_mobile_ttl_and_keeps_old_active_source_report_time():
  mobile = event(source_kind='mobile', raw_type='POLICE', raw_subtype='POLICE_VISIBLE', source_id='waze:rt:post',
                 published_at_ms=int((NOW-8*3600)*1000), expires_at_ms=int((NOW+1800)*1000))
  camera = events_to_cameras([mobile], NOW)[0]
  assert filter_fresh_waze([camera], NOW+179.999)
  assert filter_fresh_waze([camera], NOW+180) == []
  assert events_to_cameras([event()], NOW+43200) == []


@pytest.mark.parametrize('changes', [dict(observed_at_ms=int((NOW+1)*1000)), dict(expires_at_ms=int(NOW*1000)),
                                  dict(published_at_ms=int((NOW+1)*1000)), dict(observed_at_ms=True),
                                  dict(latitude=math.nan), dict(longitude=181), dict(source='other'), dict(source_id='other:1')])
def test_invalid_future_or_stale_events_are_rejected(changes):
  assert events_to_cameras([event(**changes)], NOW) == []


def test_russian_authority_preserved_and_close_unknown_warning_cannot_shadow_it():
  russian = SpeedCamera(55., 37., 60., 0., 'speedcamonline:1', 1, source_id='ru:1')
  warning = events_to_cameras([event()], NOW)[0]
  result = merge_camera_sources([russian], [warning], NOW)
  assert result == [russian] and result[0].allowed_auto_control


def test_opposite_verified_approaches_and_waze_ids_are_not_coalesced():
  russian = SpeedCamera(55., 37., 60., 0., 'speedcamonline:1', 1, source_id='ru:1')
  opposite = events_to_cameras([verified_event(heading_raw=180, limit_status='unverified')], NOW)[0]
  result = merge_camera_sources([russian], [opposite], NOW)
  assert len(result) == 2 and result[0] is russian and result[1].direction_deg == 180


def test_newest_observation_wins_same_id_without_rejuvenating_it():
  older = event(observed_at_ms=int((NOW-10)*1000), speed_raw=30)
  cameras = events_to_cameras([older, event()], NOW)
  assert len(cameras) == 1 and cameras[0].source_params['speed_raw'] == '60'
  assert merge_camera_sources([], cameras, NOW+43200) == []


@pytest.mark.parametrize("kind", ["ROAD_CLOSED", "HAZARD", "JAM", "ACCIDENT"])
def test_unrelated_road_events_are_not_camera_warnings(kind):
  assert events_to_cameras([event(source_kind="mobile", raw_type=kind, raw_subtype="NO_SUBTYPE")], NOW) == []
