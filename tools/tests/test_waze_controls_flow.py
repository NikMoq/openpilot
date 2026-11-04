"""Waze policy -> real map source/wire -> resolver/SLA/planner regression tests.

Verified evidence below is synthetic. Acquisition must independently establish
road-limit provenance; raw RT headings and missing camera speeds never supply it.
"""

import time
import json
import math
from pathlib import Path

import capnp
import pytest

from test_camera_control_selection import map_pipeline as map_pipeline, latitude
from test_camera_resolver_integration import control as control, update, real_wire_controls
from test_waze_policy import event, verified_event
from test_waze_road_policy import road_event
from openpilot.sunnypilot.mapd.camera_database import CameraDatabase, save_database
from openpilot.sunnypilot.mapd.camera_speed_source import camera_identity
from openpilot.sunnypilot.mapd.waze_policy import events_to_cameras, filter_fresh_waze
from openpilot.sunnypilot.mapd.waze.clock import utc_now_s
from openpilot.sunnypilot.mapd.waze.road_match import annotate_mobile_corridor


def _pipeline(map_pipeline, control, monkeypatch):
  instance, _, _ = map_pipeline
  runtime = real_wire_controls(control, monkeypatch)
  clock = [time.monotonic()]
  monkeypatch.setattr(time, 'monotonic', lambda: clock[0])
  schema = capnp.load(str(Path(__file__).resolve().parents[2] / 'cereal/custom.capnp'))
  captured = []

  def send(service, message):
    assert service == 'liveMapDataSP'
    with schema.LiveMapDataSP.from_bytes(message.liveMapDataSP.to_bytes()) as reader:
      captured.append(reader.as_builder())

  instance.pm.send = send
  gps = instance.sm['gpsLocationExternal']
  control.sm['gps'] = gps

  def emit(north, cameras):
    clock[0] += 1
    gps.latitude = instance.last_position.latitude = latitude(north)
    instance.sm.logMonoTime['liveLocationKalman'] = int(clock[0] * 1e9)
    save_database(instance.camera_database_path, CameraDatabase('waze-synthetic', utc_now_s(), tuple(cameras)))
    instance.publish()
    lmd = captured[-1]
    control.sm['liveMapDataSP'] = lmd
    control.sm.recv_time['liveMapDataSP'] = clock[0]
    target = update(control)
    return lmd, target, runtime.publish()

  return emit, runtime


def test_verified_waze_approach_reduces_target_and_proven_pass_restores_driver_goal(map_pipeline, control, monkeypatch):
  emit, runtime = _pipeline(map_pipeline, control, monkeypatch)
  now = utc_now_s()
  raw = verified_event(latitude=latitude(250), longitude=37.0, observed_at_ms=int(now * 1000), expires_at_ms=int((now + 43200) * 1000))
  cameras = events_to_cameras([raw], now)
  assert cameras[0].allowed_auto_control
  emit(0, cameras)
  lmd, target, plan = emit(5, cameras)
  identity = lmd.speedCameraControlEventId
  assert identity and lmd.speedCameraControlValid
  assert lmd.speedCameraControlType == 'waze:fixed:speed_camera'
  assert target == pytest.approx(75 / 3.6)
  assert plan.speedCameraControl.rawLimit == pytest.approx(60 / 3.6)
  assert plan.speedCameraControl.appliedOffset == pytest.approx(15 / 3.6)
  assert runtime.helper.camera_episode_active
  for north in (50, 100, 150, 200, 240, 280, 300):
    lmd, target, plan = emit(north, cameras)
  assert lmd.speedCameraControlPassProven and lmd.speedCameraControlEventId == identity
  assert target == pytest.approx(100 / 3.6)
  assert plan.vTarget == pytest.approx(100 / 3.6)
  assert plan.speedCameraControl.state == 'passed'
  assert not plan.speedLimit.resolver.speedLimitValid
  assert plan.speedLimit.resolver.speedLimitFinalLast == 0


def test_source_expiry_during_active_episode_never_invents_passage(map_pipeline, control, monkeypatch):
  emit, _ = _pipeline(map_pipeline, control, monkeypatch)
  now = utc_now_s()
  cameras = events_to_cameras(
    [verified_event(latitude=latitude(250), longitude=37.0, observed_at_ms=int(now * 1000), expires_at_ms=int((now + 2) * 1000))], now
  )
  emit(0, cameras)
  before, target, _ = emit(5, cameras)
  assert target == pytest.approx(75 / 3.6) and before.speedCameraControlValid
  expired = filter_fresh_waze(cameras, now + 3)
  assert expired == []
  after, target, plan = emit(10, expired)
  assert not after.speedCameraControlPassProven
  assert after.speedCameraControlEventId == before.speedCameraControlEventId
  assert target == pytest.approx(75 / 3.6)
  assert plan.speedCameraControl.state != 'passed'


def test_real_unknown_limit_waze_warns_without_changing_driver_target(map_pipeline, control, monkeypatch):
  emit, _ = _pipeline(map_pipeline, control, monkeypatch)
  now = utc_now_s()
  cameras = events_to_cameras([event(latitude=latitude(250), longitude=37.0, observed_at_ms=int(now * 1000), expires_at_ms=int((now + 43200) * 1000))], now)
  emit(0, cameras)
  lmd, target, plan = emit(5, cameras)
  assert lmd.speedCameraAheadValid and lmd.speedCameraType == 'waze:fixed:speed_camera'
  assert lmd.speedCameraSpeedLimit == 0
  assert not lmd.speedCameraControlValid
  assert target == pytest.approx(100 / 3.6)
  assert not plan.speedLimit.resolver.speedLimitValid


def _synthetic_mobile_road_event(now, *, report_lifetime=180, road_lifetime=120):
  return road_event(
    latitude=latitude(250),
    longitude=37.0,
    heading_raw=270,
    speed_raw=None,
    observed_at_ms=int(now * 1000),
    expires_at_ms=int((now + report_lifetime) * 1000),
    road_observed_at_ms=int(now * 1000),
    road_limit_expires_at_ms=int((now + road_lifetime) * 1000),
    road_geometry=[[37.0, latitude(-500)], [37.0, latitude(6500)]],
  )


def _two_way_offline_geometry(instance):
  """Use the production graph with both directions for this synthetic two-way road."""
  from openpilot.sunnypilot.mapd.camera_osm_bridge import OfflineRoadSnapshot, RoadEdge

  graph = instance.camera_osm_bridge.snapshot
  reverse = [RoadEdge(edge.key + ':reverse', edge.way, edge.end, edge.start, edge.length) for edge in graph.edges]
  instance.camera_osm_bridge.snapshot = OfflineRoadSnapshot([*graph.edges, *reverse], graph.bounds, graph.anchor)


@pytest.mark.parametrize(
  'reverse,limit,positions',
  [
    (False, 50.0, (0, 5, 50, 100, 150, 200, 240, 280, 300)),
    (True, 70.0, (500, 495, 450, 400, 350, 300, 260, 220, 200)),
  ],
)
def test_mobile_road_limit_controls_each_approach_and_proven_pass_restores_driver_goal(map_pipeline, control, monkeypatch, reverse, limit, positions):
  instance, _, _ = map_pipeline
  _two_way_offline_geometry(instance)
  instance.last_bearing = instance.sm['gpsLocationExternal'].bearingDeg = 180 if reverse else 0
  emit, runtime = _pipeline(map_pipeline, control, monkeypatch)
  now = utc_now_s()
  raw = _synthetic_mobile_road_event(now)
  cameras = events_to_cameras([raw], now)
  assert len(cameras) == 3 and sum(camera.allowed_auto_control for camera in cameras) == 2
  assert raw['speed_raw'] is None and raw['heading_raw'] == 270
  emit(positions[0], cameras)
  lmd, target, plan = emit(positions[1], cameras)
  identity = lmd.speedCameraControlEventId
  selected = next(camera for camera in cameras if camera_identity(camera) == identity)
  assert selected.source_id.endswith(':road:123:reverse' if reverse else ':road:123:forward')
  assert lmd.speedCameraControlValid and lmd.speedCameraControlAutoControlAllowed
  assert lmd.speedCameraControlType == 'waze:mobile:POLICE_WITH_MOBILE_CAMERA'
  assert target == pytest.approx((limit + 15) / 3.6)
  assert plan.speedCameraControl.rawLimit == pytest.approx(limit / 3.6)
  assert plan.speedCameraControl.appliedOffset == pytest.approx(15 / 3.6)
  assert json.loads(selected.source_params['waze_event'])['speed_raw'] is None
  assert runtime.helper.camera_episode_active
  for north in positions[2:]:
    lmd, target, plan = emit(north, cameras)
  assert lmd.speedCameraControlPassProven and lmd.speedCameraControlEventId == identity
  assert target == pytest.approx(100 / 3.6) and plan.vTarget == pytest.approx(100 / 3.6)
  assert plan.speedCameraControl.state == 'passed'
  assert not plan.speedLimit.resolver.speedLimitValid
  assert plan.speedLimit.resolver.speedLimitFinalLast == 0


@pytest.mark.parametrize('expired_source', ['report', 'road'])
def test_mobile_report_or_road_limit_expiry_during_active_episode_does_not_invent_passage(map_pipeline, control, monkeypatch, expired_source):
  instance, _, _ = map_pipeline
  _two_way_offline_geometry(instance)
  emit, _ = _pipeline(map_pipeline, control, monkeypatch)
  now = utc_now_s()
  raw = _synthetic_mobile_road_event(now, report_lifetime=2 if expired_source == 'report' else 180, road_lifetime=2 if expired_source == 'road' else 120)
  cameras = events_to_cameras([raw], now)
  emit(0, cameras)
  before, target, _ = emit(5, cameras)
  assert before.speedCameraControlValid and target == pytest.approx(65 / 3.6)
  expired = events_to_cameras([raw], now + 3)
  assert all(not camera.allowed_auto_control for camera in expired)
  after, target, plan = emit(10, expired)
  assert not after.speedCameraControlPassProven
  assert after.speedCameraControlEventId == before.speedCameraControlEventId
  assert target == pytest.approx(65 / 3.6)
  assert plan.speedCameraControl.state != 'passed'


@pytest.mark.parametrize('opposite', [False, True])
def test_mobile_parallel_one_way_carriageways_use_each_posted_limit_and_prove_pass(map_pipeline, control, monkeypatch, opposite):
  """Synthetic evidence exercises the real matcher, offline graph, wire and planner."""
  from openpilot.sunnypilot.mapd.camera_osm_bridge import OfflineRoadSnapshot, RoadEdge

  instance, _, _ = map_pipeline
  delta = math.degrees(20 / (6371000 * math.cos(math.radians(55))))
  other_lon = 37 + delta
  graph = instance.camera_osm_bridge.snapshot
  southbound = [RoadEdge(edge.key + ':opposite', edge.way + ':opposite',
                        (edge.end[0], edge.end[1] + delta), (edge.start[0], edge.start[1] + delta), edge.length)
                for edge in graph.edges]
  instance.camera_osm_bridge.snapshot = OfflineRoadSnapshot([*graph.edges, *southbound], graph.bounds, (latitude(250), 37))
  gps = instance.sm['gpsLocationExternal']
  instance.last_bearing = gps.bearingDeg = 180 if opposite else 0
  instance.last_position.longitude = gps.longitude = other_lon if opposite else 37
  emit, runtime = _pipeline(map_pipeline, control, monkeypatch)
  now = utc_now_s()
  raw = _synthetic_mobile_road_event(now)
  common = dict(source='waze_practice_features', speed_unit='km/h', primary_street_id=900,
                observed_at_ms=int(now * 1000), expires_at_ms=int((now + 120) * 1000),
                bbox=(36.99, 54.99, 37.01, 55.1), usable_for_control=True, restriction_reason='')
  roads = [dict(common, segment_id=123, from_node_id=1, to_node_id=2,
                geometry=[(37, latitude(-500)), (37, latitude(6500))], forward_allowed=True, reverse_allowed=False,
                forward_limit_kph=50, reverse_limit_kph=None),
           dict(common, segment_id=124, from_node_id=3, to_node_id=4,
                geometry=[(other_lon, latitude(-500)), (other_lon, latitude(6500))], forward_allowed=False, reverse_allowed=True,
                forward_limit_kph=None, reverse_limit_kph=70)]
  matched = annotate_mobile_corridor(raw, roads, int(now * 1000))
  assert len(matched) == 2
  cameras = events_to_cameras(matched, now)
  assert sum(camera.allowed_auto_control for camera in cameras) == 2
  positions = (500, 495, 450, 400, 350, 300, 260, 220, 200) if opposite else (0, 5, 50, 100, 150, 200, 240, 280, 300)
  emit(positions[0], cameras)
  lmd, target, plan = emit(positions[1], cameras)
  identity = lmd.speedCameraControlEventId
  selected = next(camera for camera in cameras if camera_identity(camera) == identity)
  assert selected.source_id.endswith(':road:124:reverse' if opposite else ':road:123:forward')
  assert selected.longitude == pytest.approx(other_lon if opposite else 37)
  assert lmd.speedCameraControlValid and runtime.helper.camera_episode_active
  limit = 70 if opposite else 50
  assert target == pytest.approx((limit + 15) / 3.6)
  assert plan.speedCameraControl.rawLimit == pytest.approx(limit / 3.6)
  assert plan.speedCameraControl.appliedOffset == pytest.approx(15 / 3.6)
  original = json.loads(selected.source_params['waze_event'])
  assert original['longitude'] == 37 and original['heading_raw'] == 270 and original['speed_raw'] is None
  for north in positions[2:]:
    lmd, target, plan = emit(north, cameras)
  assert lmd.speedCameraControlPassProven and lmd.speedCameraControlEventId == identity
  assert target == pytest.approx(100 / 3.6) and plan.vTarget == pytest.approx(100 / 3.6)
  assert plan.speedCameraControl.state == 'passed'
