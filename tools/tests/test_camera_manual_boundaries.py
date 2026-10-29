"""Real helper/adapter/wire/ICBM agreement at unsupported manual boundaries."""
from types import SimpleNamespace as NS

import pytest
from test_camera_resolver_integration import control, update, real_wire_controls


@pytest.mark.parametrize('baseline,observed,recovered,reason', [
  (140, 150, 140, 'driver_speed_outside_virtual_range'),
  (0, 75, 65, 'awaiting_driver_feedback'),
])
def test_unsupported_or_unknown_manual_response_keeps_authority_until_new_valid_action(
    control, monkeypatch, baseline, observed, recovered, reason):
  c = control
  runtime = real_wire_controls(c, monkeypatch)
  cs, helper = runtime.cs, runtime.helper
  cs.cruiseState.speed = cs.cruiseState.speedCluster = baseline/3.6
  update(c)
  runtime.publish()
  helper.update_v_cruise(cs, True, True)
  cc = NS(enabled=True, cruiseControl=NS(override=False, cancel=False, resume=False))
  def frame(speed, events=()):
    cs.cruiseState.speed = cs.cruiseState.speedCluster = speed/3.6
    cs.buttonEvents = list(events)
    c.planner.camera.observe_car_state(cs, automatic_target=c.planner.output_v_target)
    helper.update_v_cruise(cs, True, True)
    cs.vCruise = cs.vCruiseCluster = helper.v_cruise_kph
    update(c)
    plan = runtime.publish()
    runtime.icbm.run(cs, cc, plan, True)
    return plan
  frame(baseline, [dict(type='accelCruise', pressed=True)])
  plan = frame(observed, [dict(type='accelCruise', pressed=False)])
  for _ in range(50):
    plan = frame(observed)
    assert helper.v_cruise_kph == 100  # No unsupported/imagined driver target.
    assert plan.speedCameraControl.state == 'manual'
    assert plan.speedCameraControl.reason == reason
    assert runtime.icbm.cruise_button == runtime.icbm_mod.SendButtonState.none
  c.lmd.speedCameraControlState = 'PASSED'
  c.lmd.speedCameraControlPassProven = True
  plan = frame(observed)
  assert plan.speedCameraControl.state == 'manual'
  assert plan.speedCameraControl.rawLimit == 0 and not plan.speedCameraControl.countdownValid
  assert runtime.icbm.cruise_button == runtime.icbm_mod.SendButtonState.none
  # A fresh attributed DOWN action with a positive baseline and supported
  # response resolves uncertainty without raising the original145 guard.
  frame(observed, [dict(type='decelCruise', pressed=True)])
  plan = frame(recovered, [dict(type='decelCruise', pressed=False)])
  assert helper.v_cruise_kph == pytest.approx(recovered)
  assert plan.vTarget == pytest.approx(recovered/3.6)
  assert plan.speedCameraControl.state == 'passed'
  assert runtime.icbm.cruise_button == runtime.icbm_mod.SendButtonState.none


def test_late_unsupported_response_after_proven_pass_keeps_manual_authority(control, monkeypatch):
  c = control
  runtime = real_wire_controls(c, monkeypatch)
  cs, helper = runtime.cs, runtime.helper
  cs.cruiseState.speed = cs.cruiseState.speedCluster = 140/3.6
  update(c)
  runtime.publish()
  helper.update_v_cruise(cs, True, True)
  cc = NS(enabled=True, cruiseControl=NS(override=False, cancel=False, resume=False))
  def frame(speed, events=()):
    cs.cruiseState.speed = cs.cruiseState.speedCluster = speed/3.6
    cs.buttonEvents = list(events)
    c.planner.camera.observe_car_state(cs, automatic_target=c.planner.output_v_target)
    helper.update_v_cruise(cs, True, True)
    cs.vCruise = cs.vCruiseCluster = helper.v_cruise_kph
    update(c)
    plan = runtime.publish()
    runtime.icbm.run(cs, cc, plan, True)
    return plan
  frame(140, [dict(type='accelCruise', pressed=True)])
  frame(142)
  frame(142, [dict(type='accelCruise', pressed=False)])
  c.lmd.speedCameraControlState = 'PASSED'
  c.lmd.speedCameraControlPassProven = True
  assert frame(142).speedCameraControl.state == 'passed'
  plan = frame(150)
  assert helper.v_cruise_kph == pytest.approx(142)
  assert plan.speedCameraControl.state == 'manual'
  assert runtime.icbm.cruise_button == runtime.icbm_mod.SendButtonState.none
  frame(150, [dict(type='decelCruise', pressed=True)])
  plan = frame(140, [dict(type='decelCruise', pressed=False)])
  assert helper.v_cruise_kph == pytest.approx(140)
  assert plan.vTarget == pytest.approx(140/3.6)
