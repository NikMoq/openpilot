"""Real helper/adapter/wire/ICBM at native feedback boundaries without overrides."""
import pytest
from test_camera_resolver_integration import camera_wire_cycle
from test_camera_resolver_integration import control as control


@pytest.mark.parametrize('baseline,observed,recovered', [
  (140, 150, 140),
  (0, 75, 65),
])
def test_unknown_or_unsupported_button_feedback_preserves_pre_camera_base_and_completes_pass(
    control, monkeypatch, baseline, observed, recovered):
  c = control
  runtime, frame = camera_wire_cycle(c, monkeypatch)
  frame(baseline, [dict(type='accelCruise', pressed=True)])
  frame(observed, [dict(type='accelCruise', pressed=False)])
  for _ in range(50):
    plan = frame(observed)
    assert runtime.helper.v_cruise_kph == 100
    assert plan.speedCameraControl.state == 'active'
    assert plan.speedCameraControl.speedCap == pytest.approx(75/3.6)
  c.lmd.speedCameraControlState = 'PASSED'
  c.lmd.speedCameraControlPassProven = True
  plan = frame(observed)
  assert plan.speedCameraControl.state == 'passed'
  assert plan.speedCameraControl.rawLimit == 0 and not plan.speedCameraControl.countdownValid
  assert plan.vTarget == pytest.approx(100/3.6) and runtime.helper.v_cruise_kph == 100
  # Later feedback from the in-zone physical gesture still cannot become the
  # driver's restored base, even if it only becomes supported after passage.
  plan = frame(recovered)
  assert plan.vTarget == pytest.approx(100/3.6) and runtime.helper.v_cruise_kph == 100


def test_late_unsupported_response_after_proven_pass_does_not_create_override(control, monkeypatch):
  c = control
  runtime, frame = camera_wire_cycle(c, monkeypatch)
  frame(140, [dict(type='accelCruise', pressed=True)])
  frame(142)
  frame(142, [dict(type='accelCruise', pressed=False)])
  c.lmd.speedCameraControlState = 'PASSED'
  c.lmd.speedCameraControlPassProven = True
  assert frame(142).speedCameraControl.state == 'passed'
  for speed in (150, 140, 130, 100):
    plan = frame(speed)
    assert runtime.helper.v_cruise_kph == 100 and plan.vTarget == pytest.approx(100/3.6)
    assert not plan.speedCameraControl.manualTakeover
    assert plan.speedCameraControl.state not in ('manual', 'driverOverride')
  # A new camera is an independent limit after the old physical correction.
  c.lmd.speedCameraControlState = 'ACTIVE'
  c.lmd.speedCameraControlPassProven = False
  c.lmd.speedCameraControlEventId = 'next-camera'
  plan = frame(100)
  assert plan.speedCameraControl.state == 'active' and plan.vTarget == pytest.approx(75/3.6)
  assert runtime.helper.v_cruise_kph == 100
