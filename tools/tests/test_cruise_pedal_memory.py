"""Portable tests of actual cruise helpers with native imports isolated.

No root conftest, cereal, Params files, messages, or vehicle I/O. The production
helper bodies execute intact; only their external dependency boundaries differ.
"""

import ast
import copy
import math
from enum import IntEnum
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[2]


class ButtonType(IntEnum):
  accelCruise = 1
  decelCruise = 2
  setCruise = 3
  resumeCruise = 4
  cancel = 5
  mainCruise = 6

  @property
  def raw(self):
    return int(self)


class AssistState(IntEnum):
  disabled = 0
  preActive = 1
  active = 2


def load_body(relative, namespace):
  path = ROOT / relative
  tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
  tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
  tree.body.insert(0, ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0))
  exec(compile(ast.fix_missing_locations(tree), str(path), "exec", dont_inherit=True), namespace)


@pytest.fixture
def make_helper():
  def make(*, pcm=True, pcm_speed=False, icbm=True, openpilot_long=False, custom=False, short=1, long=5):
    params = {"CustomAccIncrementsEnabled": custom, "CustomAccShortPressIncrement": short,
              "CustomAccLongPressIncrement": long}
    namespace = {
      "np": np, "math": math,
      "car": NS(CarState=NS(ButtonEvent=NS(Type=ButtonType))),
      "custom": NS(LongitudinalPlanSP=NS(SpeedLimit=NS(AssistState=AssistState))),
      "CV": NS(MS_TO_KPH=3.6, KPH_TO_MS=1 / 3.6, MPH_TO_KPH=1.609344),
      "Params": lambda: NS(get_bool=lambda key: params[key], get=lambda key, **_: params[key]),
      "SLA_ACTIVE_STATES": (AssistState.active,),
      "compare_cluster_target": lambda current, target, is_metric: (current < target, current > target),
    }
    load_body("sunnypilot/selfdrive/car/intelligent_cruise_button_management/helpers.py", namespace)
    load_body("sunnypilot/selfdrive/car/cruise_ext.py", namespace)
    load_body("selfdrive/car/cruise.py", namespace)
    return namespace["VCruiseHelper"](
      NS(pcmCruise=pcm, openpilotLongitudinalControl=openpilot_long),
      NS(pcmCruiseSpeed=pcm_speed, intelligentCruiseButtonManagementAvailable=icbm))
  return make


def button(kind, pressed):
  return NS(type=kind, pressed=pressed)


def drive(helper, *, enabled=True, acc_enabled=None, available=True, stock=79.0, cluster=None,
          gas=False, brake=False, buttons=(), is_metric=True):
  cs = NS(vEgo=79 / 3.6, gasPressed=gas, brakePressed=brake, buttonEvents=list(buttons),
          cruiseState=NS(available=available, enabled=enabled if acc_enabled is None else acc_enabled,
                         speed=stock / 3.6, speedCluster=(stock if cluster is None else cluster) / 3.6,
                         standstill=False))
  original = copy.deepcopy(cs)
  helper.update_v_cruise(cs, enabled, is_metric)
  assert cs == original  # A remembered setpoint never enables/resumes ACC.
  return helper.v_cruise_kph, helper.v_cruise_cluster_kph


def engage(helper, speed=130.0):
  for _ in range(3):
    assert drive(helper, stock=speed) == pytest.approx((speed, speed))


def camera_plan(helper, state, reason="", *, manual=False, road_limit=None):
  resolver = NS(speedLimitValid=road_limit is not None, speedLimitLastValid=False,
                speedLimitFinalLast=0.0 if road_limit is None else road_limit / 3.6)
  plan = NS(speedCameraControl=NS(state=state, reason=reason, manualTakeover=manual),
            speedLimit=NS(resolver=resolver,
                          assist=NS(state=AssistState.disabled if road_limit is None else AssistState.active)))
  helper.update_speed_limit_assist(True, plan)


def test_first_engagement_uses_new_stock_target_not_disengaged_feedback(make_helper):
  helper = make_helper()
  drive(helper, enabled=False, stock=48)
  drive(helper, enabled=False, stock=0)
  engage(helper)


@pytest.mark.parametrize("gas,brake,enabled", [(True, False, True), (True, False, False), (False, True, False)])
@pytest.mark.parametrize("stock", [0, 79, -3.6])
def test_pedal_preserves_virtual_130_across_stock_feedback(make_helper, gas, brake, enabled, stock):
  helper = make_helper()
  engage(helper)
  for _ in range(10):
    assert drive(helper, enabled=enabled, stock=stock, gas=gas, brake=brake) == pytest.approx((130, 130))


def test_brake_release_does_not_auto_resume_or_lose_target(make_helper):
  helper = make_helper()
  engage(helper)
  drive(helper, enabled=False, brake=True, stock=79)
  for _ in range(200):
    assert drive(helper, enabled=False, stock=0) == pytest.approx((130, 130))
  assert helper.enabled_prev is False


@pytest.mark.parametrize("kind", [ButtonType.resumeCruise, ButtonType.accelCruise])
@pytest.mark.parametrize("edges", [
  ((False, True), (False, False), (True, None), (True, None)),
  ((False, True), (True, None), (True, False), (True, None)),
  ((False, True), (True, False), (True, None)),
])
@pytest.mark.parametrize("custom", [False, True])
def test_short_resume_edge_keeps_130_without_synthetic_speed_step(make_helper, kind, edges, custom):
  helper = make_helper(custom=custom, short=10, long=10)
  engage(helper)
  drive(helper, enabled=False, stock=0, brake=True)
  for enabled, pressed in edges:
    buttons = () if pressed is None else (button(kind, pressed),)
    assert drive(helper, enabled=enabled, stock=79, buttons=buttons) == pytest.approx((130, 130))


def test_resume_pulse_in_one_frame_keeps_target(make_helper):
  helper = make_helper()
  engage(helper)
  drive(helper, enabled=False, brake=True)
  events = (button(ButtonType.resumeCruise, True), button(ButtonType.resumeCruise, False))
  assert drive(helper, buttons=events) == pytest.approx((130, 130))
  assert drive(helper) == pytest.approx((130, 130))


def test_main_switch_off_ends_remembered_session(make_helper):
  helper = make_helper()
  engage(helper)
  assert drive(helper, enabled=False, available=False) == (255, 255)
  drive(helper, enabled=False, stock=0)
  engage(helper, speed=95)


@pytest.mark.parametrize("mode", [dict(pcm_speed=True), dict(icbm=False), dict(openpilot_long=True)])
def test_other_stock_modes_keep_existing_feedback_behavior(make_helper, mode):
  helper = make_helper(**mode)
  engage(helper)
  assert drive(helper, enabled=False, stock=79, cluster=80, brake=True) == pytest.approx((79, 80))


def test_non_pcm_openpilot_long_keeps_existing_lifecycle(make_helper):
  helper = make_helper(pcm=False, pcm_speed=True, openpilot_long=True)
  cs = NS(vEgo=130 / 3.6, buttonEvents=[])
  helper.initialize_v_cruise(cs, experimental_mode=False, dynamic_experimental_control=False)
  assert drive(helper, enabled=False, stock=79, brake=True) == pytest.approx((130, 130))
  assert drive(helper, enabled=False, available=False) == (255, 255)


def test_ordinary_road_sla_retains_existing_setpoint_update(make_helper):
  helper = make_helper()
  engage(helper)
  helper.sla_state = AssistState.active
  helper.has_speed_limit = True
  helper.speed_limit_final_last_kph = 80.0
  assert drive(helper) == pytest.approx((80, 80))


def test_button_timers_are_private_to_each_helper(make_helper):
  first = make_helper()
  second = type(first)(first.CP, first.CP_SP)
  assert first.enable_button_timers is not second.enable_button_timers
  drive(first, enabled=False, buttons=(button(ButtonType.resumeCruise, True),))
  assert first.enable_button_timers[ButtonType.resumeCruise] == 1
  assert second.enable_button_timers[ButtonType.resumeCruise] == 0
  assert all(value == 0 for value in type(first).update_enabled_state.__globals__["CRUISE_BUTTON_TIMER"].values())


@pytest.mark.parametrize("state,reason", [("manual", "manual_button"), ("suspended", "button_override")])
@pytest.mark.parametrize("feedback", [80.0, 89.0, 70.0, 95.0])
def test_camera_wheel_correction_keeps_base_independent_of_native_step(make_helper, state, reason, feedback):
  helper = make_helper(custom=True, short=10, long=5)
  engage(helper)
  camera_plan(helper, "approach", "kinematic_ceiling")
  assert drive(helper, stock=79) == pytest.approx((130, 130))
  kind = ButtonType.accelCruise if feedback > 79 else ButtonType.decelCruise
  # Physical input may reach card before the next planner/manualTakeover frame.
  assert drive(helper, stock=79, buttons=(button(kind, True),)) == pytest.approx((130, 130))
  camera_plan(helper, state, reason, manual=True)
  assert drive(helper, stock=feedback, buttons=(button(kind, False),)) == pytest.approx((130, 130))
  for _ in range(5):
    assert drive(helper, stock=feedback) == pytest.approx((130, 130))
  camera_plan(helper, "passed", "geometric_pass")
  assert drive(helper, stock=feedback) == pytest.approx((130, 130))
  assert not helper.camera_episode_active


@pytest.mark.parametrize("reason", ["gas", "brake", "brake_waiting_resume"])
@pytest.mark.parametrize("manual", [False, True])
def test_temporary_pedal_protocol_never_becomes_permanent_stock_handover(make_helper, reason, manual):
  helper = make_helper()
  engage(helper)
  camera_plan(helper, "hold", "kinematic_ceiling")
  camera_plan(helper, "suspended", reason, manual=manual)
  assert drive(helper, enabled=reason == "gas", gas=reason == "gas", brake=reason == "brake", stock=79) == pytest.approx((130, 130))
  camera_plan(helper, "inactive", "acc_disengaged")
  assert drive(helper, enabled=False, stock=0) == pytest.approx((130, 130))
  assert helper.camera_episode_active
  for enabled, pressed in ((False, True), (True, False), (True, None)):
    events = () if pressed is None else (button(ButtonType.resumeCruise, pressed),)
    assert drive(helper, enabled=enabled, stock=79, buttons=events) == pytest.approx((130, 130))
  camera_plan(helper, "hold", "awaiting_map_update")
  assert drive(helper, stock=79) == pytest.approx((130, 130))
  camera_plan(helper, "passed", "geometric_pass")
  assert drive(helper, stock=79) == pytest.approx((130, 130))


@pytest.mark.parametrize("kind", [ButtonType.accelCruise, ButtonType.decelCruise])
@pytest.mark.parametrize("held_frames", [0, 50, 65])
def test_release_or_long_press_after_pass_cannot_change_remembered_base(make_helper, kind, held_frames):
  helper = make_helper(custom=True, short=10, long=5)
  engage(helper)
  camera_plan(helper, "approach")
  drive(helper, buttons=(button(kind, True),))
  camera_plan(helper, "manual", "manual_button", manual=True)
  drive(helper)
  camera_plan(helper, "passed", "geometric_pass")
  for _ in range(held_frames):
    assert drive(helper) == pytest.approx((130, 130))
  assert drive(helper, buttons=(button(kind, False),)) == pytest.approx((130, 130))
  # A genuinely new press outside the episode retains configured semantics.
  camera_plan(helper, "inactive", "no_camera")
  drive(helper, buttons=(button(kind, True),))
  target = 140 if kind == ButtonType.accelCruise else 120
  assert drive(helper, buttons=(button(kind, False),)) == pytest.approx((target, target))


def test_existing_non_camera_manual_handover_is_unchanged(make_helper):
  helper = make_helper()
  engage(helper)
  camera_plan(helper, "inactive", "manual_control", manual=True)
  assert drive(helper, stock=79, cluster=80) == pytest.approx((79, 80))


@pytest.mark.parametrize("reason", ["manual_button", "override", "disabled", "acc_disengaged"])
def test_permanent_cancel_keeps_existing_handover_instead_of_latching_episode(make_helper, reason):
  helper = make_helper()
  engage(helper)
  camera_plan(helper, "approach")
  camera_plan(helper, "cancelled", reason, manual=True)
  assert drive(helper, stock=79, cluster=80) == pytest.approx((79, 80))
  assert not helper.camera_episode_active


def test_press_held_into_camera_episode_does_not_modify_base_after_pass(make_helper):
  helper = make_helper()
  engage(helper)
  drive(helper, buttons=(button(ButtonType.accelCruise, True),))
  camera_plan(helper, "approach")
  drive(helper)
  camera_plan(helper, "passed", "geometric_pass")
  assert drive(helper, buttons=(button(ButtonType.accelCruise, False),)) == pytest.approx((130, 130))


def test_camera_episode_protects_base_without_changing_ordinary_road_sla(make_helper):
  helper = make_helper()
  engage(helper)
  camera_plan(helper, "approach", "kinematic_ceiling", road_limit=80)
  assert drive(helper) == pytest.approx((130, 130))
  camera_plan(helper, "passed", "geometric_pass", road_limit=80)
  # Existing road-SLA policy still applies once the camera episode ends.
  assert drive(helper) == pytest.approx((80, 80))


@pytest.mark.parametrize("release_before_enable", [False, True])
@pytest.mark.parametrize("invalid_feedback", [0, -3.6, float("nan"), float("inf")])
def test_set_reengagement_waits_for_release_engagement_and_native_feedback(make_helper, release_before_enable, invalid_feedback):
  helper = make_helper()
  engage(helper)
  drive(helper, enabled=False, brake=True, stock=130)
  assert drive(helper, enabled=False, stock=130, buttons=(button(ButtonType.setCruise, True),)) == pytest.approx((130, 130))
  if release_before_enable:
    drive(helper, enabled=False, stock=130, buttons=(button(ButtonType.setCruise, False),))
  # Neither old feedback during the press nor an unavailable new setpoint wins.
  for _ in range(3):
    assert drive(helper, stock=invalid_feedback) == pytest.approx((130, 130))
  if not release_before_enable:
    assert drive(helper, stock=79) == pytest.approx((130, 130))
  events = () if release_before_enable else (button(ButtonType.setCruise, False),)
  assert drive(helper, stock=79, buttons=events) == pytest.approx((79, 79))
  for _ in range(5):
    assert drive(helper, stock=70) == pytest.approx((79, 79))


@pytest.mark.parametrize("pressed", [True, False])
def test_set_first_event_on_engagement_edge_uses_previous_disabled_state(make_helper, pressed):
  helper = make_helper()
  engage(helper)
  drive(helper, enabled=False, brake=True, stock=130)
  feedback = 130 if pressed else 79
  drive(helper, enabled=True, acc_enabled=True, stock=feedback, buttons=(button(ButtonType.setCruise, pressed),))
  events = (button(ButtonType.setCruise, False),) if pressed else ()
  assert drive(helper, stock=79, buttons=events) == pytest.approx((79, 79))


def test_set_release_without_press_waits_for_both_enabled_signals(make_helper):
  helper = make_helper()
  engage(helper)
  drive(helper, enabled=False, brake=True)
  drive(helper, enabled=False, stock=130, buttons=(button(ButtonType.setCruise, False),))
  assert drive(helper, enabled=False, acc_enabled=True, stock=79) == pytest.approx((130, 130))
  assert drive(helper, enabled=True, acc_enabled=False, stock=79) == pytest.approx((130, 130))
  assert drive(helper, stock=79, cluster=80) == pytest.approx((79, 80))


def test_active_set_keeps_preexisting_behavior(make_helper):
  helper = make_helper()
  engage(helper)
  for pressed in (True, False):
    assert drive(helper, stock=79, buttons=(button(ButtonType.setCruise, pressed),)) == pytest.approx((130, 130))
  assert not helper._stock_set_pending


def test_resume_supersedes_unfinished_set_without_replacing_memory(make_helper):
  helper = make_helper()
  engage(helper)
  drive(helper, enabled=False, brake=True)
  drive(helper, enabled=False, stock=130, buttons=(button(ButtonType.setCruise, False),))
  drive(helper, enabled=False, buttons=(button(ButtonType.resumeCruise, True),))
  assert drive(helper, stock=79, buttons=(button(ButtonType.resumeCruise, False),)) == pytest.approx((130, 130))
  assert not helper._stock_set_pending


def test_camera_origin_set_release_is_not_new_reengagement_intent(make_helper):
  helper = make_helper()
  engage(helper)
  camera_plan(helper, "approach")
  drive(helper, enabled=False, brake=True, buttons=(button(ButtonType.setCruise, True),))
  camera_plan(helper, "passed", "geometric_pass")
  assert drive(helper, stock=79, buttons=(button(ButtonType.setCruise, False),)) == pytest.approx((130, 130))
  assert not helper._stock_set_pending


def test_main_switch_off_clears_unfinished_set(make_helper):
  helper = make_helper()
  engage(helper)
  drive(helper, enabled=False, buttons=(button(ButtonType.setCruise, True),))
  drive(helper, enabled=False, available=False)
  assert not helper._stock_set_pending
  assert not helper._stock_set_held
  engage(helper, speed=95)
