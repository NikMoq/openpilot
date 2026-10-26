"""Opt-in Linux: real cruise helper -> native messages -> planner -> ICBM.

Only the planner's wall-clock input is controlled. Native Params/msgq, Capnp,
cruise helper, controller, planner and sender execute their actual implementations.
As in the shared fixtures, DEC receives a supplied MPC crash counter, not a solver.
"""
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
import sys
import time

import pytest

if sys.platform != 'linux' or os.getenv('PQ35_RUNTIME_SMOKE') != '1':
  pytest.skip('requires explicit disposable Linux runtime opt-in', allow_module_level=True)

from cereal import car, custom
from openpilot.selfdrive.car.cruise import VCruiseHelper
from openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management.controller import IntelligentCruiseButtonManagement
from openpilot.sunnypilot.selfdrive.controls.lib import longitudinal_planner as planner_module
from test_camera_runtime_integration import (
  longitudinal as longitudinal, native_params as native_params, pipeline as pipeline, plan_bus as plan_bus,
  publish_frame, run_longitudinal, write_camera,
)

SendButton = custom.IntelligentCruiseButtonManagement.SendButtonState
SenderState = custom.IntelligentCruiseButtonManagement.IntelligentCruiseButtonManagementState


class PlanningClock:
  """Explicit caller-time boundary; message receipt still uses real monotonic time."""
  monotonic = staticmethod(time.monotonic)

  def __init__(self):
    self.now = time.time()  # noqa: TID251 - seed the controlled Unix GPS clock

  def time(self):
    return self.now

  def advance(self, seconds):
    self.now += seconds


@pytest.fixture
def native_cruise_pair(native_params):
  native_params.put_bool('CustomAccIncrementsEnabled', False)
  with ExitStack() as stack:
    cp = car.CarParams.new_message(brand='volkswagen', pcmCruise=True, openpilotLongitudinalControl=False)
    cp_sp = custom.CarParamsSP.new_message(pcmCruiseSpeed=False, intelligentCruiseButtonManagementAvailable=True)
    cp = stack.enter_context(car.CarParams.from_bytes(cp.to_bytes()))
    cp_sp = stack.enter_context(custom.CarParamsSP.from_bytes(cp_sp.to_bytes()))
    helper = VCruiseHelper(cp, cp_sp)
    sender = IntelligentCruiseButtonManagement(cp, cp_sp)
    assert helper.enable_button_timers is not sender.cruise_button_timers
    assert sender.cruise_button_timers is not IntelligentCruiseButtonManagement(cp, cp_sp).cruise_button_timers
    yield helper, sender


@pytest.fixture
def decoded_sender_inputs():
  with ExitStack() as stack:
    def make(*, target_kph=130, gas=False, brake=False, acc_enabled=True):
      cs = car.CarState.new_message(gasPressed=gas, brakePressed=brake)
      cs.cruiseState.available = True
      cs.cruiseState.enabled = acc_enabled
      cs.cruiseState.speed = cs.cruiseState.speedCluster = 80 / 3.6
      cc = car.CarControl.new_message(enabled=True)
      lp = custom.LongitudinalPlanSP.new_message(vTarget=target_kph / 3.6)
      return tuple(stack.enter_context(schema.from_bytes(message.to_bytes())) for schema, message in
                   ((car.CarState, cs), (car.CarControl, cc), (custom.LongitudinalPlanSP, lp)))
    yield make


@pytest.mark.parametrize('pedal', ['gas', 'brake'])
@pytest.mark.parametrize('target_kph', [60, 130])
def test_native_icbm_pedal_priority_before_cc_override(native_cruise_pair, decoded_sender_inputs, pedal, target_kph):
  _, sender = native_cruise_pair
  active = decoded_sender_inputs(target_kph=target_kph)
  for _ in range(45):
    sender.run(*active, True)
  expected = SendButton.increase if target_kph > 80 else SendButton.decrease
  assert sender.cruise_button == expected
  pressed = decoded_sender_inputs(target_kph=target_kph, **{pedal: True})
  assert not pressed[1].cruiseControl.override  # Deliberately stale carControl.
  sender.run(*pressed, True)
  assert sender.cruise_button == SendButton.none and sender.state == SenderState.inactive
  assert sender.v_target == target_kph


def test_native_icbm_gas_release_obeys_activation_delay(native_cruise_pair, decoded_sender_inputs):
  _, sender = native_cruise_pair
  sender.run(*decoded_sender_inputs(gas=True), True)
  released = decoded_sender_inputs()
  sender.run(*released, True)
  assert sender.state == SenderState.preActive and sender.cruise_button == SendButton.none
  for _ in range(39):
    sender.run(*released, True)
    assert sender.cruise_button == SendButton.none
  sender.run(*released, True)
  assert sender.cruise_button == SendButton.increase and sender.v_target == 130


def test_native_icbm_brake_release_never_reengages_acc(native_cruise_pair, decoded_sender_inputs):
  _, sender = native_cruise_pair
  sender.run(*decoded_sender_inputs(brake=True), True)
  released = decoded_sender_inputs(acc_enabled=False)
  assert released[1].enabled  # Stale CC.enabled cannot override actual disengagement.
  for _ in range(60):
    sender.run(*released, True)
    assert sender.cruise_button == SendButton.none and sender.state == SenderState.inactive
  assert sender.v_target == 130


class OverrideCycle:
  def __init__(self, pipeline, planner, plan_bus, helper, sender, clock):
    self.pipeline, self.planner, self.plan_bus = pipeline, planner, plan_bus
    self.helper, self.sender, self.clock = helper, sender, clock
    self.previous_plan = plan_bus[1]['longitudinalPlanSP']
    self.trace = []
    self.last_fix_ms = None
    self.model_yaw_rate = 0.001

  def step(self, label, *, stock_kph=80, dt=0.05, **physical):
    self.clock.advance(dt)
    sm = publish_frame(self.pipeline, stock_speed=stock_kph / 3.6, gps_time_s=self.clock.time(),
                       cruise_helper=self.helper, previous_plan=self.previous_plan,
                       model_yaw_rate=self.model_yaw_rate, **physical)
    fix_ms = sm['gpsLocation'].unixTimestampMillis
    assert self.last_fix_ms is None or fix_ms > self.last_fix_ms
    self.last_fix_ms = fix_ms
    _, result = run_longitudinal(self.planner, sm, self.plan_bus,
                                 user_cruise=self.helper.v_cruise_kph / 3.6, now_s=self.clock.time())
    self.sender.run(sm['carState'], sm['carControl'], result, True)
    self.previous_plan = result
    self.sm = sm
    camera = result.speedCameraControl
    self.trace.append({'label': label, 'gps_fix_ms': fix_ms, 'base_kph': self.helper.v_cruise_kph,
                       'wire_base_kph': sm['carState'].vCruise, 'stock_kph': sm['carState'].cruiseState.speedCluster * 3.6,
                       'target_kph': result.vTarget * 3.6, 'camera_state': camera.state, 'reason': camera.reason,
                       'manual_takeover': camera.manualTakeover, 'camera_active': camera.active,
                       'sender': str(self.sender.cruise_button)})
    return result

  def assert_base(self, value=130):
    assert self.helper.v_cruise_kph == pytest.approx(value)
    assert self.helper.v_cruise_cluster_kph == pytest.approx(value)
    assert self.sm['carState'].vCruise == pytest.approx(value)
    assert self.sm['carState'].vCruiseCluster == pytest.approx(value)

  def engage(self):
    # Learn 130 from real stock messages, with no direct writes to helper state.
    for i in range(3):
      self.step(f'engage-{i}', stock_kph=130)
      self.assert_base()

  def acquire(self):
    self.engage()
    write_camera(self.pipeline[0], '18059', True)
    for i in range(2):
      result = self.step(f'approach-{i}', stock_kph=79)
      self.assert_base()
      assert result.speedCameraControl.active and result.vTarget < 130 / 3.6
    assert self.helper.camera_episode_active

  def confirm_pass(self, stock_kph, *, wheel_override=False):
    self.step('first-behind-fix', north_m=20, stock_kph=stock_kph)
    pending = self.step('second-behind-fix', north_m=30, stock_kph=stock_kph)
    self.assert_base()
    if wheel_override:
      assert pending.speedCameraControl.state == 'manual'
      assert pending.speedCameraControl.reason == 'manual_button'
      assert pending.speedCameraControl.manualTakeover and pending.speedCameraControl.speedCap == 0.0
      assert pending.vTarget == pytest.approx(stock_kph / 3.6)
    else:
      assert pending.speedCameraControl.reason == 'awaiting_map_update'
    assert pending.vTarget < 130 / 3.6
    restored = self.step('fresh-map-after-pass', north_m=40, stock_kph=stock_kph)
    self.assert_base()
    assert restored.speedCameraControl.state == 'passed'
    assert not restored.speedCameraControl.manualTakeover
    assert restored.vTarget == pytest.approx(130 / 3.6)
    self.step('helper-consumes-passed', north_m=50, stock_kph=stock_kph)
    self.assert_base()
    assert not self.helper.camera_episode_active

  def sender_ticks(self, count):
    # Real 100 Hz sender calls using the most recently decoded model plan.
    for _ in range(count):
      self.sender.run(self.sm['carState'], self.sm['carControl'], self.previous_plan, True)


@pytest.fixture
def override_cycle(pipeline, longitudinal, plan_bus, native_cruise_pair, native_params, monkeypatch, request):
  options = getattr(request, 'param', {})
  previous_offset = native_params.get('SpeedCameraSpeedOffsetKph')
  native_params.put('SpeedCameraSpeedOffsetKph', str(options.get('offset', 0)))
  clock = PlanningClock()
  monkeypatch.setattr(planner_module, 'time', clock)
  helper, sender = native_cruise_pair
  vision_speed = options.get('vision_speed')
  cycle = OverrideCycle(pipeline, longitudinal(pcm_cruise=True, vision=vision_speed is not None), plan_bus, helper, sender, clock)
  if vision_speed is not None:
    cycle.model_yaw_rate = 2 * 30 / vision_speed ** 2
  try:
    yield cycle
  finally:
    if os.getenv('PQ35_RUNTIME_EVIDENCE'):
      directory = Path(os.environ['PQ35_RUNTIME_EVIDENCE'])
      directory.mkdir(parents=True, exist_ok=True)
      (directory / f'{request.node.name}.json').write_text(json.dumps(cycle.trace, indent=2) + '\n')
    if previous_offset is None:
      native_params.remove('SpeedCameraSpeedOffsetKph')
    else:
      native_params.put('SpeedCameraSpeedOffsetKph', previous_offset)


@pytest.mark.parametrize('stock_kph', [89, 80])
@pytest.mark.parametrize('pedal', ['gas', 'brake'])
def test_actual_cruise_planner_icbm_pedal_keeps_130_through_pass(override_cycle, pedal, stock_kph):
  cycle = override_cycle
  cycle.acquire()
  physical = {'gas': True} if pedal == 'gas' else {'brake': True, 'acc_enabled': False, 'cc_enabled': False}
  manual = cycle.step(pedal, stock_kph=stock_kph, **physical)
  cycle.assert_base()
  assert manual.speedCameraControl.manualTakeover and manual.speedCameraControl.reason == pedal
  assert manual.speedCameraControl.state == 'manual' and not manual.speedCameraControl.active
  assert manual.vTarget == pytest.approx(stock_kph / 3.6)
  assert cycle.sender.cruise_button == SendButton.none
  assert cycle.planner.camera.controller.episode_active
  if pedal == 'brake':
    waiting = cycle.step('brake-released-acc-off', stock_kph=stock_kph, dt=0.6, acc_enabled=False, cc_enabled=False)
    cycle.assert_base()
    assert waiting.speedCameraControl.reason == 'brake_waiting_resume'
    assert cycle.sender.cruise_button == SendButton.none
    cycle.step('physical-res', stock_kph=stock_kph, buttons=[('resumeCruise', True)])
    cycle.assert_base()
    cycle.step('physical-res-release', stock_kph=stock_kph, buttons=[('resumeCruise', False)])
  resumed = cycle.step('pedal-priority-finished', stock_kph=stock_kph, dt=0.6)
  cycle.assert_base()
  assert resumed.speedCameraControl.active and not resumed.speedCameraControl.manualTakeover
  cycle.confirm_pass(stock_kph)
  cycle.sender_ticks(45)
  assert cycle.sender.cruise_button == SendButton.increase


@pytest.mark.parametrize('stock_kph', [89, 80])
@pytest.mark.parametrize('button,base_delta', [('accelCruise', 1), ('decelCruise', -1)])
def test_actual_cruise_planner_icbm_wheel_override_is_episode_local(override_cycle, button, base_delta, stock_kph):
  cycle = override_cycle
  cycle.acquire()
  for edge in (True, False):
    manual = cycle.step(f'wheel-{edge}', stock_kph=stock_kph, buttons=[(button, edge)])
    cycle.assert_base()
    assert manual.speedCameraControl.manualTakeover and manual.speedCameraControl.reason == 'manual_button'
    assert manual.vTarget == pytest.approx(stock_kph / 3.6)
  held = cycle.step('wheel-released-still-temporary', stock_kph=stock_kph, dt=0.6)
  cycle.assert_base()
  assert held.speedCameraControl.manualTakeover and held.speedCameraControl.state == 'manual'
  cycle.sender_ticks(45)
  assert cycle.sender.cruise_button == SendButton.none
  cycle.confirm_pass(stock_kph, wheel_override=True)
  cycle.step('stock-return-completed', north_m=55, stock_kph=130, dt=0.6)
  cycle.assert_base()
  cycle.step('outside-episode-press', north_m=60, stock_kph=130, buttons=[(button, True)])
  cycle.step('outside-episode-release', north_m=65, stock_kph=130, buttons=[(button, False)])
  cycle.assert_base(130 + base_delta)


def test_actual_held_wheel_across_pass_waits_for_release_and_sender_delay(override_cycle):
  cycle = override_cycle
  cycle.acquire()
  cycle.step('hold-wheel-press', stock_kph=89, buttons=[('accelCruise', True)])
  cycle.step('held-beyond-planner-latch', stock_kph=89, dt=0.8)
  for north in (20, 30, 40):
    held = cycle.step(f'wheel-held-past-camera-{north}m', north_m=north, stock_kph=89)
    cycle.assert_base()
    assert held.speedCameraControl.state == 'manual' and held.speedCameraControl.manualTakeover
    assert cycle.sender.cruise_button == SendButton.none
  # Keep the original physical press held past the real helper long-press timer.
  for _ in range(60):
    cycle.clock.advance(0.01)
    cycle.helper.update_speed_limit_assist(True, cycle.previous_plan)
    cycle.helper.update_v_cruise(cycle.sm['carState'], cycle.sm['carControl'].enabled, True)
    cycle.sender_ticks(1)
    cycle.assert_base()
    assert cycle.sender.cruise_button == SendButton.none
  released = cycle.step('held-wheel-release-after-pass', stock_kph=89, north_m=60, buttons=[('accelCruise', False)])
  cycle.assert_base()
  following = cycle.step('frame-after-held-wheel-release', stock_kph=89, north_m=65)
  cycle.assert_base()
  for result in (released, following):
    assert result.speedCameraControl.state == 'manual' and result.speedCameraControl.manualTakeover
    assert result.speedCameraControl.reason == 'manual_button'
  assert cycle.sender.cruise_button == SendButton.none
  barrier = cycle.step('held-release-pass-barrier', stock_kph=89, north_m=70, dt=0.6)
  cycle.assert_base()
  assert barrier.speedCameraControl.state == 'manual' and barrier.speedCameraControl.reason == 'manual_button'
  assert cycle.sender.cruise_button == SendButton.none
  passed = cycle.step('held-release-new-map', stock_kph=89, north_m=75)
  cycle.assert_base()
  assert passed.speedCameraControl.state == 'passed' and not passed.speedCameraControl.manualTakeover
  assert passed.vTarget == pytest.approx(130 / 3.6)
  assert cycle.sender.state == SenderState.preActive and cycle.sender.cruise_button == SendButton.none
  for _ in range(39):
    cycle.sender_ticks(1)
    assert cycle.sender.cruise_button == SendButton.none
  cycle.sender_ticks(1)
  assert cycle.sender.cruise_button == SendButton.increase and cycle.sender.v_target == 130
  following = cycle.step('frame-after-held-release-restoration', stock_kph=89, north_m=80)
  cycle.assert_base()
  assert not following.speedCameraControl.manualTakeover and following.vTarget == pytest.approx(130 / 3.6)


def test_actual_new_wheel_input_during_unfinished_return_hands_over_stock(override_cycle):
  cycle = override_cycle
  cycle.acquire()
  cycle.confirm_pass(89)
  manual = cycle.step('new-input-during-return', north_m=60, stock_kph=89, buttons=[('accelCruise', True)], dt=0.6)
  assert manual.speedCameraControl.state == 'cancelled' and manual.speedCameraControl.manualTakeover
  assert manual.vTarget == pytest.approx(89 / 3.6)
  cycle.step('new-input-during-return-release', north_m=65, stock_kph=89, buttons=[('accelCruise', False)])
  cycle.assert_base(89)


@pytest.mark.parametrize('override_cycle,stock_kph', [
  pytest.param({}, 140, id='manual-stock-above-base'),
  pytest.param({'vision_speed': 19.0}, 89, id='manual-stock-above-lower-constraint'),
], indirect=['override_cycle'])
def test_actual_manual_wheel_sender_never_fights_stock_feedback(override_cycle, stock_kph):
  cycle = override_cycle
  cycle.acquire()
  cycle.step('manual-authority-press', stock_kph=stock_kph, buttons=[('accelCruise', True)])
  cycle.step('manual-authority-release', stock_kph=stock_kph, buttons=[('accelCruise', False)])
  result = cycle.step('manual-authority-no-held-button', stock_kph=stock_kph, dt=0.6)
  cycle.assert_base()
  assert result.speedCameraControl.state == 'manual' and result.speedCameraControl.manualTakeover
  assert result.vTarget < stock_kph / 3.6
  if stock_kph == 140:
    assert result.vTarget == pytest.approx(130 / 3.6)
  else:
    assert cycle.planner.scc.vision.is_active and result.longitudinalPlanSource == 'sccVision'
    assert result.vTarget == pytest.approx(cycle.planner.scc.vision.output_v_target)
  cycle.sender_ticks(45)
  assert cycle.sender.cruise_button == SendButton.none and cycle.sender.state == SenderState.inactive


def test_actual_stock_set_after_brake_outside_camera_replaces_base_and_allows_first_camera(override_cycle):
  cycle = override_cycle
  cycle.engage()
  assert not cycle.helper.camera_episode_active
  cycle.step('non-camera-brake', stock_kph=79, brake=True, cc_enabled=False, acc_enabled=False)
  cycle.step('non-camera-brake-release', stock_kph=79, cc_enabled=False, acc_enabled=False, dt=0.6)
  cycle.assert_base()
  cycle.step('physical-set-off', stock_kph=79, buttons=[('setCruise', True)], cc_enabled=False, acc_enabled=False)
  cycle.step('physical-set-reengagement', stock_kph=79, buttons=[('setCruise', False)])
  following = cycle.step('following-stock-set-frame', stock_kph=79, dt=0.6)
  cycle.assert_base(79)
  assert following.vTarget == pytest.approx(79 / 3.6)
  cycle.sender_ticks(45)
  assert cycle.sender.cruise_button == SendButton.none
  write_camera(cycle.pipeline[0], '18059', True)
  first_camera = cycle.step('first-camera-after-ordinary-set', stock_kph=79, north_m=-10)
  cycle.assert_base(79)
  assert first_camera.speedCameraControl.active and not first_camera.speedCameraControl.manualTakeover
  assert first_camera.speedCameraControl.state == 'approach'
  assert first_camera.speedCameraControl.reason == 'kinematic_ceiling'
  assert first_camera.vTarget < 79 / 3.6


@pytest.mark.parametrize('override_cycle', [
  pytest.param({'offset': 19}, id='offset19'),
  pytest.param({'offset': 19, 'vision_speed': 19.0}, id='offset19-lower-vision'),
], indirect=True)
def test_actual_gas_beyond_pass_window_keeps_130_offset_and_lower_constraint(override_cycle):
  cycle = override_cycle
  cycle.acquire()
  assert cycle.planner.camera_speed_offset_kph == 19
  assert cycle.sm['liveMapDataSP'].speedCameraControlSpeedLimit == pytest.approx(60 / 3.6)
  initial = cycle.previous_plan
  assert initial.speedCameraControl.speedCap == pytest.approx(math.sqrt((79 / 3.6) ** 2 + 200), abs=1e-5)
  lower_constraint = cycle.model_yaw_rate > 0.001
  if lower_constraint:
    assert cycle.planner.scc.vision.is_active
    assert initial.vTarget == pytest.approx(cycle.planner.scc.vision.output_v_target)
    assert initial.vTarget < initial.speedCameraControl.speedCap
    assert initial.longitudinalPlanSource == 'sccVision'
  for north in (-5, 20, 40, 400, 1000):
    manual = cycle.step(f'gas-held-{north}m', north_m=north, stock_kph=89, gas=True, cc_override=True, dt=0.6)
    cycle.assert_base()
    assert manual.speedCameraControl.state == 'manual' and manual.speedCameraControl.reason == 'gas'
    assert manual.speedCameraControl.manualTakeover and not manual.speedCameraControl.active
    assert cycle.sender.cruise_button == SendButton.none
    assert cycle.planner.camera.controller.episode_active
  barrier = cycle.step('gas-released-beyond-250m', north_m=1200, stock_kph=89, dt=0.6)
  cycle.assert_base()
  assert barrier.speedCameraControl.reason == 'awaiting_map_update'
  assert barrier.speedCameraControl.speedCap == pytest.approx(79 / 3.6)
  assert barrier.vTarget <= barrier.speedCameraControl.speedCap
  passed = cycle.step('fresh-map-after-long-gas', north_m=1300, stock_kph=89)
  cycle.assert_base()
  assert passed.speedCameraControl.state == 'passed' and not passed.speedCameraControl.manualTakeover
  if lower_constraint:
    assert cycle.planner.scc.vision.is_active
    assert passed.longitudinalPlanSource == 'sccVision'
    assert passed.vTarget == pytest.approx(cycle.planner.scc.vision.output_v_target)
    assert passed.vTarget < 79 / 3.6
  else:
    assert passed.vTarget == pytest.approx(130 / 3.6)
