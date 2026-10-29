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
      # Explicit remembered driver base; target_kph may be a lower camera
      # constraint. Capnp's default zero means uninitialized, not 130 kph.
      cs = car.CarState.new_message(gasPressed=gas, brakePressed=brake, vCruise=130, vCruiseCluster=130)
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
  def __init__(self,pipeline,planner,plan_bus,helper,sender,clock):
    self.pipeline,self.planner,self.plan_bus=pipeline,planner,plan_bus
    self.helper,self.sender,self.clock=helper,sender,clock
    self.last=None
    helper.v_cruise_kph=helper.v_cruise_cluster_kph=130.
    helper._stock_virtual_cruise_initialized=helper.enabled_prev=True

  def frame(self,*,dt=1.,north_m=-100,stock_kph=75.,**options):
    self.clock.advance(dt)
    sm=publish_frame(self.pipeline,stock_speed=stock_kph/3.6,gps_time_s=self.clock.time(),
      user_cruise=130/3.6,north_m=north_m,cruise_helper=self.helper if self.last is not None else None,
      previous_plan=self.last,**options)
    _,self.last=run_longitudinal(self.planner,sm,self.plan_bus,user_cruise=sm['carState'].vCruise/3.6)
    self.sender.run(sm['carState'],sm['carControl'],self.last,True)
    return sm,self.last

  def pass_camera(self,**options):
    self.frame(dt=2,north_m=-40,**options)
    self.frame(dt=2,north_m=25,**options)
    return self.frame(dt=1,north_m=30,**options)


@pytest.fixture
def override_cycle(pipeline,longitudinal,plan_bus,native_cruise_pair,native_params,monkeypatch):
  import openpilot.sunnypilot.mapd.live_map_data.osm_map_data as producer_module
  import openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_resolver as resolver_module
  native_params.put('SpeedLimitOffsetType',1)
  native_params.put('SpeedLimitValueOffset',15)
  write_camera(pipeline[0],'18059',True)
  clock=PlanningClock()
  # Explicit wall-clock boundary only. Params,msgq,SubMaster,source,Resolver,
  # SLA,planner,helper,ICBM all execute their actual implementations.
  for module in (producer_module,resolver_module,planner_module): monkeypatch.setattr(module,'time',clock)
  helper,sender=native_cruise_pair
  yield OverrideCycle(pipeline,longitudinal(pcm_cruise=True),plan_bus,helper,sender,clock)


@pytest.mark.parametrize('pedal,stock_kph',[('gas',68.),('gas',90.),('brake',68.),('brake',90.)])
def test_actual_pedal_priority_retains_driver_and_safe_resume(override_cycle,pedal,stock_kph):
  c=override_cycle
  _,initial=c.frame()
  assert initial.vTarget==pytest.approx(75/3.6)
  options={pedal:True}
  if pedal=='brake': options.update(cc_enabled=False,acc_enabled=False)
  sm,pressed=c.frame(stock_kph=stock_kph,**options)
  assert c.sender.cruise_button==SendButton.none
  assert c.helper.v_cruise_kph==pytest.approx(130)
  if pedal=='brake':
    for _ in range(3):
      c.frame(stock_kph=stock_kph,cc_enabled=False,acc_enabled=False)
      assert c.sender.cruise_button==SendButton.none
  else:
    _,released=c.frame(stock_kph=stock_kph)
    assert released.vTarget==pytest.approx(75/3.6)
  assert pressed.speedCameraControl.driverSetSpeed==pytest.approx(130/3.6)


@pytest.mark.parametrize('button,corrected',[('accelCruise',85.),('decelCruise',65.)])
def test_actual_manual_setting_is_event_local_and_survives_pass(override_cycle,button,corrected):
  c=override_cycle
  c.frame()
  c.frame(buttons=((button,True),))
  sm,manual=c.frame(buttons=((button,False),),stock_kph=corrected)
  assert manual.speedCameraControl.manualTakeover
  assert c.helper.v_cruise_kph==pytest.approx(corrected)
  _,passed=c.pass_camera(stock_kph=corrected)
  assert passed.speedCameraControl.state=='passed'
  _,after=c.frame(north_m=35,stock_kph=corrected)
  assert after.vTarget==pytest.approx(corrected/3.6)
  assert c.helper.v_cruise_kph==pytest.approx(corrected)


def test_actual_delayed_driver_feedback_is_not_replaced_by_old_native_value(override_cycle):
  c=override_cycle
  c.frame()
  c.frame(buttons=(('accelCruise',True),))
  c.frame(buttons=(('accelCruise',False),))
  for _ in range(30):
    _,pending=c.frame(dt=.05)
    assert c.helper.v_cruise_kph==pytest.approx(130)
    assert c.sender.cruise_button==SendButton.none
  _,accepted=c.frame(stock_kph=85)
  assert c.helper.v_cruise_kph==pytest.approx(85)
  assert accepted.speedCameraControl.manualTakeover


def test_actual_gps_loss_holds_applied_native_offset_without_release(override_cycle,native_params):
  c=override_cycle
  _,initial=c.frame()
  native_params.put('SpeedLimitValueOffset',30)
  c.planner.resolver.frame=0
  _,held=c.frame(gps_ok=False)
  assert held.vTarget==pytest.approx(initial.vTarget)
  assert held.speedCameraControl.active and not held.speedCameraControl.countdownValid
  assert held.speedCameraControl.appliedOffset==pytest.approx(15/3.6)
  assert held.speedCameraControl.state!='passed'


def test_actual_pass_restores_base_and_next_camera_has_independent_cycle(override_cycle):
  c=override_cycle
  c.frame()
  _,passed=c.pass_camera()
  assert passed.speedCameraControl.state=='passed'
  assert passed.vTarget==pytest.approx(130/3.6)
  # A separate same-limit camera uses its coordinates/source identity.
  from openpilot.sunnypilot.mapd.camera_database import CameraDatabase,save_database
  from openpilot.sunnypilot.mapd.cityguide import parse_cityguide_snapshot
  latitude=55+math.degrees(200/6371000)
  rows=f'2|Radars|65001\n18059|second|{latitude}|37|1709|60|1705|180|1713|1|\n'
  cameras=tuple(parse_cityguide_snapshot(rows))
  save_database(str(c.pipeline[0]),CameraDatabase('second-runtime',c.clock.time(),cameras))
  _,next_camera=c.frame(north_m=40)
  assert next_camera.speedCameraControl.active
  assert next_camera.speedCameraControl.eventId!=passed.speedCameraControl.eventId
  assert next_camera.vTarget==pytest.approx(75/3.6)


def test_actual_held_button_across_pass_waits_for_native_release_response(override_cycle):
  c=override_cycle
  c.frame()
  c.frame(buttons=(('accelCruise',True),))
  _,pending=c.pass_camera()
  assert pending.speedCameraControl.manualTakeover
  assert c.helper.v_cruise_kph==pytest.approx(130)
  assert c.sender.cruise_button==SendButton.none
  _,released=c.frame(north_m=35,stock_kph=85,buttons=(('accelCruise',False),))
  assert c.helper.v_cruise_kph==pytest.approx(85)
  assert c.sender.cruise_button==SendButton.none
  _,settled=c.frame(north_m=40,stock_kph=85)
  assert settled.vTarget==pytest.approx(85/3.6)


def test_actual_stock_set_after_brake_outside_camera_replaces_driver_base(override_cycle):
  c=override_cycle
  c.frame()
  c.pass_camera(brake=True,cc_enabled=False,acc_enabled=False)
  c.frame(north_m=35,stock_kph=75,cc_enabled=False,acc_enabled=False)
  c.frame(north_m=40,stock_kph=75,buttons=(('setCruise',True),),cc_enabled=False,acc_enabled=False)
  _,engaged=c.frame(north_m=45,stock_kph=95,buttons=(('setCruise',False),))
  assert c.helper.v_cruise_kph==pytest.approx(95)
  assert engaged.vTarget==pytest.approx(95/3.6)


def test_actual_native_raw_twenty_plus_fifteen_targets_thirty_five(override_cycle):
  c=override_cycle
  write_camera(c.pipeline[0],'18059',True,speed_kph=20)
  sm,result=c.frame(stock_kph=75)
  assert sm['liveMapDataSP'].speedCameraControlValid
  assert result.speedCameraControl.rawLimit==pytest.approx(20/3.6)
  assert result.speedCameraControl.appliedOffset==pytest.approx(15/3.6)
  assert result.vTarget==pytest.approx(35/3.6)
  for _ in range(45):
    c.sender.run(sm['carState'],sm['carControl'],result,True)
  assert c.sender.v_cruise_min==30
  assert c.sender.v_target==35 and c.sender.cruise_button==SendButton.decrease


@pytest.mark.parametrize('button,first,held,final',[
  ('accelCruise',85.,95.,105.), ('decelCruise',65.,55.,45.)])
@pytest.mark.parametrize('delayed_final',[False,True])
def test_actual_held_increments_and_final_release_own_driver_target(override_cycle,button,first,held,final,delayed_final):
  c=override_cycle
  c.frame()
  c.frame(buttons=((button,True),),stock_kph=75)
  c.frame(stock_kph=first)
  for _ in range(50):
    c.frame(dt=.01,stock_kph=held)
    assert c.sender.cruise_button==SendButton.none
  c.frame(buttons=((button,False),),stock_kph=held if delayed_final else final)
  if delayed_final:
    for _ in range(50):
      c.frame(dt=.01,stock_kph=held)
      assert c.sender.cruise_button==SendButton.none
    c.frame(stock_kph=final)
  assert c.helper.v_cruise_kph==pytest.approx(final)
  _,passed=c.pass_camera(stock_kph=final)
  assert passed.vTarget==pytest.approx(final/3.6)
  assert c.helper.driver_set_speed_kph==pytest.approx(final)


@pytest.mark.parametrize('button,first,final',[
  ('accelCruise',85.,95.), ('decelCruise',65.,55.)])
def test_actual_release_already_shows_final_native_setting(override_cycle,button,first,final):
  c=override_cycle
  c.frame()
  c.frame(buttons=((button,True),),stock_kph=75)
  c.frame(stock_kph=first)
  c.frame(stock_kph=final)
  _,released=c.frame(buttons=((button,False),),stock_kph=final)
  assert c.helper.v_cruise_kph==pytest.approx(final)
  assert released.speedCameraControl.reason!='awaiting_driver_feedback'
  for _ in range(10):
    _,stable=c.frame(dt=.01,stock_kph=final)
    assert stable.speedCameraControl.reason!='awaiting_driver_feedback'
  _,passed=c.pass_camera(stock_kph=final)
  assert passed.vTarget==pytest.approx(final/3.6)


@pytest.mark.parametrize('button,first,final',[
  ('accelCruise',85.,95.), ('decelCruise',65.,55.)])
def test_actual_scc_feedback_after_completed_gesture_never_rewrites_driver(override_cycle,native_params,button,first,final):
  c=override_cycle
  c.frame()
  c.frame(buttons=((button,True),),stock_kph=75)
  c.frame(stock_kph=first)
  c.frame(stock_kph=final)
  c.frame(buttons=((button,False),),stock_kph=final)
  native_params.put_bool('SmartCruiseControlVision',True)
  c.planner.scc.vision.frame=0  # Poll the actual edited Params on this next update.
  rate=2*30/(12**2)
  c.frame(stock_kph=final,model_yaw_rate=rate)
  _,curve=c.frame(stock_kph=final,model_yaw_rate=rate)
  assert curve.longitudinalPlanSource=='sccVision'
  assert curve.vTarget < final/3.6
  # DOWN feedback continues in the same direction as the completed gesture.
  c.frame(stock_kph=45 if button=='decelCruise' else 75,model_yaw_rate=rate)
  assert c.helper.v_cruise_kph==pytest.approx(final)
  if button=='accelCruise':
    # Removing the real SCC constraint makes own ACC recovery increase again;
    # those UP-direction native values must not become a fresh driver action.
    native_params.put_bool('SmartCruiseControlVision',False)
    c.planner.scc.vision.frame=0
    c.frame(stock_kph=75)
    c.frame(stock_kph=85)
    c.frame(stock_kph=90)
    assert c.helper.v_cruise_kph==pytest.approx(final)


@pytest.mark.parametrize('baseline,response,recovery',[(140.,150.,140.),(0.,75.,65.)])
def test_actual_ipc_virtual_max_and_unknown_press_baseline_require_new_action(override_cycle,baseline,response,recovery):
  c=override_cycle
  c.frame()
  c.helper.v_cruise_kph=c.helper.v_cruise_cluster_kph=100.
  c.frame(stock_kph=baseline,buttons=(('accelCruise',True),))
  _,unaccepted=c.frame(stock_kph=response,buttons=(('accelCruise',False),))
  assert c.helper.v_cruise_kph==pytest.approx(100)
  assert unaccepted.speedCameraControl.manualTakeover
  assert c.sender.cruise_button==SendButton.none
  _,pending_pass=c.pass_camera(stock_kph=response)
  assert pending_pass.speedCameraControl.manualTakeover
  assert pending_pass.speedCameraControl.rawLimit==0
  assert c.helper.driver_set_speed_kph==pytest.approx(100)
  for _ in range(50):
    _,pending=c.frame(dt=.01,north_m=35,stock_kph=response)
    assert c.sender.cruise_button==SendButton.none
    assert pending.speedCameraControl.manualTakeover
    assert c.helper.v_cruise_kph==pytest.approx(100)
  # Only an explicit new attributable action can replace this uncertain base.
  c.frame(north_m=40,stock_kph=response,buttons=(('decelCruise',True),))
  _,recovered=c.frame(north_m=45,stock_kph=recovery,buttons=(('decelCruise',False),))
  assert c.helper.v_cruise_kph==pytest.approx(recovery)
  assert recovered.vTarget==pytest.approx(recovery/3.6)
