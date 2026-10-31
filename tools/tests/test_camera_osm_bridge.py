"""Installed mapd packed tiles -> local road bridge -> camera event, no network."""
import math
import sys
from pathlib import Path
from types import ModuleType

import capnp
import pytest

ROOT = Path(__file__).resolve().parents[2]
for name, relative in [('openpilot', '.'), ('openpilot.sunnypilot', 'sunnypilot'), ('openpilot.sunnypilot.mapd', 'sunnypilot/mapd')]:
  if name not in sys.modules:
    module = ModuleType(name)
    module.__path__ = [str(ROOT / relative)]
    sys.modules[name] = module

from openpilot.sunnypilot.mapd.camera_osm_bridge import OfflineOsmBridge, OfflineRoadSnapshot, RoadEdge
from openpilot.sunnypilot.mapd.camera_speed_source import CameraSpeedSource, xy
from openpilot.sunnypilot.mapd.speed_cameras import SpeedCamera

SCHEMA = capnp.load(str(ROOT/'sunnypilot/mapd/camera_osm_bridge.capnp'))
NOW = 1_800_000_000.


def point(north=0.,east=0.):
  return 55+math.degrees(north/6371000),37+math.degrees(east/(6371000*math.cos(math.radians(55))))


def tile(root, roads, *, bounds=(54.9,36.9,55.1,37.1), speeds=123):
  name = '_'.join(f'{v:.6f}' for v in bounds)
  target = root/'offline'/str(math.floor(bounds[0]/2)*2)/str(math.floor(bounds[1]/2)*2)/name
  target.parent.mkdir(parents=True,exist_ok=True)
  msg = SCHEMA.Offline.new_message()
  msg.minLat,msg.minLon,msg.maxLat,msg.maxLon = bounds
  msg.overlap = .001
  ways = msg.init('ways',len(roads))
  for way,points in zip(ways,roads):
    way.minLat = min(p[0] for p in points)
    way.minLon = min(p[1] for p in points)
    way.maxLat = max(p[0] for p in points)
    way.maxLon = max(p[1] for p in points)
    way.oneWay = True
    way.maxSpeed = way.maxSpeedForward = way.maxSpeedBackward = speeds
    nodes = way.init('nodes',len(points))
    for node,p in zip(nodes,points):
      node.latitude,node.longitude = p
  target.write_bytes(msg.to_bytes_packed())
  return target


def load(root,where=None):
  bridge = OfflineOsmBridge(root)
  assert bridge.tick(*(point() if where is None else where))
  bridge._worker.join(5)
  assert not bridge._worker.is_alive()
  return bridge


def test_packed_installed_geometry_selects900m_camera_without_network_or_map_speed(tmp_path):
  tile(tmp_path,[[point(-100),point(1500)]],speeds=5)
  bridge = load(tmp_path)
  assert bridge.status == 'ready' and bridge.snapshot is not None
  item = SpeedCamera(*point(920),60,0,'speedcamonline:1',1)
  source = CameraSpeedSource()
  for north,t in [(0,0),(20,1)]:
    selection = source.update([item],*point(north),0,gps_valid=True,fix_time_s=NOW+t,now_s=NOW+t,
                              reaction_distance_m=900,road_graph=bridge.snapshot)
  assert selection.valid and selection.state == 'ACTIVE'
  assert selection.camera.speed_limit_kph == 60
  assert selection.distance_m == pytest.approx(900,abs=.01)


def test_curved_offline_road_overrides_straight_ray_corridor(tmp_path):
  tile(tmp_path,[[point(-100),point(400),point(400,500)]])
  bridge = load(tmp_path)
  item = SpeedCamera(*point(400,400),60,90,'speedcamonline:1',1)
  source = CameraSpeedSource()
  for north,t in [(0,0),(10,1)]:
    selection = source.update([item],*point(north),0,gps_valid=True,fix_time_s=NOW+t,now_s=NOW+t,
                              reaction_distance_m=900,road_graph=bridge.snapshot)
  assert selection.valid and selection.distance_m == pytest.approx(790,abs=.2)


def test_parallel_road_camera_cannot_join_vehicle_path(tmp_path):
  tile(tmp_path,[[point(-100),point(1500)],[point(-100,20),point(1500,20)]])
  bridge = load(tmp_path)
  assert bridge.snapshot.camera_distance(point(),0,point(900,20),0) is None


def test_turning_branch_is_not_guessed(tmp_path):
  tile(tmp_path,[[point(-100),point(200),point(1500)],[point(200),point(200,200)]])
  bridge = load(tmp_path)
  assert bridge.snapshot.camera_distance(point(),0,point(900),0) is None


@pytest.mark.parametrize('vehicle,camera', [(-5.,5.),(-50.,0.),(0.,50.)])
def test_distinct_packed_ways_on_unique_continuation_associate_at_joint(tmp_path,vehicle,camera):
  tile(tmp_path,[[point(-100),point(0)],[point(0),point(1500)]])
  bridge = load(tmp_path)
  assert bridge.snapshot.camera_distance(point(vehicle),0,point(camera),0) == pytest.approx(camera-vehicle,abs=.01)


def test_near_joint_branch_is_not_treated_as_unique_continuation(tmp_path):
  tile(tmp_path,[[point(-100),point(0)],[point(0),point(1500)],[point(0),point(1500,500)]])
  bridge = load(tmp_path)
  assert bridge.snapshot.associate(point(-5),0) is None
  assert bridge.snapshot.camera_distance(point(-50),0,point(5),0) is None


def test_distinct_crossing_polylines_do_not_acquire_node_connectivity(tmp_path):
  tile(tmp_path,[[point(-100),point(1500)],[point(300,-500),point(300,500)]])
  bridge = load(tmp_path)
  assert bridge.snapshot.camera_distance(point(),0,point(300,400),90) is None


def test_missing_maps_remains_unavailable_without_creating_or_fetching_files(tmp_path):
  bridge = load(tmp_path)
  assert bridge.snapshot is None and bridge.status == 'offline_maps_unavailable'
  assert not list(tmp_path.iterdir())


def test_corrupt_packed_tile_reports_decode_failure_without_payload(tmp_path):
  path = tile(tmp_path,[[point(-100),point(1500)]])
  path.write_bytes(b'\x00')
  bridge = load(tmp_path)
  assert bridge.snapshot is None and bridge.status == 'offline_map_decode_error'


@pytest.mark.parametrize('budget,reason',[
  ('MAX_TILE_BYTES','offline_map_size_budget'), ('MAX_NEARBY_TILES','offline_map_tile_budget'),
  ('MAX_DIRECTORY_ENTRIES','offline_map_directory_budget'), ('MAX_LOCAL_EDGES','offline_map_edge_budget'),
  ('MAX_INDEX_REFERENCES','offline_map_index_budget')])
def test_worker_preserves_categorized_budget_failure(tmp_path,monkeypatch,budget,reason):
  import openpilot.sunnypilot.mapd.camera_osm_bridge as module
  tile(tmp_path,[[point(-100),point(1500)]])
  monkeypatch.setattr(module,budget,0)
  bridge = load(tmp_path)
  assert bridge.snapshot is None and bridge.status == reason


def test_worker_preserves_bounds_mismatch(tmp_path):
  path = tile(tmp_path,[[point(-100),point(1500)]])
  message = SCHEMA.Offline.from_bytes_packed(path.read_bytes()).as_builder()
  message.minLat -= .01
  path.write_bytes(message.to_bytes_packed())
  bridge = load(tmp_path)
  assert bridge.snapshot is None and bridge.status == 'offline_map_bounds_mismatch'


@pytest.mark.parametrize('error,reason',[
  (PermissionError('private path and payload'), 'offline_map_permission_denied'),
  (OSError('private path and payload'), 'offline_map_io_error'),
  (RuntimeError('private path and payload'), 'offline_map_load_error')])
def test_worker_never_exposes_unrecognized_exception_text(tmp_path,monkeypatch,error,reason):
  bridge = OfflineOsmBridge(tmp_path)
  def fail(*args):
    raise error
  monkeypatch.setattr(bridge,'_paths',fail)
  assert bridge.tick(*point())
  bridge._worker.join(5)
  assert not bridge._worker.is_alive()
  assert bridge.snapshot is None and bridge.status == reason


def test_worker_schema_failure_is_distinct_from_missing_maps(tmp_path,monkeypatch):
  def fail(*args):
    raise RuntimeError('private schema path and payload')
  monkeypatch.setattr(capnp,'load',fail)
  bridge = load(tmp_path)
  assert bridge.snapshot is None and bridge.status == 'offline_map_schema_error'


def test_adaptive_small_tile_filename_and_actual_bounds_are_supported(tmp_path):
  tile(tmp_path,[[point(-100),point(900)]],bounds=(54.99,36.99,55.01,37.01))
  bridge = load(tmp_path)
  assert bridge.snapshot is not None and bridge.snapshot.inside(point())


def test_corrupt_tile_is_not_accepted(tmp_path):
  path = tile(tmp_path,[[point(-100),point(900)]])
  path.write_bytes(b'not capnp')
  bridge = load(tmp_path)
  assert bridge.snapshot is None


def test_repeated_same_position_does_not_reload_or_rescan_tiles(tmp_path):
  tile(tmp_path,[[point(-100),point(900)]])
  bridge = load(tmp_path)
  worker = bridge._worker
  for _ in range(100):
    assert not bridge.tick(*point())
  assert bridge._worker is worker


def test_partial_tile_coverage_gap_cannot_be_traversed_on_same_long_way():
  a,b = (0.,0.),(0.,.03)
  edge = RoadEdge('way:0','way',a,b,math.hypot(*xy(b,a)))
  snapshot = OfflineRoadSnapshot([edge],[(-.01,-.01,.01,.01),(-.01,.02,.01,.04)],(0.,0.))
  assert not snapshot.segment_covered((0.,.005),(0.,.025))
  assert snapshot.camera_distance((0.,.005),90,(0.,.025),90) is None


def test_matching_neighbor_tiles_merge_without_duplicate_road_ambiguity(tmp_path):
  roads = [[point(-100),point(1500)]]
  tile(tmp_path,roads,bounds=(54.99,36.99,55.005,37.01))
  tile(tmp_path,roads,bounds=(55.005,36.99,55.02,37.01))
  bridge = load(tmp_path)
  assert bridge.snapshot.camera_distance(point(),0,point(900),0) == pytest.approx(900,abs=.01)


@pytest.mark.parametrize('heading',[2.,5.,15.,358.,355.,345.])
def test_directional_road_match_tolerates_accepted_gps_course_error(tmp_path,heading):
  tile(tmp_path,[[point(-100),point(1500)]])
  bridge = load(tmp_path)
  assert bridge.snapshot.camera_distance(point(),heading,point(900),0) == pytest.approx(900,abs=.01)


def test_unchanged_periodic_tile_check_does_not_parse_packed_data_again(tmp_path,monkeypatch):
  tile(tmp_path,[[point(-100),point(900)]])
  bridge = load(tmp_path)
  snapshot = bridge.snapshot
  def forbidden(*args):
    raise AssertionError('unchanged tiles must not be decoded again')
  monkeypatch.setattr(bridge,'_bytes',forbidden)
  bridge._next_check = 0
  assert bridge.tick(*point())
  bridge._worker.join(5)
  assert bridge.status == 'ready' and bridge.snapshot is snapshot


def test_roadside_camera25m_match_does_not_expand_vehicle_gps_tolerance(tmp_path):
  tile(tmp_path,[[point(-100),point(1500)]])
  bridge = load(tmp_path)
  assert bridge.snapshot.camera_distance(point(),0,point(900,20),0) == pytest.approx(900,abs=.1)
  assert bridge.snapshot.camera_distance(point(0,20),0,point(900,20),0) is None
  assert bridge.snapshot.passed_distance(point(950),0,point(900,20),0) == pytest.approx(50,abs=.1)


def test_roadside_camera_on_competing_parallel_road_is_not_used(tmp_path):
  tile(tmp_path,[[point(-100),point(1500)],[point(-100,15),point(1500,15)]])
  bridge = load(tmp_path)
  assert bridge.snapshot.camera_distance(point(),0,point(900,20),0) is None


def test_same_fix_reuses_one_route_and_static_camera_projections(tmp_path,monkeypatch):
  tile(tmp_path,[[point(-100),point(1500)]])
  snapshot = load(tmp_path).snapshot
  original = snapshot._associate_uncached
  associations = []
  def counted(*args):
    associations.append(args)
    return original(*args)
  monkeypatch.setattr(snapshot,'_associate_uncached',counted)
  assert snapshot.camera_distance(point(),0,point(600),0) == pytest.approx(600,abs=.1)
  route = snapshot._route_entries
  for _ in range(20):
    assert snapshot.camera_distance(point(),0,point(900),0) == pytest.approx(900,abs=.1)
    assert snapshot.camera_distance(point(),0,point(600),0) == pytest.approx(600,abs=.1)
    assert snapshot._route_entries is route
  assert len(associations) == 3  # Vehicle once, each static camera once.
  assert snapshot.camera_distance(point(10),0,point(900),0) == pytest.approx(890,abs=.1)
  assert snapshot._route_entries is not route


def test_cached_route_keeps_covered_prefix_but_never_crosses_gap():
  a,b = (0.,0.),(0.,.03)
  edge = RoadEdge('way:0','way',a,b,math.hypot(*xy(b,a)))
  snapshot = OfflineRoadSnapshot([edge],[(-.01,-.01,.01,.01),(-.01,.02,.01,.04)],a)
  prefix = snapshot.camera_distance((0.,.005),90,(0.,.009),90)
  route = snapshot._route_entries
  assert prefix is not None and prefix > 0
  assert snapshot.camera_distance((0.,.005),90,(0.,.025),90) is None
  assert snapshot._route_entries is route
  assert snapshot.camera_distance((0.,.005),90,(0.,.009),90) == prefix


def test_cached_route_never_extends_past_ambiguous_branch(tmp_path):
  tile(tmp_path,[[point(-100),point(200),point(1500)],[point(200),point(200,200)]])
  snapshot = load(tmp_path).snapshot
  assert snapshot.camera_distance(point(),0,point(100),0) == pytest.approx(100,abs=.1)
  route = snapshot._route_entries
  for _ in range(3):
    assert snapshot.camera_distance(point(),0,point(900),0) is None
    assert snapshot._route_entries is route
    assert snapshot.camera_distance(point(),0,point(100),0) == pytest.approx(100,abs=.1)


def test_association_lru_is_bounded_and_refusals_are_not_recomputed(tmp_path,monkeypatch):
  tile(tmp_path,[[point(-100),point(1500)]])
  snapshot = load(tmp_path).snapshot
  for i in range(300):
    assert snapshot.associate((56.+i*.0001,38.),0) is None
  assert len(snapshot._association_cache) == 256
  def forbidden(*args):
    raise AssertionError('cached refusal must not be recomputed')
  monkeypatch.setattr(snapshot,'_associate_uncached',forbidden)
  assert snapshot.associate((56.+299*.0001,38.),0) is None


def test_reusing_forward_and_reverse_cache_does_not_change_pass_distance(tmp_path):
  tile(tmp_path,[[point(-100),point(1500)]])
  snapshot = load(tmp_path).snapshot
  for _ in range(3):
    assert snapshot.camera_distance(point(950),0,point(900,20),0) is None
    assert snapshot.passed_distance(point(950),0,point(900,20),0) == pytest.approx(50,abs=.1)
    assert snapshot.camera_distance(point(800),0,point(900,20),0) == pytest.approx(100,abs=.1)


def test_cumulative_index_reference_budget_rejects_many_long_segments_before_allocation(monkeypatch):
  import openpilot.sunnypilot.mapd.camera_osm_bridge as module
  # Each segment is individually legal (9 cells), but their cumulative index
  # would exceed the reduced test budget. Only the first 9 refs are allocated.
  monkeypatch.setattr(module,'MAX_INDEX_REFERENCES',16)
  a,b = (0.,0.),(.005,.005)
  edges = [RoadEdge(f'way-{i}:0',f'way-{i}',a,b,math.hypot(*xy(b,a))) for i in range(100)]
  with pytest.raises(ValueError,match='offline_map_index_budget'):
    OfflineRoadSnapshot(edges,[(-1.,-1.,1.,1.)],a)
