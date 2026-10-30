"""Invalid native feedback is not a zero-speed acceleration request."""
from types import SimpleNamespace as NS

import pytest

from test_camera_resolver_integration import control as control
from test_camera_resolver_integration import real_wire_controls


@pytest.fixture
def feedback(control, monkeypatch):
  runtime = real_wire_controls(control, monkeypatch)
  controller = runtime.icbm
  cs = runtime.cs
  cs.vCruise = cs.vCruiseCluster = 100.
  cs.cruiseState.enabled = True
  cs.buttonEvents = []
  cs.gasPressed = cs.brakePressed = False
  plan = NS(vTarget=85 / 3.6, speedCameraControl=NS(state="active", manualTakeover=False))
  cc = NS(enabled=True, cruiseControl=NS(override=False, cancel=False, resume=False))
  def run(speed, cycles=1):
    cs.cruiseState.speedCluster = speed
    for _ in range(cycles):
      controller.run(cs, cc, plan, True)
    return controller.cruise_button
  return NS(run=run, controller=controller, cs=cs, plan=plan, cc=cc,
            buttons=runtime.icbm_mod.SendButtonState)


@pytest.mark.parametrize("speed", [0., -1., float("nan"), float("inf"), -float("inf")])
def test_invalid_feedback_never_increases_from_zero_or_crashes(feedback, speed):
  assert feedback.run(speed, 80) == feedback.buttons.none
  assert not feedback.controller.is_ready
  assert not feedback.controller.native_feedback_valid
  assert feedback.controller.v_cruise_cluster == 0


def test_invalid_feedback_immediately_cancels_existing_transmission_and_recovers(feedback):
  assert feedback.run(100 / 3.6, 80) == feedback.buttons.decrease
  assert feedback.run(float("nan")) == feedback.buttons.none
  assert not feedback.controller.is_ready
  assert feedback.run(100 / 3.6, 80) == feedback.buttons.decrease
  assert feedback.controller.native_feedback_valid


def test_native_feedback_above_virtual_max_can_still_decrease_to_valid_camera_target(feedback):
  # The virtual driver-base limit remains145. A stock150 reading must not be
  # rejected solely because native ACC supports a wider physical range.
  assert feedback.run(150 / 3.6, 80) == feedback.buttons.decrease
  assert feedback.controller.v_target == 85
  assert feedback.controller.native_feedback_valid
  assert feedback.controller.v_cruise_min == 30


def test_virtual_driver_base_limit_remains_unchanged(feedback):
  feedback.cs.vCruise = 150.
  assert feedback.run(150 / 3.6, 80) == feedback.buttons.none
  assert not feedback.controller.is_ready


def test_equal_valid_native_target_stops_button_commands(feedback):
  assert feedback.run(100 / 3.6, 80) == feedback.buttons.decrease
  assert feedback.run(85 / 3.6, 80) == feedback.buttons.none
  assert feedback.controller.v_cruise_equal
