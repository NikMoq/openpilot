"""Actual ICBM state machine with native message/type imports replaced offline."""

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def controller():
  source = ROOT / 'sunnypilot/selfdrive/car/intelligent_cruise_button_management/controller.py'
  parsed = ast.parse(source.read_text(), filename=str(source))
  cls = next(node for node in parsed.body if isinstance(node, ast.ClassDef))
  states = NS(inactive=0, preActive=1, holding=2, increasing=3, decreasing=4)
  buttons = NS(none=0, increase=1, decrease=2)
  dependency = {
    'State': states, 'SendButtonState': buttons,
    'CRUISE_BUTTON_TIMER': {}, 'DT_CTRL': 0.01, 'INACTIVE_TIMER': 0.4,
    'HYST_GAP': 0.0, 'SEND_BUTTONS': {states.increasing: buttons.increase, states.decreasing: buttons.decrease},
    'CV': NS(MS_TO_KPH=3.6, KPH_TO_MS=1 / 3.6, MS_TO_MPH=2.236936, MPH_TO_MS=0.44704),
    'apply_hysteresis': lambda value, previous, gap: value,
    'get_minimum_set_speed': lambda metric: 30 if metric else 20,
    # No wheel inputs in these pedal-priority cases. Button timer behavior is
    # independent and deliberately outside this test's claimed coverage.
    'update_manual_button_timers': lambda *_: None,
  }
  future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
  module = ast.fix_missing_locations(ast.Module(body=[future, cls], type_ignores=[]))
  exec(compile(module, str(source), 'exec', dont_inherit=True), dependency)
  return dependency['IntelligentCruiseButtonManagement'](NS(), NS(pcmCruiseSpeed=False)), states, buttons


def inputs(target_kph=130):
  cs = NS(gasPressed=False, brakePressed=False, cruiseState=NS(enabled=True, speedCluster=79 / 3.6))
  cc = NS(enabled=True, cruiseControl=NS(override=False, cancel=False, resume=False))
  return cs, cc, NS(vTarget=target_kph / 3.6, speedCameraControl=NS(state='inactive', manualTakeover=False))


@pytest.mark.parametrize('pedal', ['gasPressed', 'brakePressed'])
@pytest.mark.parametrize('target_kph', [60, 130])
def test_pedal_stops_active_buttons_before_override_message(controller, pedal, target_kph):
  icbm, states, buttons = controller
  cs, cc, plan = inputs(target_kph)
  icbm.state = states.increasing if target_kph > 79 else states.decreasing
  icbm.is_ready_prev = True
  icbm.run(cs, cc, plan, True)
  assert icbm.cruise_button != buttons.none
  setattr(cs, pedal, True)
  # Simulate carControl's override flag not having caught up with carState yet.
  assert not cc.cruiseControl.override
  icbm.run(cs, cc, plan, True)
  assert icbm.cruise_button == buttons.none and icbm.state == states.inactive
  assert icbm.v_target == target_kph


def test_gas_release_keeps_target_and_obeys_existing_activation_delay(controller):
  icbm, states, buttons = controller
  cs, cc, plan = inputs()
  cs.gasPressed = True
  icbm.run(cs, cc, plan, True)
  cs.gasPressed = False
  icbm.run(cs, cc, plan, True)
  assert icbm.state == states.preActive and icbm.cruise_button == buttons.none
  for _ in range(39):
    icbm.run(cs, cc, plan, True)
    assert icbm.cruise_button == buttons.none
  icbm.run(cs, cc, plan, True)
  assert icbm.cruise_button == buttons.increase and icbm.v_target == 130


def test_released_brake_cannot_reengage_stock_acc(controller):
  icbm, states, buttons = controller
  cs, cc, plan = inputs()
  cs.brakePressed = True
  icbm.run(cs, cc, plan, True)
  cs.brakePressed = False
  cs.cruiseState.enabled = False
  # Even if CC.enabled is stale, no button is sent to a disengaged ACC.
  for _ in range(60):
    icbm.run(cs, cc, plan, True)
    assert icbm.cruise_button == buttons.none and icbm.state == states.inactive
  assert icbm.v_target == 130


@pytest.mark.parametrize('stock_kph,target_kph', [(89, 80), (140, 130), (79, 130)])
def test_camera_manual_authority_suspends_buttons_until_episode_release(controller, stock_kph, target_kph):
  icbm, states, buttons = controller
  cs, cc, plan = inputs(target_kph)
  cs.cruiseState.speedCluster = stock_kph / 3.6
  plan.speedCameraControl = NS(state='manual', manualTakeover=True)
  for _ in range(60):
    icbm.run(cs, cc, plan, True)
    assert icbm.cruise_button == buttons.none and icbm.state == states.inactive
  # Passage hands authority back with the existing activation delay. Other
  # planner constraints are still represented by the supplied target.
  plan.speedCameraControl = NS(state='passed', manualTakeover=False)
  icbm.run(cs, cc, plan, True)
  assert icbm.state == states.preActive and icbm.cruise_button == buttons.none
  for _ in range(40):
    icbm.run(cs, cc, plan, True)
  expected = buttons.decrease if target_kph < stock_kph else buttons.increase
  assert icbm.cruise_button == expected
