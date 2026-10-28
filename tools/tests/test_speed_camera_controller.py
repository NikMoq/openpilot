"""Portable tests, without cereal, Params, native extensions, or vehicle I/O.

python -B -m pytest -c tools/tests/camera_replay_pytest.ini -p no:cacheprovider \
  tools/tests/test_speed_camera_controller.py -q
"""

import importlib.util
import math
import sys
from dataclasses import replace
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[2] / "sunnypilot/selfdrive/controls/lib/speed_camera_controller.py"
SPEC = importlib.util.spec_from_file_location("speed_camera_controller_under_test", MODULE_PATH)
controller_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = controller_module
SPEC.loader.exec_module(controller_module)

CameraControlConfig = controller_module.CameraControlConfig
CameraControlInput = controller_module.CameraControlInput
CameraControlState = controller_module.CameraControlState
CameraObservation = controller_module.CameraObservation
SpeedCameraController = controller_module.SpeedCameraController

# Supplied by the caller in production; deliberately not a controller default.
ADAPT_ACCEL = -1.0
RADIUS = controller_module.EARTH_RADIUS_M
EPOCH = 1_800_000_000.0
CAMERA = CameraObservation(camera_id="point-1", latitude=55.0, longitude=37.0, speed_limit_mps=20.0)


def sample(north_m=-300.0, east_m=0.0, time_s=0.0, **overrides):
  values = dict(enabled=True, acc_engaged=True, gps_valid=True, gps_alive=True, lmd_valid=True, lmd_alive=True,
                now_s=EPOCH + time_s, gps_fix_time_s=EPOCH + time_s, lmd_time_s=EPOCH + time_s,
                latitude=CAMERA.latitude + math.degrees(north_m / RADIUS),
                longitude=CAMERA.longitude + math.degrees(east_m / (RADIUS * math.cos(math.radians(CAMERA.latitude)))),
                heading_deg=0.0, user_cruise_mps=30.0, camera=CAMERA)
  values.update(overrides)
  return CameraControlInput(**values)


def update(controller, data, accel=ADAPT_ACCEL):
  return controller.update(data, adapt_accel_mps2=accel)


def test_approach_ceiling_follows_kinematics_and_holds_until_geometric_pass():
  controller = SpeedCameraController()
  outputs = [update(controller, sample(north_m=-distance, time_s=i)) for i, distance in enumerate((300, 100, 25, 0))]
  assert [out.speed_cap_mps for out in outputs] == pytest.approx([30, math.sqrt(600), math.sqrt(450), 20])
  assert all(not out.restore_allowed for out in outputs)
  assert outputs[-1].state == CameraControlState.hold
  # The publisher may already have dropped the point from its ahead selection.
  first_behind = update(controller, sample(north_m=20, time_s=4, camera=None))
  assert first_behind.speed_cap_mps == 20 and not first_behind.restore_allowed
  passed = update(controller, sample(north_m=40, time_s=5, camera=None))
  assert passed.state == CameraControlState.passed and passed.restore_allowed
  assert passed.speed_cap_mps is None and not passed.manual_takeover
  assert not update(controller, sample(north_m=60, time_s=6)).restore_allowed


@pytest.mark.parametrize("action", ["manual_button", "override"])
def test_manual_cancel_removes_authority_without_restoring_or_rearming_same_camera(action):
  controller = SpeedCameraController()
  update(controller, sample(north_m=-10))
  cancelled = update(controller, sample(north_m=-5, time_s=1, **{action: True}))
  assert cancelled.state == CameraControlState.cancelled
  assert cancelled.speed_cap_mps is None and cancelled.manual_takeover and not cancelled.restore_allowed
  for i, north in enumerate((-1, 20, 40, -100), 2):
    after = update(controller, sample(north_m=north, time_s=i))
    assert after.speed_cap_mps is None and after.manual_takeover and not after.restore_allowed


@pytest.mark.parametrize("flag", ["enabled", "acc_engaged"])
def test_disable_or_acc_disengage_cancels_current_episode(flag):
  controller = SpeedCameraController()
  assert update(controller, sample(**{flag: False})).speed_cap_mps is None
  update(controller, sample())
  result = update(controller, sample(time_s=1, **{flag: False}))
  assert result.manual_takeover and not result.restore_allowed and result.speed_cap_mps is None


@pytest.mark.parametrize("overrides", [
  {"gps_valid": False}, {"gps_alive": False}, {"lmd_valid": False}, {"lmd_alive": False},
  {"gps_fix_time_s": EPOCH - 3}, {"lmd_time_s": EPOCH - 3},
  {"gps_fix_time_s": EPOCH + 1}, {"lmd_time_s": EPOCH + 1},
  {"gps_fix_time_s": None}, {"lmd_time_s": None},
  {"latitude": float("nan")}, {"longitude": 181}, {"heading_deg": None},
])
def test_invalid_or_stale_data_cannot_acquire_and_cannot_release_an_existing_cap(overrides):
  assert update(SpeedCameraController(), sample(**overrides)).speed_cap_mps is None
  controller = SpeedCameraController()
  before = update(controller, sample(north_m=-10))
  held = update(controller, sample(north_m=40, **overrides))
  assert held.state == CameraControlState.hold
  assert held.speed_cap_mps == before.speed_cap_mps and not held.restore_allowed


def test_no_timer_or_missing_camera_completion_and_fresh_gps_can_later_prove_pass():
  controller = SpeedCameraController()
  cap = update(controller, sample(north_m=0)).speed_cap_mps
  assert cap is None  # Never acquire an already-passed/at-camera point.
  cap = update(controller, sample(north_m=-1)).speed_cap_mps
  stale = sample(north_m=40, time_s=1_000_000, gps_fix_time_s=EPOCH, camera=None)
  held = update(controller, stale)
  assert held.speed_cap_mps == cap and not held.restore_allowed
  first = update(controller, sample(north_m=40, time_s=1_000_001, camera=None))
  assert first.speed_cap_mps == 20 and not first.restore_allowed
  passed = update(controller, sample(north_m=60, time_s=1_000_002, camera=None))
  assert passed.restore_allowed


def test_planning_ticks_reusing_same_gps_fix_cannot_confirm_pass():
  controller = SpeedCameraController()
  update(controller, sample(north_m=-5))
  update(controller, sample(north_m=20, time_s=1))
  for tick in range(20):
    out = update(controller, sample(north_m=20, time_s=1 + tick / 20, gps_fix_time_s=EPOCH + 1))
    assert not out.restore_allowed and out.speed_cap_mps == 20
  assert update(controller, sample(north_m=40, time_s=2)).restore_allowed


@pytest.mark.parametrize("overrides", [{"east_m": 80}, {"heading_deg": 180}])
def test_parallel_or_opposite_camera_is_not_acquired(overrides):
  result = update(SpeedCameraController(), sample(**overrides))
  assert result.speed_cap_mps is None and not result.restore_allowed


@pytest.mark.parametrize("overrides", [{"east_m": 80}, {"heading_deg": 180}, {"north_m": 300}])
def test_diversion_heading_flip_or_far_jump_does_not_complete_pass(overrides):
  controller = SpeedCameraController()
  before = update(controller, sample(north_m=-1))
  values = dict(north_m=40, time_s=1)
  values.update(overrides)
  for t in (1, 2):
    values["time_s"] = t
    out = update(controller, sample(**values))
    assert not out.restore_allowed
    assert out.speed_cap_mps <= before.speed_cap_mps


def test_user_ceiling_and_jitter_never_increase_target_before_pass():
  controller = SpeedCameraController()
  first = update(controller, sample(north_m=-100))
  jitter = update(controller, sample(north_m=-120, time_s=1))
  assert jitter.speed_cap_mps == first.speed_cap_mps
  lower = update(controller, sample(north_m=-90, time_s=2, user_cruise_mps=15))
  assert lower.speed_cap_mps == 15
  assert update(controller, sample(north_m=-80, time_s=3, user_cruise_mps=35)).speed_cap_mps == 15
  assert update(SpeedCameraController(), sample(user_cruise_mps=15)).speed_cap_mps is None


@pytest.mark.parametrize("limit", [None, 0, -1, float("nan"), float("inf")])
def test_unknown_limit_cannot_acquire(limit):
  out = update(SpeedCameraController(), sample(camera=replace(CAMERA, speed_limit_mps=limit)))
  assert out.speed_cap_mps is None and not out.restore_allowed


def test_unknown_observation_or_changed_nearest_camera_does_not_release_tracked_camera():
  controller = SpeedCameraController()
  update(controller, sample(north_m=-30))
  unknown = update(controller, sample(north_m=-10, time_s=1, camera=replace(CAMERA, speed_limit_mps=None)))
  different = replace(CAMERA, camera_id="other", latitude=55.01)
  held = update(controller, sample(north_m=-5, time_s=2, camera=different))
  assert held.camera_id == CAMERA.key and held.speed_cap_mps <= unknown.speed_cap_mps
  assert not held.restore_allowed


def test_average_speed_sections_are_not_controlled():
  out = update(SpeedCameraController(), sample(camera=replace(CAMERA, is_average_speed_section=True)))
  assert out.speed_cap_mps is None and out.reason == "average_speed_unsupported"


def test_acceleration_argument_is_required_and_does_not_have_a_new_deceleration_default():
  controller = SpeedCameraController()
  with pytest.raises(TypeError):
    controller.update(sample())
  for accel in (0, 1, float("nan")):
    assert update(controller, sample(), accel=accel).speed_cap_mps is None
  out = update(controller, sample(north_m=-100), accel=-0.5)
  assert out.speed_cap_mps == pytest.approx(math.sqrt(500))


def test_missing_eligibility_flags_fail_closed():
  assert update(SpeedCameraController(), CameraControlInput()).speed_cap_mps is None


def test_out_of_order_fix_does_not_confirm_pass():
  controller = SpeedCameraController()
  update(controller, sample(north_m=-1, time_s=1))
  held = update(controller, sample(north_m=30, now_s=EPOCH + 1, gps_fix_time_s=EPOCH, lmd_time_s=EPOCH + 1))
  assert held.reason == "gps_out_of_order" and not held.restore_allowed


def test_manual_cancel_does_not_block_a_different_camera():
  controller = SpeedCameraController()
  update(controller, sample())
  update(controller, sample(time_s=1, manual_button=True))
  new_camera = replace(CAMERA, camera_id="point-2", latitude=55.001)
  result = update(controller, sample(time_s=2, camera=new_camera))
  assert result.camera_id == "point-2" and result.speed_cap_mps is not None and not result.manual_takeover


def test_gas_temporarily_suspends_without_suppressing_saved_camera():
  controller = SpeedCameraController()
  initial = update(controller, sample(north_m=-10, user_cruise_mps=130 / 3.6))
  suspended = update(controller, sample(north_m=-5, time_s=1, gas_pressed=True, user_cruise_mps=130 / 3.6))
  assert suspended.state == CameraControlState.manual
  assert suspended.camera_id == initial.camera_id and suspended.speed_cap_mps is None
  assert suspended.manual_takeover and not suspended.restore_allowed
  resumed = update(controller, sample(north_m=-1, time_s=2, camera=None, user_cruise_mps=130 / 3.6))
  assert resumed.camera_id == initial.camera_id and resumed.speed_cap_mps <= initial.speed_cap_mps
  assert not resumed.manual_takeover and not resumed.restore_allowed


@pytest.mark.parametrize('action', ['gas_pressed', 'brake_pressed', 'speed_button'])
def test_pass_is_observed_during_manual_authority_and_remembered_beyond_pass_window(action):
  controller = SpeedCameraController()
  initial = update(controller, sample(north_m=-10, user_cruise_mps=130 / 3.6))
  for time_s, north in ((1, -5), (2, 20), (3, 40), (4, 400), (5, 1000)):
    data = sample(north_m=north, time_s=time_s, camera=None, user_cruise_mps=130 / 3.6,
                  acc_engaged=action != 'brake_pressed', **{action: True})
    out = update(controller, data)
    assert out.state == CameraControlState.manual and out.manual_takeover
    assert out.camera_id == initial.camera_id and out.speed_cap_mps is None and not out.restore_allowed
    assert data.user_cruise_mps == 130 / 3.6
  if action == 'brake_pressed':
    still_off = update(controller, sample(north_m=1100, time_s=6, acc_engaged=False))
    assert still_off.reason == 'brake_waiting_resume' and not still_off.restore_allowed
    not_res = update(controller, sample(north_m=1200, time_s=7))
    assert not_res.reason == 'brake_waiting_resume' and not not_res.restore_allowed
  passed = update(controller, sample(north_m=1300, time_s=8, camera=None,
                                     resume_requested=action == 'brake_pressed', user_cruise_mps=130 / 3.6))
  assert passed.state == CameraControlState.passed and passed.restore_allowed
  assert passed.camera_id == initial.camera_id and not passed.manual_takeover and passed.speed_cap_mps is None
  assert not update(controller, sample(north_m=1400, time_s=9)).restore_allowed


def test_brake_release_requires_physical_resume_and_engagement_then_reapplies_saved_geometry():
  controller = SpeedCameraController()
  initial = update(controller, sample(north_m=-100))
  update(controller, sample(north_m=-90, time_s=1, brake_pressed=True, acc_engaged=False))
  for time_s, engaged, resume in ((2, False, False), (3, False, True), (4, True, False)):
    waiting = update(controller, sample(north_m=-80, time_s=time_s, acc_engaged=engaged, resume_requested=resume))
    assert waiting.state == CameraControlState.manual and waiting.reason == 'brake_waiting_resume'
    assert waiting.speed_cap_mps is None and not waiting.restore_allowed
  resumed = update(controller, sample(north_m=-70, time_s=5, resume_requested=True, camera=None))
  assert resumed.camera_id == initial.camera_id and resumed.speed_cap_mps is not None
  assert not resumed.manual_takeover and not resumed.restore_allowed


def test_missing_or_duplicate_fixes_during_gas_do_not_invent_pass():
  controller = SpeedCameraController()
  update(controller, sample(north_m=-10))
  update(controller, sample(north_m=20, time_s=1, gas_pressed=True))
  duplicate = update(controller, sample(north_m=40, time_s=1.1, gas_pressed=True, gps_fix_time_s=EPOCH + 1))
  assert not duplicate.restore_allowed
  update(controller, sample(north_m=50, time_s=2, gas_pressed=True, gps_valid=False))
  released = update(controller, sample(north_m=400, time_s=3, camera=None))
  assert released.state == CameraControlState.hold and not released.restore_allowed
  assert released.speed_cap_mps is not None


def test_manual_speed_override_does_not_change_saved_geometry_when_map_selects_another_camera():
  controller = SpeedCameraController()
  initial = update(controller, sample(north_m=-10))
  update(controller, sample(north_m=-5, time_s=1, speed_button=True))
  another = replace(CAMERA, camera_id='other', latitude=54.999)
  for time_s in (2, 3, 4):
    out = update(controller, sample(north_m=-5, time_s=time_s, camera=another))
    assert out.camera_id == initial.camera_id and out.state == CameraControlState.manual
    assert out.speed_cap_mps is None and not out.restore_allowed


@pytest.mark.parametrize('enabled_at_brake', [False, True])
def test_brake_without_camera_episode_does_not_require_res_for_first_camera(enabled_at_brake):
  controller = SpeedCameraController()
  update(controller, sample(camera=None, brake_pressed=True, acc_engaged=False, enabled=enabled_at_brake))
  assert not controller.waiting_for_resume and not controller.episode_active
  first = update(controller, sample(time_s=2))
  assert first.state == CameraControlState.approach and first.speed_cap_mps is not None
  assert not first.manual_takeover and not first.restore_allowed


@pytest.mark.parametrize("overrides", [{"gps_max_age_s": 0}, {"heading_tolerance_deg": 90},
                                      {"pass_margin_m": 250}, {"lookahead_m": float("nan")}])
def test_invalid_configuration_is_rejected(overrides):
  with pytest.raises(ValueError):
    CameraControlConfig(**overrides)
