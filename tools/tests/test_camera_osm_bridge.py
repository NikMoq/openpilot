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


def tile(root, roads, *, bounds=(54.9,36.9,55.1,37.1), speeds=123, one_way=True):
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
    way.oneWay = one_way
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


def test_reported_vehicle_uncertainty_is_bounded_cached_and_not_camera_uncertainty(tmp_path):
  tile(tmp_path,[[point(-100),point(1500)]])
  graph = load(tmp_path).snapshot
  options = dict(vehicle_tolerance_m=30.,vehicle_heading_tolerance_deg=45.)
  assert graph.camera_distance(point(0,25),40,point(900),0) is None
  assert graph.camera_distance(point(0,25),40,point(900),0,**options) == pytest.approx(900,abs=.1)
  assert graph.camera_distance(point(0,25),40,point(900),0) is None
  assert graph.camera_distance(point(0,25),40,point(900),40,**options) is None
  assert graph.camera_distance(point(0,25),40,point(900,30),0,**options) is None
  assert graph.camera_distance(point(0,120),40,point(900),0,vehicle_tolerance_m=120.,vehicle_heading_tolerance_deg=90.) is None
  assert graph.passed_distance(point(920,25),40,point(900),0,**options) == pytest.approx(20,abs=.1)


def test_reported_uncertainty_still_rejects_competing_parallel_roads(tmp_path):
  tile(tmp_path,[[point(-100),point(1500)],[point(-100,20),point(1500,20)]])
  graph = load(tmp_path).snapshot
  assert graph.camera_distance(point(0,10),0,point(900),0,vehicle_tolerance_m=30.,vehicle_heading_tolerance_deg=45.) is None


def test_offset_fix_gap_cannot_release_camera_and_two_progressing_fixes_prove_pass(tmp_path):
  tile(tmp_path,[[point(-100),point(1500)]])
  graph = load(tmp_path).snapshot
  source = CameraSpeedSource()
  item = SpeedCamera(*point(100),60,0,'speedcamonline:1',1)
  def fix(north,t,valid=True):
    return source.update([item],*point(north,25),40,gps_valid=valid,fix_time_s=NOW+t,now_s=NOW+t,
                         road_graph=graph,vehicle_tolerance_m=30.,vehicle_heading_tolerance_deg=45.)
  fix(0,0)
  acquired = fix(10,1)
  assert acquired.valid and acquired.state == 'ACTIVE'
  lost = fix(120,2,False)
  assert lost.camera_id == acquired.camera_id and not lost.pass_proven and not lost.valid
  assert not fix(120,3).pass_proven
  assert not fix(120,3).pass_proven  # duplicate cannot provide another observation
  assert not fix(130,4).pass_proven
  assert fix(140,5).pass_proven


def test_existing_packed_route_ambiguity_can_count_can_distance_then_recover(tmp_path):
  tile(tmp_path/'clear',[[point(-100),point(1500)]])
  tile(tmp_path/'ambiguous',[[point(-100),point(1500)],[point(-100,5),point(1500,5)]])
  clear = load(tmp_path/'clear').snapshot
  ambiguous = load(tmp_path/'ambiguous').snapshot
  source = CameraSpeedSource()
  item = SpeedCamera(*point(100),60,0,'speedcamonline:1',1)
  def fix(north,t,graph):
    source.update([item],*point(north),0,gps_valid=True,fix_time_s=NOW+t,now_s=NOW+t,road_graph=graph)
    return source.update_odometry((500.+t,10.),trusted_position=True)
  fix(0,0,clear)
  assert fix(10,1,clear).distance_m == pytest.approx(90,abs=.1)
  tracked = fix(20,2,ambiguous)
  assert tracked.valid and tracked.reason == 'odometry_tracking'
  assert tracked.distance_m == pytest.approx(80,abs=.1)
  recovered = fix(30,3,clear)
  assert recovered.valid and recovered.reason == 'offline_approach'
  assert recovered.distance_m == pytest.approx(70,abs=.1)
  for north in range(40,111,10):
    tracked = fix(north, north/10,ambiguous)
  assert tracked.pass_proven and tracked.reason == 'odometry_passed'


def test_curved_offline_road_overrides_straight_ray_corridor(tmp_path):
  tile(tmp_path,[[point(-100),point(400),point(400,500)]])
  bridge = load(tmp_path)
  item = SpeedCamera(*point(400,400),60,90,'speedcamonline:1',1)
  source = CameraSpeedSource()
  for north,t in [(0,0),(10,1)]:
    selection = source.update([item],*point(north),0,gps_valid=True,fix_time_s=NOW+t,now_s=NOW+t,
                              reaction_distance_m=900,road_graph=bridge.snapshot)
  assert selection.valid and selection.distance_m == pytest.approx(790,abs=.2)


def test_unique_packed_bend_accepts_measured_corner_course_without_wider_corridor(tmp_path):
  tile(tmp_path,[[point(-100),point(0),point(0,100),point(100,100),point(100,200)]])
  snapshot = load(tmp_path).snapshot
  measured = snapshot.camera_distance(point(-3,3),45,point(0,80),90)
  assert measured is not None and 70 < measured < 90


def test_camera_on_unique_packed_corner_matches_local_course(tmp_path):
  tile(tmp_path,[[point(-100),point(0),point(0,100)]])
  snapshot = load(tmp_path).snapshot
  assert snapshot.camera_distance(point(-50),0,point(),45) == pytest.approx(50,abs=.1)


def test_corner_course_does_not_join_a_real_branch(tmp_path):
  tile(tmp_path,[[point(-100),point(0),point(0,100)],[point(0),point(100)]])
  snapshot = load(tmp_path).snapshot
  assert snapshot.associate(point(-3,3),45) is None


def test_corner_heading_does_not_match_interior_of_straight_segment(tmp_path):
  tile(tmp_path,[[point(-100),point(0),point(0,100)]])
  snapshot = load(tmp_path).snapshot
  assert snapshot.associate(point(-50),45) is None


@pytest.mark.parametrize('one_way',[False,True])
def test_packed_corner_preserves_legitimate_raw_edge_heading(tmp_path,one_way):
  tile(tmp_path,[[point(-100),point(0),point(0,100)]],one_way=one_way)
  snapshot = load(tmp_path).snapshot
  assert snapshot.associate(point(-3),0) is not None
  assert snapshot.associate(point(0,3),90) is not None
  assert snapshot.associate(point(-3,3),45) is not None
  assert (snapshot.associate(point(-3,3),225) is not None) is (not one_way)
  if not one_way:
    assert snapshot.camera_distance(point(0,50),270,point(),225) == pytest.approx(50,abs=.1)


@pytest.mark.parametrize('one_way',[False,True])
def test_packed_curve_source_keeps_camera_through_bend_and_proves_pass(tmp_path,one_way):
  tile(tmp_path,[[point(-100),point(0),point(0,100)]],one_way=one_way)
  snapshot = load(tmp_path).snapshot
  item = SpeedCamera(*point(),60,45,'speedcamonline:1',1)
  source = CameraSpeedSource()
  samples = [(-50,0,0),(-40,0,0),(-3,3,45),(0,25,90),(0,30,90)]
  results = []
  for second,(north,east,heading) in enumerate(samples):
    results.append(source.update([item],*point(north,east),heading,gps_valid=True,
                                 fix_time_s=NOW+second,now_s=NOW+second,road_graph=snapshot))
  assert results[1].valid and results[1].state == 'ACTIVE'
  assert results[2].camera_id == results[1].camera_id
  assert results[2].reason != 'approach_corridor_lost'
  assert not results[3].pass_proven
  assert results[4].state == 'PASSED' and results[4].pass_proven


def test_parallel_hairpin_leg_is_not_reclassified_as_local_corner(tmp_path):
  tile(tmp_path,[[point(-100),point(100)],[point(100,15),point(-100,15)]])
  snapshot = load(tmp_path).snapshot
  assert snapshot.camera_distance(point(),0,point(0,15),180) is None
  # Interior course differs45deg from its local straight leg; the distant
  # hairpin's tangent cannot authorize a new association here.
  assert snapshot.associate(point(),45) is None


def test_connected_unique_hairpin_keeps_real_route_distance_not_nearby_leg_shortcut(tmp_path):
  tile(tmp_path,[[point(-100),point(100),point(100,15),point(-100,15)]])
  snapshot = load(tmp_path).snapshot
  distance = snapshot.camera_distance(point(),0,point(0,15),180)
  assert distance == pytest.approx(215,abs=.01)


def test_opposite_collinear_joint_has_no_derived_tangent(tmp_path):
  tile(tmp_path,[[point(-100),point(0),point(-100)]])
  snapshot = load(tmp_path).snapshot
  assert snapshot.associate(point(-3),90) is None


def test_disconnected_nearby_corner_does_not_create_shared_endpoint(tmp_path):
  tile(tmp_path,[[point(-100),point(-1)],[point(0),point(0,100)]])
  snapshot = load(tmp_path).snapshot
  assert snapshot.associate(point(-3,3),45) is None


def test_high_degree_joint_refuses_tangent_before_neighbor_filtering(monkeypatch):
  a,joint = point(-100),point()
  incoming = RoadEdge('before','before',a,joint,math.hypot(*xy(joint,a)))
  edges = [incoming]
  for i in range(1000):
    end = point(0,100+i)
    edges.append(RoadEdge(f'after-{i}',f'after-{i}',joint,end,math.hypot(*xy(end,joint))))
  snapshot = OfflineRoadSnapshot(edges,[(54.9,36.9,55.1,37.1)],joint)
  def forbidden(*args):
    raise AssertionError('high-degree tangent must not walk/filter neighbors')
  monkeypatch.setattr(snapshot,'_unique_continuation',forbidden)
  assert not snapshot._heading_matches(incoming,point(-3,3),0,45,12)


def test_incoming_index_has_same_edge_budget_as_existing_snapshot(monkeypatch):
  import openpilot.sunnypilot.mapd.camera_osm_bridge as module
  monkeypatch.setattr(module,'MAX_LOCAL_EDGES',1)
  a,b,c = point(-100),point(),point(0,100)
  edges = [RoadEdge('a','a',a,b,math.hypot(*xy(b,a))),RoadEdge('b','b',b,c,math.hypot(*xy(c,b)))]
  with pytest.raises(ValueError,match='offline_map_edge_budget'):
    OfflineRoadSnapshot(edges,[(54.9,36.9,55.1,37.1)],b)


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
