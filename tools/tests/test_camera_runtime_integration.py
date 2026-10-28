"""Opt-in, disposable Linux runtime smoke using native msgq and Params.

Run only inside the isolated runtime container with PQ35_RUNTIME_SMOKE=1 and
PARAMS_ROOT=/data/params. Uses OsmMapData.tick -> BaseMapData.publish -> real
msgq -> SubMaster decoded messages -> SpeedCameraPlanner/LongitudinalPlannerSP.
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
from openpilot.sunnypilot.mapd.camera_database import CameraDatabase, save_database
from openpilot.sunnypilot.mapd.camera_updater import CameraParams
from openpilot.sunnypilot.mapd.cityguide import parse_cityguide_snapshot
from openpilot.sunnypilot.mapd.live_map_data.base_map_data import BaseMapData
from openpilot.sunnypilot.mapd.live_map_data.osm_map_data import OsmMapData
from openpilot.sunnypilot.selfdrive.controls.lib.speed_camera_planner import SpeedCameraPlanner
from openpilot.sunnypilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlannerSP
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import Mode


LAT, LON = 55.0, 37.0
USER_SPEED = 30.0
LIMIT = 60 / 3.6
EGO_LAT = LAT - math.degrees(100 / 6_371_000.0)
INPUT_SERVICES = ['carState', 'carControl', 'gpsLocation', 'controlsState',
                  'carStateSP', 'modelV2', 'radarState', 'selfdriveState']


@pytest.fixture
def native_params():
  assert os.environ.get('PARAMS_ROOT') == '/data/params'
  assert 'OPENPILOT_PREFIX' not in os.environ
  assert 'CEREAL_FAKE' not in os.environ and 'ZMQ' not in os.environ
  params = Params()
  params.put_bool('SpeedCameraEnabled', True)
  params.put_bool('SpeedCameraControlEnabled', True)
  params.put('SpeedCameraAlertDistanceMeters', '1000')
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
def pipeline(native_params, tmp_path):
  cache = tmp_path / 'cameras.json'
  native_params.put('SpeedCameraDatabasePath', str(cache))
  publisher = messaging.PubMaster(['liveLocationKalman', *INPUT_SERVICES])
  map_data = OsmMapData()
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
  location = messaging.new_message('liveLocationKalman', valid=True)
  llk = location.liveLocationKalman
  llk.status = 'valid'
  llk.gpsOK = gps_ok
  llk.positionGeodetic.valid = True
  llk.positionGeodetic.value = [ego_lat, LON, 0.0]
  llk.calibratedOrientationNED.valid = True
  llk.calibratedOrientationNED.value = [0.0, 0.0, 0.0]
  publisher.send('liveLocationKalman', location)
  receive(map_data.sm, ['liveLocationKalman'])
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
  gps_msg = messaging.new_message('gpsLocation', valid=True)
  gps = gps_msg.gpsLocation
  gps.hasFix = True
  gps.horizontalAccuracy = 3.0
  gps.bearingAccuracyDeg = 2.0
  gps.unixTimestampMillis = int((time.time() if gps_time_s is None else gps_time_s) * 1000)  # noqa: TID251 - GPS Unix timestamp
  gps.latitude, gps.longitude, gps.bearingDeg = ego_lat, LON, 0.0
  publisher.send('gpsLocation', gps_msg)
  for service in ('controlsState', 'carStateSP', 'radarState', 'selfdriveState'):
    publisher.send(service, messaging.new_message(service, valid=True))
  model_msg = messaging.new_message('modelV2', valid=True)
  model_msg.modelV2.position.x = [float(i * 5) for i in range(33)]
  model_msg.modelV2.orientation.x = [0.0] * 33
  model_msg.modelV2.orientationRate.z = [model_yaw_rate] * 33
  model_msg.modelV2.velocity.x = [USER_SPEED] * 33
  publisher.send('modelV2', model_msg)
  receive(receiver, receiver.services)
  return receiver


def plan(planner, sm):
  return planner.update(
    sm['carState'], sm['carControl'], sm['gpsLocation'], sm['liveMapDataSP'],
    enabled=True, gps_valid=sm.valid['gpsLocation'], gps_alive=sm.alive['gpsLocation'],
    lmd_valid=sm.valid['liveMapDataSP'], lmd_alive=sm.alive['liveMapDataSP'],
    lmd_age_s=time.monotonic() - sm.recv_time['liveMapDataSP'],
    lmd_generation=sm.recv_time['liveMapDataSP'], now_s=time.time(),  # noqa: TID251 - compare Unix GPS fixes
    user_cruise_mps=USER_SPEED, minimum_speed_mps=30 / 3.6, adapt_accel_mps2=-1.0)


def write_camera(cache, kind, trusted):
  metadata = '|1705|180|1713|1' if trusted else ''
  raw = f'2|Radars|65001\n{kind}|Synthetic runtime fixture|{LAT}|{LON}|1709|60{metadata}\n'
  cameras = tuple(parse_cityguide_snapshot(raw))
  assert len(cameras) == 1
  save_database(str(cache), CameraDatabase('runtime-fixture', time.time(), cameras))  # noqa: TID251 - persisted cache Unix time


@pytest.mark.parametrize('kind,trusted,allowed', [
  ('18059', True, True), ('18950', True, True), ('18958', True, True),
  ('18059', False, False), ('99999', True, False),
])
def test_production_map_publish_and_decoded_planner(pipeline, kind, trusted, allowed):
  write_camera(pipeline[0], kind, trusted)
  sm = publish_frame(pipeline)
  lmd = sm['liveMapDataSP']
  assert sm.valid['liveMapDataSP'] and sm.alive['liveMapDataSP']
  assert lmd.locationValid and lmd.speedCameraAheadValid
  assert lmd.speedCameraLatitude == LAT and lmd.speedCameraLongitude == LON
  assert lmd.speedCameraSpeedLimit == pytest.approx(LIMIT)
  assert lmd.speedCameraType == f'speedcamonline-cg77:{kind}'
  assert lmd.speedCameraAutoControlAllowed is allowed
  output = plan(SpeedCameraPlanner(), sm)
  if allowed:
    assert output.state.value == 'approach'
    assert output.speed_cap_mps == pytest.approx(math.sqrt(LIMIT ** 2 + 200), abs=1e-5)
  else:
    assert output.speed_cap_mps is None and output.reason == 'camera_missing'


def test_production_invalid_map_holds_then_decoded_gas_takes_over(pipeline):
  write_camera(pipeline[0], '18059', True)
  planner = SpeedCameraPlanner()
  initial = plan(planner, publish_frame(pipeline))
  assert initial.speed_cap_mps is not None
  sm = publish_frame(pipeline, gps_ok=False)
  assert not sm.valid['liveMapDataSP'] and not sm['liveMapDataSP'].locationValid
  held = plan(planner, sm)
  assert held.speed_cap_mps == initial.speed_cap_mps
  assert held.reason == 'lmd_unreliable' and not held.restore_allowed
  manual = plan(planner, publish_frame(pipeline, gas=True))
  assert manual.manual_takeover and manual.reason == 'gas'
  assert manual.speed_cap_mps is None and not manual.restore_allowed
  assert manual.state.value == 'manual' and planner.controller.episode_active


@pytest.mark.parametrize('eligible', [False, True])
def test_production_nearest_warning_does_not_mask_independent_control_candidate(pipeline, eligible):
  next_lat = LAT + math.degrees(400 / 6_371_000)
  raw = f'2|Radars|65001\n18952|warning 100m|{LAT}|{LON}|1709|60|\n'
  if eligible:
    raw += f'18059|control 500m|{next_lat}|{LON}|1709|60|1705|180|1713|1|\n'
  cameras = tuple(parse_cityguide_snapshot(raw))
  save_database(str(pipeline[0]), CameraDatabase('runtime-selection', time.time(), cameras))  # noqa: TID251 - persisted cache Unix time
  sm = publish_frame(pipeline)
  lmd = sm['liveMapDataSP']
  assert lmd.speedCameraAheadValid and lmd.speedCameraType == 'speedcamonline-cg77:18952'
  assert lmd.speedCameraAheadDistance == pytest.approx(100, abs=0.001)
  assert lmd.speedCameraControlVersion == 1 and lmd.speedCameraControlValid is eligible
  output = plan(SpeedCameraPlanner(), sm)
  assert (output.speed_cap_mps is not None) is eligible
  if eligible:
    assert lmd.speedCameraControlDistance == pytest.approx(500, abs=0.001)
    assert lmd.speedCameraControlSpeedLimit == pytest.approx(LIMIT)
    assert output.camera_id == f'{next_lat:.7f},{LON:.7f}:speedcamonline-cg77:18059'


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
  planner.camera.observe_car_state(sm['carState'], time.time() if now_s is None else now_s)  # noqa: TID251 - latch shares Unix GPS clock
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


@pytest.mark.parametrize('vision_curve_speed', [None, 19.0, 29.0])
def test_longitudinal_real_min_arbitration_and_publish(pipeline, longitudinal, plan_bus, vision_curve_speed):
  write_camera(pipeline[0], '18059', True)
  planner = longitudinal(vision=vision_curve_speed is not None)
  # Synthetic model geometry goes through the real vision-controller state machine.
  rate = 0.001 if vision_curve_speed is None else 2 * USER_SPEED / vision_curve_speed ** 2
  run_longitudinal(planner, publish_frame(pipeline, model_yaw_rate=rate), plan_bus)
  time.sleep(0.05)
  sm = publish_frame(pipeline, model_yaw_rate=rate)
  target, published = run_longitudinal(planner, sm, plan_bus)
  cap = planner.camera.output.speed_cap_mps
  assert cap == pytest.approx(math.sqrt(LIMIT ** 2 + 200), abs=1e-5)
  candidates = [USER_SPEED, planner.scc.vision.output_v_target,
                planner.scc.map.output_v_target, planner.sla.output_v_target]
  assert target[0] == pytest.approx(min(*candidates, cap))
  if vision_curve_speed == 19.0:
    assert planner.scc.vision.is_active
    assert planner.scc.vision.output_v_target < cap
    assert published.longitudinalPlanSource == 'sccVision'
  else:
    assert min(candidates) > cap
    assert published.longitudinalPlanSource == 'speedLimitAssist'
  camera = published.speedCameraControl
  assert camera.enabled and camera.active and not camera.manualTakeover
  assert camera.speedCap == pytest.approx(cap)
  assert camera.state == 'approach' and camera.reason == 'kinematic_ceiling'
  assert sm['carState'].vCruiseCluster == pytest.approx(USER_SPEED * 3.6)


def test_longitudinal_published_hold_and_manual_takeover(pipeline, longitudinal, plan_bus):
  write_camera(pipeline[0], '18950', True)
  planner = longitudinal()
  initial_target, _ = run_longitudinal(planner, publish_frame(pipeline), plan_bus)
  _, held = run_longitudinal(planner, publish_frame(pipeline, gps_ok=False), plan_bus)
  assert held.speedCameraControl.active and held.speedCameraControl.state == 'hold'
  assert held.speedCameraControl.reason == 'lmd_unreliable'
  assert held.speedCameraControl.speedCap == pytest.approx(initial_target[0])
  target, manual = run_longitudinal(planner, publish_frame(pipeline, gas=True, stock_speed=18.0), plan_bus)
  assert target[0] == 18.0
  assert manual.speedCameraControl.manualTakeover and not manual.speedCameraControl.active
  assert manual.speedCameraControl.state == 'manual' and manual.speedCameraControl.reason == 'gas'
  assert manual.speedCameraControl.speedCap == 0.0
  assert planner.camera.controller.episode_active


def test_longitudinal_disabled_camera_publishes_inactive(pipeline, longitudinal, plan_bus, native_params):
  write_camera(pipeline[0], '18958', True)
  native_params.put_bool('SpeedCameraControlEnabled', False)
  planner = longitudinal()
  target, published = run_longitudinal(planner, publish_frame(pipeline), plan_bus)
  assert target[0] == USER_SPEED and published.longitudinalPlanSource == 'cruise'
  camera = published.speedCameraControl
  assert not camera.enabled and not camera.active and not camera.manualTakeover
  assert camera.speedCap == 0.0 and camera.state == 'inactive' and camera.reason == 'disabled'


@pytest.mark.parametrize('cruise_kph', [108.0, 70.0])
def test_longitudinal_native_offset_preserves_raw_limit_and_user_cruise(pipeline, longitudinal, plan_bus, native_params, cruise_kph):
  key = 'SpeedCameraSpeedOffsetKph'
  previous_offset = native_params.get(key)
  native_params.put(key, '19')
  try:
    assert Params().get(key) == '19'
    write_camera(pipeline[0], '18059', True)
    planner = longitudinal()
    cruise = cruise_kph / 3.6
    sm = publish_frame(pipeline, stock_speed=cruise, user_cruise=cruise)
    target, published = run_longitudinal(planner, sm, plan_bus, user_cruise=cruise)
    assert planner.camera_speed_offset_kph == 19.0
    lmd = sm['liveMapDataSP']
    assert lmd.speedCameraControlVersion == 1 and lmd.speedCameraControlValid
    assert lmd.speedCameraSpeedLimit == pytest.approx(60 / 3.6)
    assert lmd.speedCameraControlSpeedLimit == pytest.approx(60 / 3.6)
    assert lmd.speedCameraControlDistance == pytest.approx(100, abs=0.001)
    assert sm['carState'].vCruiseCluster == pytest.approx(cruise_kph)
    camera = published.speedCameraControl
    assert camera.enabled and not camera.manualTakeover
    if cruise_kph == 108.0:
      # At 100 m and the existing -1 m/s^2, 60 + 19 km/h sets this ceiling.
      expected = math.sqrt((79 / 3.6) ** 2 + 200)
      assert target[0] == pytest.approx(expected, abs=1e-5)
      assert published.vTarget == pytest.approx(expected, abs=1e-5)
      assert camera.active and camera.speedCap == pytest.approx(expected, abs=1e-5)
      assert camera.state == 'approach' and camera.reason == 'kinematic_ceiling'
      assert published.longitudinalPlanSource == 'speedLimitAssist'
    else:
      assert target[0] == pytest.approx(cruise) and published.vTarget == pytest.approx(cruise)
      assert published.longitudinalPlanSource == 'cruise'
      assert not camera.active and camera.speedCap == 0.0
      assert camera.state == 'inactive' and camera.reason == 'below_camera_limit'
  finally:
    if previous_offset is None:
      native_params.remove(key)
    else:
      native_params.put(key, previous_offset)
