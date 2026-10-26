"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import json
import math
import platform

import pytest

from cereal import custom
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.car.cruise import V_CRUISE_UNSET
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.map_controller import SmartCruiseControlMap

MapState = VisionState = custom.LongitudinalPlanSP.SmartCruiseControl.MapState


class TestSmartCruiseControlMap:

  def setup_method(self):
    self.params = Params()
    self.mem_params = Params("/dev/shm/params") if platform.system() != "Darwin" else self.params
    self.reset_params()
    self.scc_m = SmartCruiseControlMap()

  def reset_params(self):
    self.params.put_bool("SmartCruiseControlMap", True)

    # TODO-SP: mock data from gpsLocation
    self.params.put("LastGPSPosition", "{}")
    self.params.put("MapTargetVelocities", "{}")

  def test_initial_state(self):
    assert self.scc_m.state == VisionState.disabled
    assert not self.scc_m.is_active
    assert self.scc_m.output_v_target == V_CRUISE_UNSET
    assert self.scc_m.output_a_target == 0.

  def test_system_disabled(self):
    self.params.put_bool("SmartCruiseControlMap", False)
    self.scc_m.enabled = self.params.get_bool("SmartCruiseControlMap")

    for _ in range(int(10. / DT_MDL)):
      self.scc_m.update(True, False, 0., 0., 0.)
    assert self.scc_m.state == VisionState.disabled
    assert not self.scc_m.is_active

  def test_disabled(self):
    for _ in range(int(10. / DT_MDL)):
      self.scc_m.update(False, False, 0., 0., 0.)
    assert self.scc_m.state == VisionState.disabled

  def test_transition_disabled_to_enabled(self):
    for _ in range(int(10. / DT_MDL)):
      self.scc_m.update(True, False, 0., 0., 0.)
    assert self.scc_m.state == VisionState.enabled

  @pytest.mark.parametrize("acceleration,target_speed,distance,expected_target", [
    (0.0, 19.4, 30.0, 19.4),
    (0.0, 19.4, 60.0, 100.0),
    (-1.1, 19.95, 20.5, 19.95),
    (-1.1, 19.95, 25.0, 100.0),
    (0.5, 19.4, 50.0, 19.4),
    (0.5, 19.4, 80.0, 100.0),
  ])
  def test_jerk_limited_curve_lookahead(self, acceleration, target_speed, distance, expected_target):
    # At 20 m/s with zero initial acceleration, reaching 19.4 m/s at
    # -0.6 m/s^3 takes sqrt(2) seconds and about 28 m, plus the 19.4 m
    # lookahead offset. A curve 30 m ahead must therefore be considered.
    self.mem_params.put("LastGPSPosition", json.dumps({"latitude": 0.0, "longitude": 0.0}))
    self.mem_params.put("MapTargetVelocities", json.dumps([{
      "latitude": math.degrees(distance / 6373000.0),
      "longitude": 0.0,
      "velocity": target_speed,
    }]))
    self.scc_m.v_ego = 20.0
    self.scc_m.a_ego = acceleration

    self.scc_m.update_calculations()

    assert self.scc_m.v_target == expected_target

  # TODO-SP: mock data from modelV2 to test other states
