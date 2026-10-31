"""Explicit disposable Linux: real parameter/IPC reaction-distance boundaries."""
import os
import sys
import time

import pytest

if sys.platform != 'linux' or os.getenv('PQ35_RUNTIME_SMOKE') != '1':
  pytest.skip('requires explicit disposable Linux runtime opt-in', allow_module_level=True)

from openpilot.sunnypilot.mapd.camera_updater import CameraParams
from test_camera_runtime_integration import (
  native_params as native_params, pipeline as pipeline, longitudinal as longitudinal, plan_bus as plan_bus,
  publish_frame, run_longitudinal, write_camera,
)


@pytest.mark.parametrize('distance',[50,300,900,1000])
def test_native_reaction_setting_reaches_source_adapter_and_wire_at_actual_boundary(
    native_params,pipeline,longitudinal,plan_bus,monkeypatch,distance):
  import openpilot.sunnypilot.mapd.live_map_data.osm_map_data as source_module
  import openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_resolver as resolver_module

  class Clock:
    monotonic = staticmethod(time.monotonic)
    now = time.time()
    def time(self):
      return self.now

  clock = Clock()
  monkeypatch.setattr(source_module,'time',clock)
  monkeypatch.setattr(resolver_module,'time',clock)
  native_params.put('SpeedCameraReactionDistance',distance)
  native_params.put('SpeedCameraAlertDistanceMeters','100')
  assert native_params.get('SpeedCameraReactionDistance') == distance
  assert CameraParams().get('SpeedCameraReactionDistance') == str(distance)
  assert os.path.realpath(native_params.get_param_path()) == os.path.realpath(CameraParams().root)
  write_camera(pipeline[0],'18059',True)
  planner = longitudinal()
  outside = publish_frame(pipeline,north_m=-(distance+5),gps_time_s=clock.now)
  _,outside_plan = run_longitudinal(planner,outside,plan_bus)
  assert outside['liveMapDataSP'].speedCameraControlValid
  assert not outside['liveMapDataSP'].speedCameraAheadValid
  assert pipeline[3].camera_selection.state == 'APPROACHING'
  assert planner.resolver.camera_reaction_distance == distance
  assert outside_plan.speedCameraControl.reactionDistance == distance
  assert not outside_plan.speedCameraControl.active
  assert not outside_plan.speedCameraControl.countdownValid
  assert outside_plan.vTarget == pytest.approx(30.)

  clock.now += 1
  inside = publish_frame(pipeline,north_m=-(distance-5),gps_time_s=clock.now)
  _,inside_plan = run_longitudinal(planner,inside,plan_bus)
  camera = inside_plan.speedCameraControl
  assert pipeline[3].camera_selection.state == 'ACTIVE'
  assert camera.active and camera.countdownValid
  assert camera.reactionDistance == distance
  assert camera.distance == pytest.approx(distance-5,abs=.01)
  assert inside['liveMapDataSP'].speedCameraAheadValid
  assert inside['liveMapDataSP'].speedCameraAheadDistance == pytest.approx(distance-5,abs=.01)
  assert inside_plan.vTarget == pytest.approx(60/3.6)
  assert pipeline[3].camera_alert_distance_m == distance


def test_native_ipc_can_odometry_retires_only_preselected_camera_after_gps_loss(
    native_params,pipeline,monkeypatch):
  """Real PubMaster/SubMaster timestamps and CAN vEgo; no source/IO substitute."""
  import math
  from cereal import messaging
  import openpilot.sunnypilot.mapd.live_map_data.osm_map_data as source_module
  from test_camera_runtime_integration import LAT,LON,receive

  assert not os.path.exists('/TICI')
  write_camera(pipeline[0],'18059',True)
  _,publisher,receiver,map_data = pipeline

  class Clock:
    unix = int(time.time()*1000)*.001
    mono_ns = time.monotonic_ns()
    def time(self):
      return self.unix
    def monotonic(self):
      return self.mono_ns/1_000_000_000

  clock = Clock()
  monkeypatch.setattr(source_module,'time',clock)

  def frame(north,gps_ok,fix_ms,speed=10.):
    state = messaging.new_message('carState',valid=True)
    state.logMonoTime = clock.mono_ns
    state.carState.canValid = True
    state.carState.gearShifter = 'drive'
    state.carState.vEgo = speed
    # An unrelated cruise setpoint must never become integrated distance.
    state.carState.cruiseState.speed = 50.
    gps = messaging.new_message('gpsLocation',valid=True)
    gps.gpsLocation.hasFix = gps_ok
    gps.gpsLocation.latitude = LAT+math.degrees(north/6_371_000)
    gps.gpsLocation.longitude = LON
    gps.gpsLocation.bearingDeg = 0.
    gps.gpsLocation.speed = 10.
    gps.gpsLocation.horizontalAccuracy = 3.
    gps.gpsLocation.bearingAccuracyDeg = 2.
    gps.gpsLocation.unixTimestampMillis = fix_ms
    location = messaging.new_message('liveLocationKalman',valid=True)
    llk = location.liveLocationKalman
    llk.status = 'valid'
    llk.gpsOK = gps_ok
    llk.positionGeodetic.valid = True
    llk.positionGeodetic.value = [gps.gpsLocation.latitude,LON,0.]
    llk.calibratedOrientationNED.valid = True
    llk.calibratedOrientationNED.value = [0.,0.,0.]
    for service,message in [('carState',state),('gpsLocation',gps),('liveLocationKalman',location)]:
      publisher.send(service,message)
    receive(map_data.sm,['carState','gpsLocation','liveLocationKalman'])
    assert map_data.sm.logMonoTime['carState'] == clock.mono_ns
    assert map_data.sm['carState'].canValid and map_data.sm['carState'].vEgo == speed
    map_data.tick()
    receive(receiver,['liveMapDataSP'])
    return receiver['liveMapDataSP']

  fix_ms = int(clock.unix*1000)
  frame(-105,True,fix_ms-1000)
  initial = frame(-100,True,fix_ms)
  identity = initial.speedCameraControlEventId
  assert identity and initial.speedCameraControlValid
  assert initial.speedCameraControlDistance == pytest.approx(100.,abs=.01)
  for step in range(1,12):
    clock.mono_ns += 1_000_000_000
    clock.unix += 1.
    message = frame(-100,False,fix_ms)
    assert message.speedCameraControlEventId == identity
    assert not message.locationValid and receiver.valid['liveMapDataSP']
    if step < 10:
      assert message.speedCameraControlValid and message.speedCameraControlReason == 'odometry_tracking'
      assert message.speedCameraControlDistance == pytest.approx(100-step*10,abs=.01)
    if message.speedCameraControlPassProven:
      assert not message.speedCameraControlValid and not message.speedCameraAheadValid
      assert message.speedCameraControlAutoControlAllowed
      assert message.speedCameraControlState == 'PASSED' and message.speedCameraControlReason == 'odometry_passed'
      break
  else:
    pytest.fail('Fresh actual CAN distance did not retire the selected camera')
