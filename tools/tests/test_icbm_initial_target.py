"""Initial stock SET/RES and retained planner-target regression evidence.

Execute production cruise helper and ICBM bodies with native imports replaced.
These are software observations, not timing or ECU acceptance measurements.
"""
import ast
import struct
from types import SimpleNamespace as NS

import pytest

from test_cruise_pedal_memory import ButtonType, ROOT, button, drive, make_helper  # noqa: F401


def icbm_controller():
  path = ROOT / 'sunnypilot/selfdrive/car/intelligent_cruise_button_management/controller.py'
  tree = ast.parse(path.read_text(encoding='utf-8'))
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
  # Actual manual-button timer function, not a no-op replacement.
  ext = ast.parse((ROOT / 'sunnypilot/selfdrive/car/cruise_ext.py').read_text(encoding='utf-8'))
  timer = next(n for n in ext.body if isinstance(n, ast.FunctionDef) and n.name == 'update_manual_button_timers')
  states = NS(inactive=0, preActive=1, holding=2, increasing=3, decreasing=4)
  buttons = NS(none=0, increase=1, decrease=2)
  ns = {
    'State': states, 'SendButtonState': buttons, 'DT_CTRL': .01, 'INACTIVE_TIMER': .4,
    'HYST_GAP': 0., 'CRUISE_BUTTON_TIMER': dict.fromkeys(ButtonType, 0),
    'SEND_BUTTONS': {states.increasing: buttons.increase, states.decreasing: buttons.decrease},
    'CV': NS(MS_TO_KPH=3.6, KPH_TO_MS=1 / 3.6, MS_TO_MPH=2.236936, MPH_TO_MS=.44704),
    'apply_hysteresis': lambda value, previous, gap: value,
    'get_minimum_set_speed': lambda metric: 30 if metric else 20,
  }
  future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
  module = ast.fix_missing_locations(ast.Module(body=[future, timer, cls], type_ignores=[]))
  exec(compile(module, str(path), 'exec', dont_inherit=True), ns)
  return ns['IntelligentCruiseButtonManagement'](NS(), NS(pcmCruiseSpeed=False)), states, buttons


def inputs():
  cs = NS(vCruise=255., vCruiseCluster=255., gasPressed=False, brakePressed=False,
          buttonEvents=[], cruiseState=NS(enabled=True, speed=30 / 3.6, speedCluster=30 / 3.6))
  cc = NS(enabled=True, cruiseControl=NS(override=False, cancel=False, resume=False))
  plan = NS(vTarget=145 / 3.6, speedCameraControl=NS(state='inactive', manualTakeover=False))
  return cs, cc, plan


@pytest.mark.parametrize('kind', [ButtonType.setCruise, ButtonType.resumeCruise])
def test_manual_first_engagement_button_suppresses_retained_145_plan(kind):
  icbm, states, buttons = icbm_controller()
  cs, cc, plan = inputs()
  cs.buttonEvents = [button(kind, True)]
  for _ in range(70):
    icbm.run(cs, cc, plan, True)
    cs.buttonEvents = []
    assert icbm.cruise_button == buttons.none
    assert icbm.state == states.inactive


def test_first_set_adopts_native_30_and_plan_catches_up_before_delay(make_helper):
  helper = make_helper()
  drive(helper, enabled=False, stock=0)
  drive(helper, enabled=False, stock=0, buttons=(button(ButtonType.setCruise, True),))
  drive(helper, stock=30)
  assert helper.v_cruise_kph == 255  # Held SET does not adopt old feedback.
  drive(helper, stock=30, buttons=(button(ButtonType.setCruise, False),))
  assert helper.v_cruise_kph == pytest.approx(30)
  icbm, states, buttons = icbm_controller()
  cs, cc, plan = inputs()
  cs.vCruise = cs.vCruiseCluster = helper.v_cruise_kph
  for tick in range(70):
    # Retain the old, otherwise valid plan for 100 ms after native adoption.
    if tick == 10:
      plan.vTarget = helper.v_cruise_kph / 3.6
    icbm.run(cs, cc, plan, True)
    assert icbm.cruise_button == buttons.none
  assert icbm.state == states.holding


@pytest.mark.parametrize('invalid_feedback', [0., -3.6, float('nan'), float('inf')])
@pytest.mark.parametrize('delay_ticks', [1, 70])
def test_first_resume_waits_for_native_feedback_after_enabled_edge(make_helper, invalid_feedback, delay_ticks):
  helper = make_helper()
  drive(helper, enabled=False, stock=0)
  drive(helper, enabled=False, stock=0, buttons=(button(ButtonType.resumeCruise, True),))
  drive(helper, stock=0, buttons=(button(ButtonType.resumeCruise, False),))
  for _ in range(delay_ticks):
    drive(helper, stock=invalid_feedback)
    assert not helper._stock_virtual_cruise_initialized
  assert drive(helper, stock=30) == pytest.approx((30, 30))
  assert helper._stock_virtual_cruise_initialized
  # Native feedback changes after initialization must not replace the base.
  assert drive(helper, stock=40) == pytest.approx((30, 30))


def test_known_base_survives_resume_with_delayed_native_feedback(make_helper):
  helper = make_helper()
  drive(helper, stock=130)
  assert helper._stock_virtual_cruise_initialized
  drive(helper, enabled=False, stock=0, brake=True)
  drive(helper, enabled=False, stock=0, buttons=(button(ButtonType.resumeCruise, True),))
  drive(helper, stock=0, buttons=(button(ButtonType.resumeCruise, False),))
  for _ in range(70):
    assert drive(helper, stock=0) == pytest.approx((130, 130))
  assert drive(helper, stock=30) == pytest.approx((130, 130))


def test_disabled_native_value_is_not_a_remembered_base_until_engaged(make_helper):
  helper = make_helper()
  # A native value observed while disabled does not establish driver memory.
  assert drive(helper, enabled=False, stock=130) == pytest.approx((130, 130))
  assert not helper._stock_virtual_cruise_initialized
  assert drive(helper, stock=30) == pytest.approx((30, 30))
  assert helper._stock_virtual_cruise_initialized
  for _ in range(5):
    assert drive(helper, stock=79) == pytest.approx((30, 30))


def test_existing_valid_enabled_branch_establishes_memory_before_next_frame(make_helper):
  helper = make_helper()
  # Exercise the original common branch rather than the new feedback path.
  helper._stock_set_held = True
  helper.enabled_prev = True
  helper.v_cruise_kph = helper.v_cruise_cluster_kph = 130.
  assert not helper._stock_virtual_cruise_initialized
  assert drive(helper, stock=79) == pytest.approx((130, 130))
  assert helper._stock_virtual_cruise_initialized
  helper._stock_set_held = False
  assert drive(helper, stock=30) == pytest.approx((130, 130))


@pytest.mark.parametrize('kind', [ButtonType.setCruise, ButtonType.resumeCruise])
def test_released_button_cannot_turn_unset_virtual_target_into_145_increase(kind):
  icbm, _, buttons = icbm_controller()
  cs, cc, plan = inputs()
  cs.buttonEvents = [button(kind, True)]
  icbm.run(cs, cc, plan, True)
  cs.buttonEvents = [button(kind, False)]
  for _ in range(70):
    icbm.run(cs, cc, plan, True)
    cs.buttonEvents = []
    assert icbm.cruise_button == buttons.none


def test_fresh_but_semantically_old_plan_after_set_cannot_increase_to_145():
  icbm, _, buttons = icbm_controller()
  cs, cc, plan = inputs()
  cs.vCruise = cs.vCruiseCluster = 30.
  # Plan messages may be healthy while carrying an older virtual setpoint.
  # No message-age failure or publication freeze is modeled by this test.
  for _ in range(70):
    icbm.run(cs, cc, plan, True)
    assert icbm.cruise_button == buttons.none
  plan.vTarget = 30 / 3.6
  for _ in range(70):
    icbm.run(cs, cc, plan, True)
    assert icbm.cruise_button == buttons.none
  assert icbm.v_target == 30


@pytest.mark.parametrize('invalid', [0., -1., 255., float('nan'), float('inf'), -float('inf')])
def test_uninitialized_or_invalid_virtual_base_never_sends_buttons(invalid):
  icbm, states, buttons = icbm_controller()
  cs, cc, plan = inputs()
  cs.vCruise = invalid
  for _ in range(70):
    icbm.run(cs, cc, plan, True)
    assert icbm.cruise_button == buttons.none and icbm.state == states.inactive


@pytest.mark.parametrize('invalid', [-1., float('nan'), float('inf'), -float('inf')])
def test_invalid_plan_never_reaches_rounding_or_button_action(invalid):
  icbm, states, buttons = icbm_controller()
  cs, cc, plan = inputs()
  cs.vCruise = 130.
  plan.vTarget = invalid
  for _ in range(70):
    icbm.run(cs, cc, plan, True)
    assert icbm.cruise_button == buttons.none and icbm.state == states.inactive


def test_zero_plan_preserves_original_decrease_to_native_minimum():
  icbm, states, buttons = icbm_controller()
  cs, cc, plan = inputs()
  cs.vCruise = 130.
  cs.cruiseState.speed = cs.cruiseState.speedCluster = 79 / 3.6
  plan.vTarget = 0.
  for _ in range(70):
    icbm.run(cs, cc, plan, True)
  assert icbm.state == states.decreasing and icbm.cruise_button == buttons.decrease
  cs.cruiseState.speed = cs.cruiseState.speedCluster = 30 / 3.6
  icbm.run(cs, cc, plan, True)
  assert icbm.state == states.holding and icbm.cruise_button == buttons.none
  # The pre-existing holding/preActive cycle never requests another decrease
  # once the stock setpoint is at the native minimum.
  for _ in range(70):
    icbm.run(cs, cc, plan, True)
    assert icbm.cruise_button == buttons.none


def test_zero_plan_cannot_activate_without_an_initialized_virtual_base():
  icbm, states, buttons = icbm_controller()
  cs, cc, plan = inputs()
  cs.vCruise = 255.
  plan.vTarget = 0.
  for _ in range(70):
    icbm.run(cs, cc, plan, True)
    assert icbm.state == states.inactive and icbm.cruise_button == buttons.none


@pytest.mark.parametrize('base,stock,target,expected', [(130, 79, 130, 'increase'), (130, 79, 60, 'decrease'),
                                                     (30, 30, 30, 'none')])
def test_current_base_and_camera_lower_targets_retain_expected_action(base, stock, target, expected):
  icbm, _, buttons = icbm_controller()
  cs, cc, plan = inputs()
  cs.vCruise = base
  cs.cruiseState.speed = cs.cruiseState.speedCluster = stock / 3.6
  plan.vTarget = target / 3.6
  for _ in range(70):
    icbm.run(cs, cc, plan, True)
  assert icbm.cruise_button == getattr(buttons, expected)


@pytest.mark.parametrize('base', [*range(1, 146), 30.1, 79.3, 140.7, 144.999])
def test_float32_wire_rounding_preserves_equal_base_and_rejects_excess(base):
  icbm, _, _ = icbm_controller()
  cs, _, plan = inputs()
  # Both fields are Float32 in their production Cereal schemas.
  wire = lambda value: struct.unpack('<f', struct.pack('<f', value))[0]
  cs.vCruise = wire(base)
  plan.vTarget = wire(cs.vCruise / 3.6)
  assert icbm.target_is_valid(cs, plan)
  plan.vTarget = wire((cs.vCruise + .01) / 3.6)
  assert not icbm.target_is_valid(cs, plan)
