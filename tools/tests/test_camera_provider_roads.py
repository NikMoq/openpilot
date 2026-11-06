"""Real provider admission and shared approach/pass state; synthetic geometry."""
import math
from dataclasses import replace
from types import SimpleNamespace

import pytest

from test_yandex_policy import NOW, event
from openpilot.sunnypilot.mapd.yandex_policy import events_to_cameras
from openpilot.sunnypilot.mapd.camera_provider_roads import ProviderRoadGraph
from openpilot.sunnypilot.mapd.camera_speed_source import CameraSpeedSource, xy
from openpilot.sunnypilot.mapd.speed_cameras import SpeedCamera
from test_camera_control_selection import map_pipeline as map_pipeline
from test_camera_resolver_integration import control as control


def camera(**overrides):
  record = event()
  record.update(approach_geometry=[[38., 45.], [38., 45.001]], approach_source='yandex_native_route',
                approach_observed_at=NOW, approach_expires_at=NOW + 300)
  record.update(overrides)
  return events_to_cameras((record,), record['observed_at'])[0]


def graph(offline=None, now=NOW):
  return ProviderRoadGraph(offline, now_s=now)


def test_missing_offline_uses_exact_source_polyline_and_pass_state():
  item = camera()
  source = CameraSpeedSource()
  selections = []
  for index, latitude in enumerate((45.0000, 45.00002, 45.00075, 45.0008)):
    time = 100 + 2 * index
    selections.append(source.update([item], latitude, 38., 0, gps_valid=True, fix_time_s=time, now_s=time,
                                    road_graph=graph()))
  assert selections[0].reason == 'confirming_approach'
  assert selections[1].valid and selections[1].state == 'ACTIVE'
  assert not selections[2].pass_proven
  assert selections[3].pass_proven and selections[3].state == 'PASSED'


def test_parallel_other_road_cannot_borrow_verified_limit():
  item = camera()
  assert graph().camera_distance_for_camera(item, (45.0001, 38.), 0, 0) is not None
  assert graph().camera_distance_for_camera(item, (45.0001, 38.0003), 0, 0) is None  # ~24m parallel road.
  assert graph().camera_distance_for_camera(item, (45.0001, 38.), 180, 0) is None


def test_no_extrapolation_or_legacy_euclidean_admission():
  item = camera()
  assert graph().camera_distance_for_camera(item, (44.9999, 38.), 0, 0) is None
  legacy = SpeedCamera(45.0005, 38., 60, 0, 'legacy', 1)
  assert graph().camera_distance_for_camera(legacy, (45.0001, 38.), 0, 0) is None
  assert graph().camera_distance_for_camera(replace(item, speed_limit_kph=80), (45.0001, 38.), 0, 0) is None


def test_expired_source_cannot_keep_retained_control_even_if_offline_succeeds():
  class Offline:
    def camera_distance(self, *args, **kwargs):
      return 10
  assert graph(Offline(), NOW + 300).camera_distance_for_camera(camera(), (45.0001, 38.), 0, 0) is None


def test_expiry_does_not_reactivate_retained_can_approach_or_prove_passage():
  item = camera()
  source = CameraSpeedSource()
  for time, latitude in ((100, 45.0000), (101, 45.00002)):
    selected = source.update([item], latitude, 38., 0, gps_valid=True, fix_time_s=time, now_s=time, road_graph=graph())
  assert selected.valid
  source.update_odometry((101., 10.), trusted_position=True)
  selected = source.update([], 45.00002, 38., 0, gps_valid=False, fix_time_s=102, now_s=102, road_graph=graph(now=NOW + 300))
  assert not selected.valid and not selected.pass_proven
  selected = source.update_odometry((102., 10.), trusted_position=False)
  assert not selected.valid and not selected.pass_proven
  assert source._camera is None and not source._completed


def test_actual_fresh_same_identity_replaces_expired_retained_object_without_reset():
  item = camera()
  source = CameraSpeedSource()
  for time, latitude in ((100, 45.0000), (101, 45.00002)):
    selected = source.update([item], latitude, 38., 0, gps_valid=True, fix_time_s=time, now_s=time, road_graph=graph())
  assert selected.valid
  source.update_odometry((101., 10.), trusted_position=True)
  anchor = source.odometry
  fresh = camera(observed_at=NOW + 299, expires_at=NOW + 599,
                 approach_observed_at=NOW + 299, approach_expires_at=NOW + 599)
  selected = source.update([fresh], 45.00004, 38., 0, gps_valid=True, fix_time_s=102, now_s=102,
                           road_graph=graph(now=NOW + 301))
  assert selected.valid and selected.state == 'ACTIVE'
  assert source._camera is fresh and source.odometry is anchor and source._confirmed_approach


def test_offline_success_has_priority_and_covered_ambiguity_is_not_overridden():
  class Offline:
    def __init__(self, distance, associated):
      self.distance, self.associated = distance, associated

    def camera_distance(self, *args, **kwargs):
      return self.distance

    def inside(self, point):
      return True

    def associate(self, *args, **kwargs):
      return self.associated

    def segment_covered(self, *args):
      return False

  item = camera()
  assert graph(Offline(123, None)).camera_distance_for_camera(item, (45.0001, 38.), 0, 0) == 123
  assert graph(Offline(None, None)).camera_distance_for_camera(item, (45.0001, 38.), 0, 0) is None
  assert graph(Offline(None, ('road', 1))).camera_distance_for_camera(item, (45.0001, 38.), 0, 0) is not None


@pytest.mark.parametrize('change', [{'approach_source': 'unknown'}, {'approach_observed_at': NOW - 1},
                                    {'approach_expires_at': NOW + 400}, {'approach_geometry': []}])
def test_unverified_approach_evidence_does_not_supply_road(change):
  assert graph().camera_distance_for_camera(camera(**change), (45.0001, 38.), 0, 0) is None


def test_localization_stays_required():
  selected = CameraSpeedSource().update([camera()], 45.0001, 38., 0, gps_valid=False, fix_time_s=100, now_s=100, road_graph=graph())
  assert not selected.valid and selected.reason == 'localizer_unreliable'


def test_projection_work_is_bounded():
  roads = graph()
  roads._budget = 0
  assert roads.camera_distance_for_camera(camera(), (45.0001, 38.), 0, 0) is None


def test_duplicate_vertices_preserve_exact_path_without_extrapolation():
  original = camera()
  duplicate = camera(approach_geometry=[[38., 45.], [38., 45.], [38., 45.001], [38., 45.001]])
  expected = graph().camera_distance_for_camera(original, (45.0001, 38.), 0, 0)
  assert graph().camera_distance_for_camera(duplicate, (45.0001, 38.), 0, 0) == expected
  assert graph().camera_distance_for_camera(duplicate, (44.9999, 38.), 0, 0) is None
  empty = camera(approach_geometry=[[38., 45.], [38., 45.]])
  assert graph().camera_distance_for_camera(empty, (45., 38.), 0, 0) is None


def test_waze_uses_own_verified_segment_for_each_directed_limit():
  from test_waze_road_policy import road_event
  from test_waze_policy import NOW as WAZE_NOW
  from openpilot.sunnypilot.mapd.waze_policy import events_to_cameras as waze_cameras

  controls = [item for item in waze_cameras((road_event(),), WAZE_NOW) if item.allowed_auto_control]
  north = next(item for item in controls if item.direction_deg == 0)
  south = next(item for item in controls if item.direction_deg == 180)
  assert graph(now=WAZE_NOW).camera_distance_for_camera(north, (54.9995, 37.), 0, 0) is not None
  assert graph(now=WAZE_NOW).camera_distance_for_camera(south, (55.0005, 37.), 180, 180) is not None
  assert graph(now=WAZE_NOW + 180).camera_distance_for_camera(north, (54.9995, 37.), 0, 0) is None


def test_parallel_loop_geometry_does_not_choose_a_competing_projection():
  item = camera(approach_geometry=[[38., 45.], [38., 45.001], [38.0001, 45.001], [38.0001, 45.], [38., 45.]])
  assert graph().camera_distance_for_camera(item, (45.0001, 38.), 0, 0) is None


@pytest.mark.parametrize('passed,vehicle_lat', [(False, 45.0001), (True, 45.0008)])
@pytest.mark.parametrize('gap,allowed', [((45.0015, 45.0018), False), ((44.9992, 44.9995), False),
                                       ((45.0003, 45.0007), True)])
def test_only_actual_projected_interval_can_authorize_coverage_gap(passed, vehicle_lat, gap, allowed):
  checked = []

  class Offline:
    def camera_distance(self, *args, **kwargs):
      return None  # E.g. a covered fork whose continuation was not selected.

    passed_distance = camera_distance

    def inside(self, point):
      return True

    def associate(self, *args, **kwargs):
      return ('unique-road', 0)

    def segment_covered(self, start, end):
      checked.append((start, end))
      low, high = sorted((start[0], end[0]))
      return min(high, gap[1]) <= max(low, gap[0])

  item = camera(approach_geometry=[[38., 44.999], [38., 45.002]])
  roads = graph(Offline())
  method = roads.passed_distance_for_camera if passed else roads.camera_distance_for_camera
  distance = method(item, (vehicle_lat, 38.), 0, 0)
  assert (distance is not None) == allowed
  assert checked
  for start, end in checked:
    assert min(vehicle_lat, item.latitude) - 1e-10 <= start[0] <= max(vehicle_lat, item.latitude) + 1e-10
    assert min(vehicle_lat, item.latitude) - 1e-10 <= end[0] <= max(vehicle_lat, item.latitude) + 1e-10


def test_covered_osm_small_bends_remain_matched_without_threshold_relaxation():
  from openpilot.sunnypilot.mapd.camera_osm_bridge import OfflineRoadSnapshot, RoadEdge

  def point(north, east):
    return (45 + math.degrees(north / 6371000), 38 + math.degrees(east / (6371000 * math.cos(math.radians(45)))))

  for bend in (5, 10, 20, 30):
    a, joint = point(-100, 0), point(100, 0)
    end = point(100 + 200 * math.cos(math.radians(bend)), 200 * math.sin(math.radians(bend)))
    edges = [RoadEdge(str(index), 'road', start, finish, math.hypot(*xy(finish, start)))
             for index, (start, finish) in enumerate(((a, joint), (joint, end)))]
    roads = OfflineRoadSnapshot(edges, ((44.9, 37.9, 45.1, 38.1),), point(0, 0))
    target = point(100 + 100 * math.cos(math.radians(bend)), 100 * math.sin(math.radians(bend)))
    assert roads.camera_distance(point(0, 0), 0, target, bend) == pytest.approx(200, abs=.02)
    # The same-way main continuation is authoritative through a side street;
    # a target on the side street still cannot select an unobserved turn.
    spur_end = point(100, -100)
    spur = RoadEdge('spur', 'side-road', joint, spur_end, math.hypot(*xy(spur_end, joint)))
    fork = OfflineRoadSnapshot(edges + [spur], ((44.9, 37.9, 45.1, 38.1),), point(0, 0))
    assert fork.associate(point(0, 0), 0) is not None
    assert fork.associate(target, bend, tolerance=25.) is not None
    assert fork.camera_distance(point(0, 0), 0, target, bend) == pytest.approx(200, abs=.02)
    item = SpeedCamera(*target, 60, bend, 'speedcamonline:1', 1)
    source = CameraSpeedSource()
    states = []
    for index, progress in enumerate((0, 5, 50, 90, 130, 180, 225, 230)):
      position = point(progress, 0) if progress <= 100 else point(
        100 + (progress - 100) * math.cos(math.radians(bend)), (progress - 100) * math.sin(math.radians(bend)))
      heading = 0 if progress <= 100 else bend
      states.append(source.update([item], *position, heading, gps_valid=True,
                                  fix_time_s=100 + index * 2, now_s=100 + index * 2, road_graph=fork))
    assert states[1].valid and not states[-2].pass_proven
    assert states[-1].pass_proven
    assert fork.camera_distance(point(0, 0), 0, point(100, -70), 270) is None
    ambiguous = OfflineRoadSnapshot(edges + [replace(spur, way='road')], ((44.9, 37.9, 45.1, 38.1),), point(0, 0))
    assert ambiguous.camera_distance(point(0, 0), 0, target, bend) is None


@pytest.mark.parametrize('provider', ['waze', 'yandex'])
def test_provider_geometry_without_osm_through_real_wire_and_planner(map_pipeline, control, monkeypatch, provider):
  from test_waze_controls_flow import _pipeline
  from test_yandex_controls_flow import record
  from test_waze_road_policy import road_event
  from test_camera_control_selection import latitude
  from openpilot.sunnypilot.mapd.waze.clock import utc_now_s
  from openpilot.sunnypilot.mapd.waze_policy import events_to_cameras as waze_cameras

  instance, _, _ = map_pipeline
  instance.camera_osm_bridge = SimpleNamespace(snapshot=None, status='offline_maps_unavailable', tick=lambda *_: False)
  emit, _ = _pipeline(map_pipeline, control, monkeypatch)
  now = utc_now_s()
  if provider == 'yandex':
    raw = record(now)
    raw.update(approach_geometry=raw['road_geometry'], approach_source='yandex_native_route',
               approach_observed_at=now, approach_expires_at=raw['expires_at'])
    cameras = events_to_cameras((raw,), now)
    expected = 75 / 3.6
  else:
    raw = road_event(latitude=latitude(250), longitude=37.,
                     observed_at_ms=int(now * 1000), expires_at_ms=int((now + 180) * 1000),
                     road_geometry=[[37., latitude(-500)], [37., latitude(6500)]],
                     road_observed_at_ms=int(now * 1000), road_limit_expires_at_ms=int((now + 180) * 1000))
    cameras = waze_cameras((raw,), now)
    expected = 65 / 3.6
  first, _, _ = emit(0, cameras)
  assert first.speedCameraControlReason == 'confirming_approach'
  active, target, _ = emit(5, cameras)
  assert active.speedCameraControlValid and target == pytest.approx(expected)
  for north in (50, 100, 150, 200, 240, 280, 300):
    passed, target, plan = emit(north, cameras)
  assert passed.speedCameraControlPassProven and target == pytest.approx(100 / 3.6)
  assert plan.speedCameraControl.state == 'passed'
