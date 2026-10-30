"""Opt-in, disposable Linux runtime smoke using native msgq and Params.

Run only inside the isolated runtime container with PQ35_RUNTIME_SMOKE=1 and
PARAMS_ROOT=/data/params. Uses OsmMapData.tick -> BaseMapData.publish -> real
msgq -> SubMaster decoded messages -> CameraSpeedSource/Resolver/SLA/LongitudinalPlannerSP.
No module/socket or Params substitutes. Longitudinal tests supply only a given
MPC crash counter; they do not execute an MPC solver or model inference.
"""

from contextlib import ExitStack
from dataclasses import dataclass
import importlib.machinery
import math
import os
import sys
import time

import pytest

if sys.platform != 'linux' or os.getenv('PQ35_RUNTIME_SMOKE') != '1':
  pytest.skip('requires explicit disposable Linux runtime opt-in', allow_module_level=True)

from cereal import car, custom, messaging
import msgq.ipc_pyx
from openpilot.common import params_pyx
from openpilot.common.params import Params
from openpilot.system.hardware.hw import Paths
from openpilot.sunnypilot.mapd.camera_database import CameraDatabase, save_database
from openpilot.sunnypilot.mapd.camera_updater import CameraParams
from openpilot.sunnypilot.mapd.cityguide import parse_cityguide_snapshot
from openpilot.sunnypilot.mapd.live_map_data.base_map_data import BaseMapData
from openpilot.sunnypilot.mapd.live_map_data.osm_map_data import OsmMapData
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_resolver import SpeedLimitResolver
from openpilot.sunnypilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlannerSP
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import Mode


LAT, LON = 55.0, 37.0
USER_SPEED = 30.0
LIMIT = 60 / 3.6
EGO_LAT = LAT - math.degrees(100 / 6_371_000.0)
INPUT_SERVICES = ['carState', 'carControl', 'gpsLocation', 'controlsState',
                  'carStateSP', 'modelV2', 'radarState', 'selfdriveState', 'selfdriveStateSP']


@pytest.fixture
def native_params():
  assert os.environ.get('PARAMS_ROOT') == '/data/params'
  assert 'OPENPILOT_PREFIX' not in os.environ
  assert 'CEREAL_FAKE' not in os.environ and 'ZMQ' not in os.environ
  params = Params()
  params.put_bool('SpeedCameraEnabled', True)
  params.put_bool('SpeedCameraControlEnabled', True)
  params.put('SpeedCameraAlertDistanceMeters', '1000')
  params.put('SpeedCameraReactionDistance', 300)
  params.put('SpeedLimitOffsetType', 0)
  params.put('SpeedLimitValueOffset', 0)
  params.put_bool('IsMetric', True)
  params.put_bool('UbloxAvailable', False)
  yield params


def test_native_extensions_and_params_roundtrip(native_params):
  for module in (msgq.ipc_pyx, params_pyx):
    assert any(module.__file__.endswith(suffix) for suffix in importlib.machinery.EXTENSION_SUFFIXES)
  assert native_params.get_type('SpeedCameraControlEnabled') == params_pyx.ParamKeyType.BOOL
  assert native_params.get('SpeedCameraControlEnabled') is True
  assert Params().get('SpeedCameraAlertDistanceMeters') == '1000'


@pytest.mark.parametrize('configured_root', [True, False])
def test_camera_params_matches_native_pc_and_prefixed_roots(monkeypatch, tmp_path, configured_root):
  prefix = f'pq35-runtime-path-{tmp_path.name}'
  monkeypatch.setenv('OPENPILOT_PREFIX', prefix)
  if configured_root:
    monkeypatch.setenv('PARAMS_ROOT', str(tmp_path / 'params'))
  else:
    monkeypatch.delenv('PARAMS_ROOT', raising=False)
  native = Params()
  adapter = CameraParams()
  assert os.path.realpath(native.get_param_path()) == os.path.realpath(adapter.root)
  adapter.put('SpeedCameraDatabaseStatus', 'update requested')
  assert native.get('SpeedCameraDatabaseStatus') == 'update requested'
  native.put_bool('SpeedCameraEnabled', True)
  assert adapter.get('SpeedCameraEnabled') == '1'


@pytest.fixture
def pipeline(native_params, tmp_path, monkeypatch):
  cache = tmp_path / 'cameras.json'
  native_params.put('SpeedCameraDatabasePath', str(cache))
  monkeypatch.setattr(Paths, 'mapd_root', lambda: str(tmp_path))
  import capnp
  from pathlib import Path
  source_root = Path(__file__).resolve().parents[2]
  schema = capnp.load(str(source_root / 'sunnypilot/mapd/camera_osm_bridge.capnp'))
  tile = schema.Offline.new_message(minLat=54.9, minLon=36.9, maxLat=55.1, maxLon=37.1, overlap=.001)
  way = tile.init('ways', 1)[0]
  way.oneWay = True
  way.minLat, way.minLon, way.maxLat, way.maxLon = 54.95, LON, 55.05, LON
  nodes = way.init('nodes', 2)
  nodes[0].latitude, nodes[0].longitude = 54.95, LON
  nodes[1].latitude, nodes[1].longitude = 55.05, LON
  folder = tmp_path / 'offline' / '54' / '36'
  folder.mkdir(parents=True)
  (folder / '54.900000_36.900000_55.100000_37.100000').write_bytes(tile.to_bytes_packed())
  # A nonexistent legacy graph path must not affect database-only operation.
  native_params.put('SpeedCameraRoadGraphPath', str(tmp_path / 'missing-legacy-graph.osm'))
  publisher = messaging.PubMaster(['liveLocationKalman', *INPUT_SERVICES])
  map_data = OsmMapData()
  map_data.camera_osm_bridge.tick(LAT, LON)
  map_data.camera_osm_bridge._worker.join(5)
  assert map_data.camera_osm_bridge.snapshot is not None
  receiver = messaging.SubMaster(['liveMapDataSP', *INPUT_SERVICES], poll='modelV2')
  assert type(map_data.params) is Params
  assert OsmMapData.publish is BaseMapData.publish
  yield cache, publisher, receiver, map_data


def receive(sm, services):
  deadline = time.monotonic() + 2
  pending = set(services)
  while pending and time.monotonic() < deadline:
    sm.update(100)
    pending.difference_update(service for service in services if sm.updated[service])
  assert not pending, f'msgq receive timed out: {pending}'


def publish_frame(pipeline, *, gps_ok=True, gas=False, stock_speed=USER_SPEED, model_yaw_rate=0.001, user_cruise=USER_SPEED,
                  brake=False, buttons=(), cc_enabled=True, acc_enabled=True, cc_override=False, cc_cancel=False, cc_resume=False,
                  north_m=-100, gps_time_s=None, cruise_helper=None, previous_plan=None):
  _, publisher, receiver, map_data = pipeline
  ego_lat = LAT + math.degrees(north_m / 6_371_000.0)
  fix_ms = int((time.time() if gps_time_s is None else gps_time_s) * 1000)  # noqa: TID251 - GPS Unix timestamp
  samples = [(ego_lat, fix_ms)]
  if gps_ok and not map_data.camera_selection.camera_id:
    # Deliver both measured approach fixes through real msgq. The first fix is
    # five metres behind; no source-valid flag or selection is substituted.
    samples.insert(0, (ego_lat - math.degrees(5 / 6_371_000.0), fix_ms - 1000))
  for sample_lat, sample_ms in samples:
    # Each transmitted Cap'n Proto message owns a fresh builder. Reusing one
    # across the two real input samples triggers pycapnp's repeated-write guard.
    location = messaging.new_message('liveLocationKalman', valid=True)
    llk = location.liveLocationKalman
    llk.status = 'valid'
    llk.gpsOK = gps_ok
    llk.positionGeodetic.valid = True
    llk.positionGeodetic.value = [sample_lat, LON, 0.0]
    llk.calibratedOrientationNED.valid = True
    llk.calibratedOrientationNED.value = [0.0, 0.0, 0.0]
    gps_msg = messaging.new_message('gpsLocation', valid=True)
    gps = gps_msg.gpsLocation
    gps.hasFix = gps_ok
    gps.speed = USER_SPEED
    gps.horizontalAccuracy = 3.0
    gps.bearingAccuracyDeg = 2.0
    gps.unixTimestampMillis = sample_ms
    gps.latitude, gps.longitude, gps.bearingDeg = sample_lat, LON, 0.0
    publisher.send('gpsLocation', gps_msg)
    publisher.send('liveLocationKalman', location)
    receive(map_data.sm, ['liveLocationKalman', 'gpsLocation'])
    map_data.tick()

  cs_msg = messaging.new_message('carState', valid=True)
  cs = cs_msg.carState
  cs.vEgo = USER_SPEED
  cs.vCruise = user_cruise * 3.6
  cs.vCruiseCluster = user_cruise * 3.6
  cs.gasPressed = gas
  cs.brakePressed = brake
  cs.buttonEvents = [dict(type=kind, pressed=pressed) for kind, pressed in buttons]
  cs.cruiseState.available = True
  cs.cruiseState.enabled = acc_enabled
  cs.cruiseState.speed = stock_speed
  cs.cruiseState.speedCluster = stock_speed
  if cruise_helper is not None:
    assert previous_plan is not None
    # The same ordering as card: previous plan -> helper -> published virtual setpoint.
    cruise_helper.update_speed_limit_assist(True, previous_plan)
    cruise_helper.update_v_cruise(cs, cc_enabled, True)
    cs.vCruise = float(cruise_helper.v_cruise_kph)
    cs.vCruiseCluster = float(cruise_helper.v_cruise_cluster_kph)
  publisher.send('carState', cs_msg)
  cc_msg = messaging.new_message('carControl', valid=True)
  cc_msg.carControl.enabled = cc_enabled
  cc_msg.carControl.cruiseControl.override = cc_override
  cc_msg.carControl.cruiseControl.cancel = cc_cancel
  cc_msg.carControl.cruiseControl.resume = cc_resume
  publisher.send('carControl', cc_msg)
  for service in ('controlsState', 'carStateSP', 'radarState', 'selfdriveState', 'selfdriveStateSP'):
    publisher.send(service, messaging.new_message(service, valid=True))
  model_msg = messaging.new_message('modelV2', valid=True)
  model_msg.modelV2.position.x = [float(i * 5) for i in range(33)]
  model_msg.modelV2.orientation.x = [0.0] * 33
  model_msg.modelV2.orientationRate.z = [model_yaw_rate] * 33
  model_msg.modelV2.velocity.x = [USER_SPEED] * 33
  publisher.send('modelV2', model_msg)
  receive(receiver, receiver.services)
  return receiver


def plan(resolver, sm):
  resolver.update(USER_SPEED, sm, camera_compatible=True)
  return resolver.camera.output


def write_camera(cache, kind, trusted, speed_kph=60):
  metadata = '|1705|180|1713|1' if trusted else ''
  raw = f'2|Radars|65001\n{kind}|Synthetic runtime fixture|{LAT}|{LON}|1709|{speed_kph}{metadata}\n'
  cameras = tuple(parse_cityguide_snapshot(raw))
  assert len(cameras) == 1
  save_database(str(cache), CameraDatabase('runtime-fixture', time.time(), cameras))  # noqa: TID251 - persisted cache Unix time


@dataclass
class GivenMpcState:
  """Explicit supplied-state boundary: DEC reads this counter; no solver runs."""
  crash_cnt: int = 0


@pytest.fixture
def longitudinal(native_params):
  native_params.put('SpeedLimitMode', int(Mode.assist))
  native_params.put_bool('IsMetric', True)
  native_params.put_bool('UbloxAvailable', False)
  for key in ('SmartCruiseControlMap', 'SmartCruiseControlVision', 'DynamicExperimentalControl',
              'GreenLightAlert', 'LeadDepartAlert'):
    native_params.put_bool(key, False)
  native_params.remove('ModelManager_ActiveBundle')
  mpc = GivenMpcState()
  with ExitStack() as stack:
    def construct(*, vision=False, pcm_cruise=False):
      native_params.put_bool('SmartCruiseControlVision', vision)
      cp = car.CarParams.new_message(brand='volkswagen', pcmCruise=pcm_cruise, openpilotLongitudinalControl=False)
      cp_sp = custom.CarParamsSP.new_message(pcmCruiseSpeed=False, intelligentCruiseButtonManagementAvailable=True)
      cp_read = stack.enter_context(car.CarParams.from_bytes(cp.to_bytes()))
      cp_sp_read = stack.enter_context(custom.CarParamsSP.from_bytes(cp_sp.to_bytes()))
      planner = LongitudinalPlannerSP(cp_read, cp_sp_read, mpc)
      assert planner.camera_compatible and planner.generation is None
      assert planner.dec._mpc is mpc
      return planner
    yield construct


@pytest.fixture
def plan_bus():
  publisher = messaging.PubMaster(['longitudinalPlanSP'])
  receiver = messaging.SubMaster(['longitudinalPlanSP'])
  yield publisher, receiver


def run_longitudinal(planner, sm, plan_bus, *, user_cruise=USER_SPEED, now_s=None):
  planner.sla.update_car_state(sm['carState'])
  planner.camera.observe_car_state(sm['carState'])  # noqa: TID251 - latch shares Unix GPS clock
  planner.update(sm)
  assert planner.dec._mpc_fcw_crash_cnt == planner.dec._mpc.crash_cnt
  target = planner.update_targets(sm, USER_SPEED, 0.0, user_cruise)
  publisher, receiver = plan_bus
  planner.publish_longitudinal_plan_sp(sm, publisher)
  receive(receiver, ['longitudinalPlanSP'])
  assert receiver.valid['longitudinalPlanSP'] == sm.all_checks(['carState', 'controlsState'])
  result = receiver['longitudinalPlanSP']
  assert result.vTarget == pytest.approx(target[0])
  assert result.aTarget == pytest.approx(target[1])
  assert result.longitudinalPlanSource == planner.source
  return target, result



@pytest.mark.parametrize('kind,trusted,allowed', [('18059',True,True),('18950',True,True),('18958',True,True),('18059',False,False),('99999',True,False)])
def test_production_map_publish_and_decoded_resolver(pipeline, kind, trusted, allowed):
  write_camera(pipeline[0],kind,trusted)
  sm=publish_frame(pipeline)
  lmd=sm['liveMapDataSP']
  assert sm.valid['liveMapDataSP'] and lmd.locationValid and lmd.speedCameraAheadValid
  assert lmd.speedCameraSpeedLimit == pytest.approx(LIMIT)
  assert lmd.speedCameraControlValid is allowed
  output=plan(SpeedLimitResolver(),sm)
  assert output.automatic is allowed
  if allowed: assert output.state=='ACTIVE' and output.raw_limit==pytest.approx(LIMIT)


def test_production_invalid_map_holds_then_driver_button_overrides(pipeline):
  write_camera(pipeline[0],'18059',True)
  resolver=SpeedLimitResolver()
  initial=plan(resolver,publish_frame(pipeline))
  held=plan(resolver,publish_frame(pipeline,gps_ok=False))
  assert held.automatic and held.raw_limit==initial.raw_limit and not held.countdown_valid
  manual=plan(resolver,publish_frame(pipeline,buttons=(('accelCruise',True),)))
  assert manual.manual and not manual.automatic and manual.state=='DRIVER_OVERRIDE'


@pytest.mark.parametrize('eligible',[False,True])
def test_production_nearest_warning_independent_control(pipeline,eligible):
  next_lat=LAT+math.degrees(400/6371000)
  raw=f'2|Radars|65001\n18952|warning|{LAT}|{LON}|1709|60|\n'
  if eligible: raw+=f'18059|control|{next_lat}|{LON}|1709|60|1705|180|1713|1|\n'
  cameras=tuple(parse_cityguide_snapshot(raw))
  save_database(str(pipeline[0]),CameraDatabase('runtime-selection',time.time(),cameras))
  sm=publish_frame(pipeline)
  lmd=sm['liveMapDataSP']
  assert lmd.speedCameraAheadValid and lmd.speedCameraType=='speedcamonline-cg77:18952'
  assert lmd.speedCameraControlValid is eligible
  output=plan(SpeedLimitResolver(),sm)
  # 500m is deliberately outside the300m reaction zone.
  assert not output.automatic
  if eligible:
    assert output.state=='APPROACHING' and output.distance==pytest.approx(500,abs=.01)
    assert lmd.speedCameraEventId != lmd.speedCameraControlEventId


@pytest.mark.parametrize('vision_curve_speed',[None,12.,29.])
def test_longitudinal_real_min_arbitration_and_publish(pipeline,longitudinal,plan_bus,vision_curve_speed):
  write_camera(pipeline[0],'18059',True)
  planner=longitudinal(vision=vision_curve_speed is not None)
  rate=.001 if vision_curve_speed is None else 2*USER_SPEED/vision_curve_speed**2
  run_longitudinal(planner,publish_frame(pipeline,model_yaw_rate=rate),plan_bus)
  target,published=run_longitudinal(planner,publish_frame(pipeline,model_yaw_rate=rate),plan_bus)
  candidates=[USER_SPEED,planner.scc.vision.output_v_target,planner.scc.map.output_v_target,planner.sla.output_v_target]
  assert target[0]==pytest.approx(min(candidates))
  assert planner.sla.output_v_target==pytest.approx(LIMIT)
  if vision_curve_speed==12.: assert published.longitudinalPlanSource=='sccVision'
  else: assert published.longitudinalPlanSource=='speedLimitAssist'
  camera=published.speedCameraControl
  assert camera.enabled and camera.active and camera.state=='active'
  assert camera.speedCap==pytest.approx(LIMIT) and camera.rawLimit==pytest.approx(LIMIT)


def test_longitudinal_hold_manual_and_disabled(pipeline,longitudinal,plan_bus,native_params):
  write_camera(pipeline[0],'18950',True)
  planner=longitudinal()
  target,_=run_longitudinal(planner,publish_frame(pipeline),plan_bus)
  _,held=run_longitudinal(planner,publish_frame(pipeline,gps_ok=False),plan_bus)
  assert held.speedCameraControl.active and held.speedCameraControl.speedCap==pytest.approx(target[0])
  assert not held.speedCameraControl.countdownValid
  _,manual=run_longitudinal(planner,publish_frame(pipeline,buttons=(('accelCruise',True),)),plan_bus)
  assert manual.speedCameraControl.manualTakeover and not manual.speedCameraControl.active
  native_params.put_bool('SpeedCameraControlEnabled',False)
  planner.resolver.frame=0
  _,disabled=run_longitudinal(planner,publish_frame(pipeline),plan_bus)
  assert not disabled.speedCameraControl.enabled and disabled.speedCameraControl.state=='inactive'


@pytest.mark.parametrize('cruise_kph',[108.,70.])
@pytest.mark.parametrize('raw_kph',[20.,60.])
def test_longitudinal_native_offset_preserves_raw_and_driver(pipeline,longitudinal,plan_bus,native_params,cruise_kph,raw_kph):
  native_params.put('SpeedLimitOffsetType',1)
  native_params.put('SpeedLimitValueOffset',15)
  write_camera(pipeline[0],'18059',True,speed_kph=raw_kph)
  planner=longitudinal()
  cruise=cruise_kph/3.6
  target,published=run_longitudinal(planner,publish_frame(pipeline,user_cruise=cruise,stock_speed=cruise),plan_bus,user_cruise=cruise)
  assert target[0]==pytest.approx(min(cruise,(raw_kph+15)/3.6))
  assert published.speedCameraControl.rawLimit==pytest.approx(raw_kph/3.6)
  assert published.speedCameraControl.appliedOffset==pytest.approx(15/3.6)
  assert published.speedCameraControl.driverSetSpeed==pytest.approx(cruise)
