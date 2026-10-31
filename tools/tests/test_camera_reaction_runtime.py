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
