"""Explicit local-junction policy: connected arms, bounded distance, CAN passage."""
import pytest

from test_camera_osm_bridge import NOW, dense_way, load, point, tile
from openpilot.sunnypilot.mapd.camera_speed_source import CameraSpeedSource, camera_identity, xy
from openpilot.sunnypilot.mapd.speed_cameras import SpeedCamera


def junction_graph(root, *, spacing=None):
  roads = [[(-1200.,0.),(0.,0.),(500.,0.)], [(0.,0.),(0.,500.)], [(0.,0.),(0.,-500.)]]
  tile(root, [dense_way(road,spacing) if spacing else [point(*node) for node in road] for road in roads])
  return load(root).snapshot


@pytest.mark.parametrize('camera_point',[point(40),point(0,40),point(0,-40)])
def test_junction_accepts_straight_sibling_and_opposite_arms_without_strict_route(tmp_path,camera_point):
  graph = junction_graph(tmp_path)
  assert graph.camera_distance(point(-100),0,camera_point,0) is None
  distance,junction = graph.junction_camera_approach(point(-100),0,camera_point)
  assert distance == pytest.approx(140.,abs=.05)
  assert junction == point()
  # The new approximate API does not invent a strict forward route.
  assert graph.camera_distance(point(-100),0,camera_point,0) is None


@pytest.mark.parametrize('branch_distance,eligible',[(40.,True),(49.,True),(50.,True),(51.,False)])
def test_junction_camera_radius_is_fifty_metres_from_node(tmp_path,branch_distance,eligible):
  graph = junction_graph(tmp_path)
  result = graph.junction_camera_approach(point(-100),0,point(0,branch_distance))
  assert (result is not None) is eligible
  if eligible:
    assert result[0] == pytest.approx(100.+branch_distance,abs=.05)


def test_junction_road_projection_does_not_expand_physical_camera_radius(tmp_path):
  graph = junction_graph(tmp_path)
  # Road projection is inside 50 m, but the provider point is outside it.
  assert graph.junction_camera_approach(point(-100),0,point(24,49)) is None


@pytest.mark.parametrize('incoming_distance',[300.,1000.])
def test_dense_two_metre_four_arm_junction_keeps_incoming_reaction_lookahead(tmp_path,incoming_distance):
  graph = junction_graph(tmp_path,spacing=2.)
  for camera_point in (point(40),point(0,50),point(0,-40)):
    result = graph.junction_camera_approach(point(-incoming_distance),0,camera_point)
    assert result is not None
    branch = 50. if camera_point == point(0,50) else 40.
    assert result[0] == pytest.approx(incoming_distance+branch,abs=.05)


@pytest.mark.parametrize('scenario',['parallel','crossing','incoming_arm','behind_vehicle'])
def test_junction_does_not_join_disconnected_or_backward_camera(tmp_path,scenario):
  roads = [[point(-1200),point(),point(500)], [point(),point(0,500)]]
  if scenario == 'parallel':
    roads.append([point(-500,10),point(500,10)])
    camera = point(30,10)
  elif scenario == 'crossing':
    roads.append([point(30,-500),point(30,500)])
    camera = point(30,40)
  else:
    camera = point(-20 if scenario == 'incoming_arm' else -150)
  tile(tmp_path,roads)
  graph = load(tmp_path).snapshot
  assert graph.junction_camera_approach(point(-100),0,camera) is None


def test_junction_budget_failure_does_not_publish_partial_context(tmp_path,monkeypatch):
  import openpilot.sunnypilot.mapd.camera_osm_bridge as module
  graph = junction_graph(tmp_path,spacing=5.)
  monkeypatch.setattr(module,'MAX_JUNCTION_NODES',1)
  assert graph.junction_camera_approach(point(-100),0,point(0,40)) is None


class JunctionGraph:
  """Explicit fake topology boundary for source clock/odometry tests only."""
  def __init__(self):
    self.key = 'junction-a'
    self.available = True

  def camera_distance(self,*args,**kwargs):
    return None

  def passed_distance(self,*args,**kwargs):
    raise AssertionError('Approximate junction events cannot use graph/GPS passage')

  def associate(self,*args,**kwargs):
    return object()

  def junction_camera_approach(self,position,heading,camera_point,**kwargs):
    return (150.-xy(position,point())[1],self.key) if self.available else None


def junction_camera(direction=90,source_id='junction-camera'):
  return SpeedCamera(*point(0,40),60,direction,'speedcamonline:1',1,source_id=source_id)


FRESH = object()


def fix(source,graph,north,second,*,east=0.,heading=0.,valid=True,speed=10.,sample=FRESH,cameras=None):
  source.update([junction_camera()] if cameras is None else cameras,*point(north,east),heading,
                gps_valid=valid,fix_time_s=NOW+second,now_s=NOW+second,road_graph=graph)
  return source.update_odometry((500.+second,speed) if sample is FRESH else sample,trusted_position=valid)


@pytest.mark.parametrize('direction',[0.,90.,180.,270.])
def test_junction_source_confirms_two_positions_then_requires_actual_can(direction):
  graph,source = JunctionGraph(),CameraSpeedSource()
  cameras = [junction_camera(direction)]
  assert not fix(source,graph,0,0,sample=None,cameras=cameras).valid
  confirmed = fix(source,graph,10,1,sample=None,cameras=cameras)
  assert not confirmed.valid and confirmed.reason == 'odometry_unavailable'
  assert not confirmed.pass_proven
  selected = fix(source,graph,20,2,cameras=cameras)
  assert selected.valid and selected.reason == 'junction_approach'
  assert selected.distance_m == pytest.approx(130.)  # Refreshed before first CAN anchor.


@pytest.mark.parametrize('actual_arm',['camera_arm','other_arm'])
def test_junction_actual_route_and_wrong_branch_both_end_at_frozen_can_distance(tmp_path,actual_arm):
  graph,source = junction_graph(tmp_path),CameraSpeedSource()
  fix(source,graph,-100,0)
  initial = fix(source,graph,-90,1)
  assert initial.valid and initial.reason == 'junction_approach'
  assert initial.distance_m == pytest.approx(130.,abs=.05)
  for step in range(1,14):
    travelled = step*10.
    north = min(0.,-90.+travelled) if actual_arm == 'camera_arm' else -90.+travelled
    east = max(0.,travelled-90.) if actual_arm == 'camera_arm' else 0.
    # Packed-coordinate conversion can leave a sub-nanometre residual.
    result = fix(source,graph,north,1+step+(.001 if step == 13 else 0.),east=east,heading=90. if east else 0.)
    assert result.camera_id == initial.camera_id
    if step < 13:
      assert result.valid and not result.pass_proven
      assert result.distance_m == pytest.approx(130.-travelled,abs=.05)
    else:
      assert result.pass_proven and result.reason == 'odometry_passed'


def test_junction_gps_loop_or_behind_fixes_cannot_pass_without_can_motion():
  graph,source = JunctionGraph(),CameraSpeedSource()
  fix(source,graph,0,0,speed=0.)
  initial = fix(source,graph,10,1,speed=0.)
  assert initial.valid
  for second,(north,east) in enumerate([(50,0),(90,0),(130,0),(170,0),(180,0),(150,20),(110,20)],2):
    result = fix(source,graph,north,second,east=east,speed=0.)
    assert result.valid and not result.pass_proven
    assert result.reason == 'junction_approach'
    assert result.distance_m == pytest.approx(initial.distance_m)


def test_junction_stationary_or_invalid_llk_cannot_acquire_from_can():
  for invalid in (False,True):
    graph,source = JunctionGraph(),CameraSpeedSource()
    for second in range(4):
      selected = fix(source,graph,second*10 if invalid else 0.,second,valid=not invalid,speed=0.)
      assert not selected.valid and not selected.pass_proven and not selected.camera_id


@pytest.mark.parametrize('changed_context',['different_node','missing','position_jump'])
def test_junction_missing_initial_can_cannot_later_apply_obsolete_context(changed_context):
  graph,source = JunctionGraph(),CameraSpeedSource()
  fix(source,graph,0,0,sample=None)
  assert not fix(source,graph,10,1,sample=None).valid
  if changed_context == 'missing':
    graph.available = False
  else:
    graph.key = 'junction-b'
  if changed_context == 'position_jump':
    jumped = fix(source,graph,10,2,east=200.)
    assert not jumped.valid and not jumped.pass_proven
    result = fix(source,graph,10,3,east=210.)
  else:
    result = fix(source,graph,20,2)
  assert not result.valid and not result.pass_proven and not result.camera_id


def test_junction_pending_approach_is_isolated_by_node_identity():
  graph,source = JunctionGraph(),CameraSpeedSource()
  fix(source,graph,0,0)
  graph.key = 'junction-b'
  assert not fix(source,graph,10,1).valid
  selected = fix(source,graph,20,2)
  assert selected.valid and selected.reason == 'junction_approach'
  assert selected.camera_id == camera_identity(junction_camera())


@pytest.mark.parametrize('fault',['duplicate','missing','long_gap'])
def test_junction_can_gap_or_duplicate_does_not_spend_or_reanchor(fault):
  graph,source = JunctionGraph(),CameraSpeedSource()
  fix(source,graph,0,0)
  initial = fix(source,graph,10,1)
  sample = (501.,10.) if fault == 'duplicate' else None if fault == 'missing' else (506.,10.)
  unavailable = fix(source,graph,20,2,sample=sample)
  assert not unavailable.valid and not unavailable.pass_proven
  assert unavailable.reason == 'odometry_unavailable'
  if fault == 'duplicate':
    resumed = fix(source,graph,30,3,sample=(502.,10.))
  else:
    first = 503. if fault == 'missing' else 507.
    recovered = fix(source,graph,30,3,sample=(first,10.))
    if fault == 'missing':
      assert not recovered.valid
      first += 1
    resumed = fix(source,graph,40,4,sample=(first,10.)) if fault == 'missing' else recovered
  assert resumed.valid and resumed.distance_m == pytest.approx(initial.distance_m-10.)


def test_junction_lost_llk_retains_existing_event_but_cannot_start_one():
  graph,source = JunctionGraph(),CameraSpeedSource()
  fix(source,graph,0,0)
  selected = fix(source,graph,10,1)
  retained = fix(source,graph,20,2,valid=False)
  assert retained.valid and retained.reason == 'odometry_tracking'
  assert retained.camera_id == selected.camera_id
  assert retained.distance_m == pytest.approx(selected.distance_m-10.)
