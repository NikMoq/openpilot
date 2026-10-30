"""Camera buttons pause ICBM temporarily; they cannot latch a camera override."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("camera_event_adapter", ROOT / "sunnypilot/selfdrive/controls/lib/camera_speed_adapter.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def event(identity="first", *, distance=100., state="ACTIVE", passed=False, valid=True, raw=70.):
  return NS(speedCameraControlVersion=1, speedCameraControlEventId=identity,
            speedCameraControlState=state, speedCameraControlDistance=distance,
            speedCameraControlSpeedLimit=raw / 3.6, speedCameraControlValid=valid,
            speedCameraControlAutoControlAllowed=True, speedCameraControlPassProven=passed)


def update(adapter, data=None, **kwargs):
  return adapter.update(event() if data is None else data,
                        **(dict(enabled=True, reliable=True, engaged=True, reaction_distance=900) | kwargs))


def stock(*buttons, speed=100., enabled=True, gas=False, brake=False):
  return NS(cruiseState=NS(enabled=enabled, speedCluster=speed / 3.6),
            buttonEvents=[NS(type=kind, pressed=pressed) for kind, pressed in buttons],
            gasPressed=gas, brakePressed=brake)


@pytest.mark.parametrize("kind", ["accelCruise", "decelCruise", "setCruise", "resumeCruise", "cancel", "mainCruise"])
def test_in_zone_buttons_never_create_persistent_manual_camera_authority(kind):
  adapter = module.CameraSpeedAdapter()
  assert update(adapter).automatic
  adapter.observe_car_state(stock((kind, True)))
  pressed = update(adapter)
  assert pressed.automatic and not pressed.manual and pressed.state == "ACTIVE"
  # A changed or unchanged stock response, held input and standstill do not
  # redefine the camera ceiling or the saved driver's pre-camera target.
  for speed in (100., 105., 65.):
    adapter.observe_car_state(stock((kind, True), speed=speed))
    held = update(adapter)
    assert held.raw_limit == pytest.approx(70 / 3.6) and held.automatic and not held.manual
  adapter.observe_car_state(stock((kind, False), speed=65.))
  released = update(adapter)
  assert released.automatic and not released.manual and released.state == "ACTIVE"


def test_confirmed_pass_clears_event_even_with_unacknowledged_held_button():
  adapter = module.CameraSpeedAdapter()
  assert update(adapter).event_id == "first"
  adapter.observe_car_state(stock(("accelCruise", True)))
  done = update(adapter, event(state="PASSED", distance=-40, passed=True, valid=False))
  assert done.state == "PASSED" and not done.manual and not done.automatic
  assert done.raw_limit == done.distance == 0 and not done.countdown_valid
  assert adapter._event_id == "" and not adapter._active
  # No release or native stock-speed response is required to finish the event.
  idle = update(adapter, event(state="PASSED", distance=-40, passed=True, valid=False))
  assert idle.state == "IDLE" and idle.event_id == "" and not idle.manual
  next_camera = update(adapter, event("second", raw=70.))
  assert next_camera.event_id == "second" and next_camera.automatic and not next_camera.manual


def test_gps_uncertainty_does_not_create_override_or_complete_event():
  adapter = module.CameraSpeedAdapter()
  update(adapter)
  adapter.observe_car_state(stock(("decelCruise", True)))
  hold = update(adapter, event(valid=False), reliable=False)
  assert hold.state == "ACTIVE" and hold.event_id == "first" and hold.automatic
  assert not hold.manual and not hold.countdown_valid and hold.reason == "unreliable_hold"
  recovered = update(adapter, event(distance=80.))
  assert recovered.automatic and recovered.countdown_valid and not recovered.manual


def test_no_buttons_or_stopped_acc_cannot_create_driver_override():
  adapter = module.CameraSpeedAdapter()
  update(adapter)
  for gas, brake, enabled in [(False, False, True), (True, False, True), (False, True, False)]:
    adapter.observe_car_state(stock(speed=100., gas=gas, brake=brake, enabled=enabled))
    result = update(adapter, engaged=enabled)
    assert result.automatic and not result.manual and result.state == "ACTIVE"


def test_wrong_event_pass_or_new_candidate_cannot_release_previous_camera():
  adapter = module.CameraSpeedAdapter()
  update(adapter)
  wrong = update(adapter, event("other", state="PASSED", passed=True, valid=False))
  assert wrong.automatic and wrong.event_id == "first" and not wrong.countdown_valid
  different = update(adapter, event("other", raw=90.))
  assert different.automatic and different.raw_limit == pytest.approx(70 / 3.6)
  assert different.event_id == "first" and not different.manual


def test_disengaged_acc_does_not_activate_new_camera_but_approach_remains_visible():
  adapter = module.CameraSpeedAdapter()
  approaching = update(adapter, engaged=False)
  assert approaching.state == "APPROACHING" and not approaching.automatic
  adapter.observe_car_state(stock(("setCruise", True), enabled=False))
  assert not update(adapter, engaged=False).automatic
  assert update(adapter).automatic


def test_disable_clears_camera_without_manual_residue():
  adapter = module.CameraSpeedAdapter()
  update(adapter)
  adapter.observe_car_state(stock(("accelCruise", True)))
  disabled = update(adapter, enabled=False)
  assert disabled.event_id == "" and disabled.raw_limit == 0 and not disabled.manual
  assert disabled.reason == "disabled" and not adapter._active
