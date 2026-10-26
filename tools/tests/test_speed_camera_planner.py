"""Pure planner adapter tests: no cereal, Params, messaging, or vehicle I/O.

python -B -m pytest -c tools/tests/camera_replay_pytest.ini -p no:cacheprovider \
  tools/tests/test_speed_camera_planner.py -q
"""

import math
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS

import pytest


ROOT = Path(__file__).resolve().parents[2]
for name, relative in (
  ('openpilot', '.'),
  ('openpilot.sunnypilot', 'sunnypilot'),
  ('openpilot.sunnypilot.mapd', 'sunnypilot/mapd'),
  ('openpilot.sunnypilot.selfdrive', 'sunnypilot/selfdrive'),
  ('openpilot.sunnypilot.selfdrive.controls', 'sunnypilot/selfdrive/controls'),
  ('openpilot.sunnypilot.selfdrive.controls.lib', 'sunnypilot/selfdrive/controls/lib'),
):
  if name not in sys.modules:
    namespace = ModuleType(name)
    namespace.__path__ = [str(ROOT / relative)]
    sys.modules[name] = namespace

from openpilot.sunnypilot.mapd.speed_cameras import parse_camera_snapshot
from openpilot.sunnypilot.selfdrive.controls.lib.speed_camera_controller import CameraControlOutput, CameraControlState
from openpilot.sunnypilot.selfdrive.controls.lib.speed_camera_planner import SpeedCameraPlanner


EPOCH = 1_800_000_000.0
LATITUDE = 55.0
LONGITUDE = 37.0
EARTH_RADIUS_M = 6_371_000.0
LIMIT_MPS = 60 / 3.6
USER_MPS = 30.0


def latitude(north_m):
  return LATITUDE + math.degrees(north_m / EARTH_RADIUS_M)


def sco_map_data(kind=1, speed_kph=60, north_m=0):
  # Actual seven-column SCO export schema, provider identity, and speed units.
  raw = ('IDX,X,Y,TYPE,SPEED,DIRTYPE,DIRECTION\r\n' +
         f'1,{LONGITUDE},{latitude(north_m)},{kind},{speed_kph},0,0\r\n').encode()
  camera, = parse_camera_snapshot(raw)
  return NS(speedCameraType=camera.camera_type, speedCameraAheadValid=True,
            speedCameraSpeedLimit=(camera.speed_limit_kph or 0) / 3.6,
            speedCameraLatitude=camera.latitude, speedCameraLongitude=camera.longitude,
            speedCameraAheadDistance=100.0, locationValid=True)


@pytest.fixture
def frame():
  def make(*, north_m=-100, time_s=0, kind=1, speed_kph=60, camera_north_m=0,
           fix_offset_s=0, cs_values=None, cc_values=None, gps_values=None, lmd_values=None, **kwargs):
    now = EPOCH + time_s
    cs = NS(vEgo=USER_MPS, gasPressed=False, brakePressed=False, standstill=False, buttonEvents=[],
            cruiseState=NS(enabled=True, available=True, speed=USER_MPS, speedCluster=USER_MPS, standstill=False))
    cc = NS(enabled=True, longActive=True, cruiseControl=NS(override=False, cancel=False, resume=False))
    gps = NS(hasFix=True, horizontalAccuracy=3.0, bearingAccuracyDeg=2.0,
             unixTimestampMillis=int((now + fix_offset_s) * 1000),
             latitude=latitude(north_m), longitude=LONGITUDE, bearingDeg=0.0)
    lmd = sco_map_data(kind, speed_kph, camera_north_m)
    for message, values in ((cs, cs_values), (cc, cc_values), (gps, gps_values), (lmd, lmd_values)):
      for key, value in (values or {}).items():
        setattr(message, key, value)
    options = dict(enabled=True, gps_valid=True, gps_alive=True, lmd_valid=True, lmd_alive=True,
                   lmd_age_s=0.1, user_cruise_mps=USER_MPS, minimum_speed_mps=30 / 3.6,
                   now_s=now, adapt_accel_mps2=-1.0)
    options.update(kwargs)
    return NS(cs=cs, cc=cc, gps=gps, lmd=lmd, options=options)

  return make


def update(planner, data):
  return planner.update(data.cs, data.cc, data.gps, data.lmd, **data.options)


def assert_no_release(out, previous_cap):
  assert out.speed_cap_mps is not None
  assert out.speed_cap_mps <= previous_cap + 1e-9
  assert not out.restore_allowed


@pytest.mark.parametrize('kind', [1, 2, 5])
def test_real_sco_point_types_acquire_ceiling_in_mps(frame, kind):
  data = frame(kind=kind)
  out = update(SpeedCameraPlanner(), data)
  assert isinstance(out, CameraControlOutput)
  assert out.state == CameraControlState.approach
  assert out.speed_cap_mps == pytest.approx(math.sqrt(LIMIT_MPS ** 2 + 200), abs=1e-6)
  assert not out.restore_allowed and not out.manual_takeover
  assert data.cs.cruiseState.speed == USER_MPS
  assert data.options['user_cruise_mps'] == USER_MPS


@pytest.mark.parametrize('kind', [3, 4, 6, 7, 999])
def test_non_speed_and_unsupported_sco_types_never_start_control(frame, kind):
  out = update(SpeedCameraPlanner(), frame(kind=kind, speed_kph=90))
  assert out.speed_cap_mps is None and not out.restore_allowed


@pytest.mark.parametrize('kind', ['18059', '18950', '18958'])
@pytest.mark.parametrize('allowed', [False, True])
def test_cityguide_control_requires_map_parser_eligibility(frame, kind, allowed):
  data = frame(lmd_values={'speedCameraType': f'speedcamonline-cg77:{kind}',
                          'speedCameraAutoControlAllowed': allowed})
  out = update(SpeedCameraPlanner(), data)
  assert (out.speed_cap_mps is not None) is allowed


@pytest.mark.parametrize('kind', ['18925', '18951', '18952', 'unknown', '18059extra'])
def test_cityguide_unknown_type_cannot_be_enabled_by_flag(frame, kind):
  data = frame(lmd_values={'speedCameraType': f'speedcamonline-cg77:{kind}',
                          'speedCameraAutoControlAllowed': True})
  out = update(SpeedCameraPlanner(), data)
  assert out.speed_cap_mps is None


def test_cityguide_old_map_publisher_does_not_acquire_control(frame):
  out = update(SpeedCameraPlanner(), frame(lmd_values={'speedCameraType': 'speedcamonline-cg77:18059'}))
  assert out.speed_cap_mps is None


@pytest.mark.parametrize('kind', ['', '1', 'radarbase:1', 'speedcamonline:11', 'speedcamonline:1extra', 'SpeedCamOnline:1'])
def test_type_must_have_exact_sco_provider_and_eligible_id(frame, kind):
  out = update(SpeedCameraPlanner(), frame(lmd_values={'speedCameraType': kind}))
  assert out.speed_cap_mps is None and not out.restore_allowed


UNRELIABLE = [
  pytest.param({'gps_valid': False}, id='gps-invalid'),
  pytest.param({'gps_alive': False}, id='gps-dead'),
  pytest.param({'lmd_valid': False}, id='lmd-invalid'),
  pytest.param({'lmd_alive': False}, id='lmd-dead'),
  pytest.param({'lmd_values': {'locationValid': False}}, id='location-invalid'),
  pytest.param({'fix_offset_s': -3}, id='unix-fix-stale'),
  pytest.param({'fix_offset_s': 1}, id='unix-fix-future'),
  pytest.param({'gps_values': {'unixTimestampMillis': 0}}, id='unix-fix-zero'),
  pytest.param({'gps_values': {'unixTimestampMillis': int(EPOCH)}}, id='seconds-in-millis-field'),
  pytest.param({'now_s': 123.0}, id='monotonic-now-with-unix-gps'),
  pytest.param({'gps_values': {'hasFix': False}}, id='no-fix'),
  pytest.param({'gps_values': {'horizontalAccuracy': 0}}, id='zero-horizontal-accuracy'),
  pytest.param({'gps_values': {'horizontalAccuracy': -1}}, id='negative-horizontal-accuracy'),
  pytest.param({'gps_values': {'horizontalAccuracy': 21}}, id='poor-horizontal-accuracy'),
  pytest.param({'gps_values': {'horizontalAccuracy': float('nan')}}, id='nan-horizontal-accuracy'),
  pytest.param({'gps_values': {'horizontalAccuracy': float('inf')}}, id='infinite-horizontal-accuracy'),
  pytest.param({'gps_values': {'bearingAccuracyDeg': -1}}, id='negative-bearing-accuracy'),
  pytest.param({'gps_values': {'bearingAccuracyDeg': 31}}, id='poor-bearing-accuracy'),
  pytest.param({'gps_values': {'bearingAccuracyDeg': float('nan')}}, id='nan-bearing-accuracy'),
  pytest.param({'gps_values': {'latitude': float('nan')}}, id='nan-position'),
  pytest.param({'gps_values': {'longitude': 181}}, id='invalid-position'),
  pytest.param({'gps_values': {'bearingDeg': float('nan')}}, id='nan-heading'),
  pytest.param({'lmd_age_s': 3}, id='lmd-stale'),
  pytest.param({'lmd_age_s': -1}, id='lmd-negative-age'),
  pytest.param({'lmd_age_s': float('nan')}, id='lmd-nan-age'),
  pytest.param({'lmd_age_s': float('inf')}, id='lmd-infinite-age'),
]


@pytest.mark.parametrize('overrides', UNRELIABLE)
def test_unreliable_data_neither_acquires_nor_releases_existing_ceiling(frame, overrides):
  fresh = SpeedCameraPlanner()
  out = update(fresh, frame(**overrides))
  assert out.speed_cap_mps is None and not out.restore_allowed
  planner = SpeedCameraPlanner()
  initial = update(planner, frame(north_m=-10))
  for time_s, north_m in ((1, 20), (2, 40)):
    held = update(planner, frame(time_s=time_s, north_m=north_m, **overrides))
    assert_no_release(held, initial.speed_cap_mps)


def test_map_receipt_age_is_converted_to_same_unix_clock_as_gps(frame):
  out = update(SpeedCameraPlanner(), frame(time_s=15, lmd_age_s=0.2))
  assert out.speed_cap_mps is not None
  stale = update(SpeedCameraPlanner(), frame(time_s=15, lmd_age_s=5))
  assert stale.speed_cap_mps is None and not stale.restore_allowed


@pytest.mark.parametrize('button', ['accelCruise', 'decelCruise', 'cancel', 'resumeCruise', 'setCruise'])
def test_short_carstate_button_pulse_is_latched_between_planning_frames(frame, button):
  planner = SpeedCameraPlanner()
  assert update(planner, frame()).speed_cap_mps is not None
  pulse = frame(time_s=0.01, cs_values={'buttonEvents': [NS(type=button, pressed=True)]})
  planner.observe_car_state(pulse.cs, EPOCH + 0.01)
  # All later 100 Hz samples have cleared buttonEvents before model planning.
  for tick in range(2, 20):
    planner.observe_car_state(frame(time_s=tick / 100).cs, EPOCH + tick / 100)
  cancelled = update(planner, frame(time_s=0.2))
  assert cancelled.manual_takeover and cancelled.speed_cap_mps is None
  assert not cancelled.restore_allowed
  for time_s, north in ((1, 20), (2, 40), (3, -100)):
    out = update(planner, frame(time_s=time_s, north_m=north))
    assert out.manual_takeover and out.speed_cap_mps is None and not out.restore_allowed


def test_button_latch_is_effective_near_half_second_boundary(frame):
  planner = SpeedCameraPlanner()
  update(planner, frame())
  cs = frame(cs_values={'buttonEvents': [NS(type='decelCruise', pressed=False)]}).cs
  planner.observe_car_state(cs, EPOCH + 0.01)
  out = update(planner, frame(time_s=0.5))
  assert out.manual_takeover and not out.restore_allowed


@pytest.mark.parametrize('field', ['override', 'cancel', 'resume'])
def test_carcontrol_manual_flags_cancel_without_later_restore(frame, field):
  planner = SpeedCameraPlanner()
  update(planner, frame())
  manual = frame(time_s=0.2)
  setattr(manual.cc.cruiseControl, field, True)
  cancelled = update(planner, manual)
  assert cancelled.manual_takeover and cancelled.speed_cap_mps is None and not cancelled.restore_allowed
  for time_s, north in ((1, 20), (2, 40)):
    after = update(planner, frame(time_s=time_s, north_m=north))
    assert after.manual_takeover and after.speed_cap_mps is None and not after.restore_allowed


@pytest.mark.parametrize('pedal', ['gasPressed', 'brakePressed'])
def test_manual_pedal_cancels_and_never_restores_saved_cruise(frame, pedal):
  planner = SpeedCameraPlanner()
  update(planner, frame())
  manual = frame(time_s=1, cs_values={pedal: True})
  out = update(planner, manual)
  assert out.manual_takeover and out.speed_cap_mps is None and not out.restore_allowed
  assert manual.cs.cruiseState.speed == USER_MPS
  assert update(planner, frame(time_s=2, north_m=40)).manual_takeover


@pytest.mark.parametrize('source', ['carState', 'carControl'])
def test_acc_disengage_removes_cap_without_restoration_and_new_session_can_rearm(frame, source):
  planner = SpeedCameraPlanner()
  assert update(planner, frame()).speed_cap_mps is not None
  off = frame(time_s=1)
  if source == 'carState':
    off.cs.cruiseState.enabled = False
  else:
    off.cc.enabled = False
  result = update(planner, off)
  assert result.state == CameraControlState.inactive
  assert result.speed_cap_mps is None and not result.restore_allowed
  assert update(planner, frame(time_s=2)).speed_cap_mps is not None


def test_pass_requires_two_distinct_fresh_behind_fixes_and_restores_only_once(frame):
  planner = SpeedCameraPlanner()
  before = update(planner, frame(north_m=-1))
  no_camera = {'speedCameraAheadValid': False}
  first = update(planner, frame(north_m=20, time_s=1, lmd_values=no_camera))
  assert_no_release(first, before.speed_cap_mps)
  for tick in range(1, 10):
    duplicate = frame(north_m=20, time_s=1 + tick / 20, lmd_values=no_camera,
                      gps_values={'unixTimestampMillis': int((EPOCH + 1) * 1000)})
    assert_no_release(update(planner, duplicate), first.speed_cap_mps)
  current = frame(north_m=40, time_s=2, lmd_values=no_camera, user_cruise_mps=22)
  pending = update(planner, current)
  assert pending.state == CameraControlState.hold and pending.reason == 'awaiting_map_update'
  assert_no_release(pending, first.speed_cap_mps)
  passed = update(planner, frame(north_m=50, time_s=3, lmd_age_s=0,
                                  lmd_values=no_camera, user_cruise_mps=22))
  assert passed.state == CameraControlState.passed and passed.restore_allowed
  assert passed.speed_cap_mps is None and not passed.manual_takeover
  assert current.options['user_cruise_mps'] == 22 and current.cs.cruiseState.speed == USER_MPS
  assert not update(planner, frame(north_m=60, time_s=4, lmd_values=no_camera)).restore_allowed


def test_bad_gps_between_behind_fixes_requires_two_new_fresh_pass_confirmations(frame):
  planner = SpeedCameraPlanner()
  initial = update(planner, frame(north_m=-10))
  no_camera = {'speedCameraAheadValid': False}
  assert_no_release(update(planner, frame(north_m=20, time_s=1, lmd_values=no_camera)), initial.speed_cap_mps)
  stale = update(planner, frame(north_m=25, time_s=2, fix_offset_s=-5, lmd_values=no_camera))
  assert_no_release(stale, initial.speed_cap_mps)
  recovered = update(planner, frame(north_m=30, time_s=3, lmd_values=no_camera))
  assert_no_release(recovered, initial.speed_cap_mps)
  pending = update(planner, frame(north_m=40, time_s=4, lmd_values=no_camera))
  assert_no_release(pending, recovered.speed_cap_mps)
  assert pending.reason == 'awaiting_map_update'
  passed = update(planner, frame(north_m=50, time_s=5, lmd_age_s=0, lmd_values=no_camera))
  assert passed.restore_allowed and passed.state == CameraControlState.passed
  assert passed.speed_cap_mps is None


@pytest.mark.parametrize('lmd_values', [
  {'speedCameraSpeedLimit': 0}, {'speedCameraSpeedLimit': float('nan')},
  {'speedCameraAheadValid': False}, {'speedCameraType': 'speedcamonline:3'},
])
def test_unknown_or_ineligible_observation_never_accelerates_active_episode(frame, lmd_values):
  planner = SpeedCameraPlanner()
  cap = update(planner, frame(north_m=-10)).speed_cap_mps
  for time_s in (1, 2, 10_000):
    held = update(planner, frame(north_m=-5, time_s=time_s, lmd_values=lmd_values))
    assert_no_release(held, cap)
    cap = held.speed_cap_mps


@pytest.mark.parametrize('speed', [0, float('nan'), -1, 301 / 3.6, 5.0])
def test_unknown_out_of_range_or_below_minimum_camera_limit_never_acquires(frame, speed):
  out = update(SpeedCameraPlanner(), frame(lmd_values={'speedCameraSpeedLimit': speed}))
  assert out.speed_cap_mps is None and not out.restore_allowed


def test_second_camera_cannot_replace_saved_geometry_before_first_pass(frame):
  planner = SpeedCameraPlanner()
  first = update(planner, frame(north_m=-10))
  changed = update(planner, frame(north_m=20, time_s=1, camera_north_m=65, kind=5, speed_kph=36))
  assert changed.camera_id == first.camera_id
  assert_no_release(changed, first.speed_cap_mps)
  # On the second behind fix, acquire the new camera in this SAME frame.
  next_camera = update(planner, frame(north_m=40, time_s=2, camera_north_m=65, kind=5, speed_kph=36))
  assert next_camera.camera_id != first.camera_id
  assert next_camera.state == CameraControlState.approach
  assert next_camera.speed_cap_mps == pytest.approx(math.sqrt(10 ** 2 + 2 * 25), abs=1e-6)
  assert not next_camera.restore_allowed and not next_camera.manual_takeover


def test_nearest_camera_identity_jump_without_vehicle_movement_cannot_confirm_pass(frame):
  planner = SpeedCameraPlanner()
  first = update(planner, frame(north_m=-100, kind=1))
  for time_s, camera_north in enumerate((65, -150, 500), start=1):
    changed = update(planner, frame(north_m=-100, time_s=time_s, kind=5,
                                    camera_north_m=camera_north, speed_kph=40))
    assert changed.camera_id == first.camera_id
    assert_no_release(changed, first.speed_cap_mps)


def test_user_ceiling_cannot_be_raised_by_camera_jitter_or_new_higher_user_value(frame):
  planner = SpeedCameraPlanner()
  initial = update(planner, frame(north_m=-10))
  lower = update(planner, frame(north_m=-8, time_s=1, user_cruise_mps=15))
  assert lower.speed_cap_mps == 15
  later = update(planner, frame(north_m=-20, time_s=2, user_cruise_mps=35))
  assert later.speed_cap_mps == 15 <= initial.speed_cap_mps


def test_caller_deceleration_is_forwarded_without_new_default(frame):
  out = update(SpeedCameraPlanner(), frame(adapt_accel_mps2=-0.5))
  assert out.speed_cap_mps == pytest.approx(math.sqrt(LIMIT_MPS ** 2 + 100), abs=1e-6)
  for accel in (0, 1, float('nan')):
    out = update(SpeedCameraPlanner(), frame(adapt_accel_mps2=accel))
    assert out.speed_cap_mps is None and not out.restore_allowed


def test_disabled_switch_never_starts_control(frame):
  out = update(SpeedCameraPlanner(), frame(enabled=False))
  assert out.speed_cap_mps is None and not out.restore_allowed


RESTORE_USER_MPS = 110 / 3.6
RESTORE_STOCK_MPS = 72 / 3.6


def restoration_frame(frame, *, stock_mps=RESTORE_STOCK_MPS, **kwargs):
  data = frame(user_cruise_mps=RESTORE_USER_MPS, speed_kph=72, **kwargs)
  data.cs.vEgo = stock_mps
  data.cs.cruiseState.speed = stock_mps
  data.cs.cruiseState.speedCluster = stock_mps
  return data


def enter_post_pass_restoration(planner, frame):
  update(planner, restoration_frame(frame, north_m=-10, lmd_age_s=0))
  outcomes = []
  for time_s, north_m in ((1, 20), (2, 40), (3, 45)):
    outcomes.append(update(planner, restoration_frame(
      frame, north_m=north_m, time_s=time_s, lmd_age_s=0,
      lmd_values={'speedCameraAheadValid': False})))
  # Support both the old immediate release and the repaired receipt barrier
  # here, so handover regressions independently expose the restoration bug.
  # The separate receipt tests prohibit the old immediate release.
  assert any(out.restore_allowed for out in outcomes)
  assert outcomes[-1].speed_cap_mps is None


@pytest.mark.parametrize('receipt', ['age', 'generation'])
@pytest.mark.parametrize('old_observation', ['camera-1', 'no-camera'])
def test_p1_old_lmd_receipt_holds_geometric_pass_until_new_second_camera(frame, receipt, old_observation):
  planner = SpeedCameraPlanner()

  def data_at(time_s, north_m, *, new_receipt=False):
    options = {'lmd_generation': 1234.0 + (time_s if new_receipt else 0)} if receipt == 'generation' else {}
    values = {'speedCameraAheadValid': False} if old_observation == 'no-camera' else {}
    return restoration_frame(frame, time_s=time_s, north_m=north_m,
                             lmd_age_s=0 if new_receipt else time_s,
                             lmd_values=values if time_s else {}, **options)

  initial = update(planner, data_at(0, -10))
  assert initial.speed_cap_mps is not None
  previous_cap = initial.speed_cap_mps
  # Same monotonic generation / same Unix receipt time; two distinct GPS fixes
  # prove passage, but the provider has not supplied its post-pass selection.
  for time_s, north_m in ((1, 20), (2, 40), (2.25, 45)):
    held = update(planner, data_at(time_s, north_m))
    assert_no_release(held, previous_cap)
    previous_cap = held.speed_cap_mps

  next_data = data_at(2.5, 50, new_receipt=True)
  next_data.lmd = sco_map_data(kind=5, speed_kph=36, north_m=80)
  next_out = update(planner, next_data)
  assert next_out.camera_id != initial.camera_id
  assert next_out.speed_cap_mps == pytest.approx(math.sqrt(10 ** 2 + 2 * 30), abs=1e-6)
  assert next_out.speed_cap_mps < RESTORE_USER_MPS
  assert not next_out.restore_allowed and not next_out.manual_takeover


@pytest.mark.parametrize('button', ['accelCruise', 'decelCruise'])
def test_p1_manual_plus_minus_during_stock72_to_user110_restoration_hands_over(frame, button):
  planner = SpeedCameraPlanner()
  enter_post_pass_restoration(planner, frame)
  tap = restoration_frame(frame, time_s=3.01, cs_values={'buttonEvents': [NS(type=button, pressed=True)]})
  planner.observe_car_state(tap.cs, EPOCH + 3.01)
  # The planning frame sees neither the original button press nor an active cap.
  out = update(planner, restoration_frame(frame, north_m=50, time_s=3.2,
                                          lmd_values={'speedCameraAheadValid': False}))
  assert out.manual_takeover, 'manual +/- must also revoke post-pass restoration authority'
  assert out.speed_cap_mps is None and not out.restore_allowed
  later = update(planner, restoration_frame(frame, north_m=60, time_s=4,
                                            lmd_values={'speedCameraAheadValid': False}))
  assert later.manual_takeover and not later.restore_allowed


@pytest.mark.parametrize(('remaining_mps', 'handover'), [(0.51, True), (0.49, False)])
def test_p1_restoration_completion_uses_speed_cluster_and_half_mps_tolerance(frame, remaining_mps, handover):
  planner = SpeedCameraPlanner()
  enter_post_pass_restoration(planner, frame)
  stock = RESTORE_USER_MPS - remaining_mps
  approaching_target = restoration_frame(frame, north_m=50, time_s=4, stock_mps=stock,
                                         lmd_values={'speedCameraAheadValid': False})
  # Ordinary speed already equals the user setting; cluster is the completion
  # measurement, in m/s. Using speed or comparing with 110 km/h is incorrect.
  approaching_target.cs.cruiseState.speed = RESTORE_USER_MPS
  update(planner, approaching_target)
  tap = restoration_frame(frame, time_s=4.01, stock_mps=stock,
                          cs_values={'buttonEvents': [NS(type='decelCruise', pressed=True)]})
  planner.observe_car_state(tap.cs, EPOCH + 4.01)
  final = restoration_frame(frame, north_m=55, time_s=4.2, stock_mps=stock,
                            lmd_values={'speedCameraAheadValid': False})
  final.cs.cruiseState.speed = RESTORE_USER_MPS
  out = update(planner, final)
  assert out.manual_takeover is handover
  assert out.speed_cap_mps is None and not out.restore_allowed


@pytest.mark.parametrize('pedal', ['gasPressed', 'brakePressed'])
def test_p1_short_pedal_pulse_observed_between_model_frames_cancels_active_cap(frame, pedal):
  planner = SpeedCameraPlanner()
  assert update(planner, frame(north_m=-10)).speed_cap_mps is not None
  pulse = frame(time_s=0.01, cs_values={pedal: True})
  planner.observe_car_state(pulse.cs, EPOCH + 0.01)
  for tick in range(2, 20):
    planner.observe_car_state(frame(time_s=tick / 100).cs, EPOCH + tick / 100)
  out = update(planner, frame(north_m=-5, time_s=0.2))
  assert out.manual_takeover, 'a 100 Hz pedal pulse must survive until the next planning frame'
  assert out.speed_cap_mps is None and not out.restore_allowed
  for time_s, north_m in ((1, 20), (2, 40)):
    after = update(planner, frame(time_s=time_s, north_m=north_m))
    assert after.manual_takeover and after.speed_cap_mps is None and not after.restore_allowed
