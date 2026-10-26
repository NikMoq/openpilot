"""Real pycapnp wire/planner smoke; run separately from namespace-based tests.

Linux Python 3.12, pycapnp, pytest and numpy; repository packages on PYTHONPATH.
The isolated checkout must resolve cereal/car.capnp to opendbc's real schema.
python -B -m pytest -c tools/tests/camera_replay_pytest.ini -p no:cacheprovider \
  tools/tests/test_camera_schema_integration.py -q

No messaging/Params substitutes: this verifies schemas and SpeedCameraPlanner,
not LongitudinalPlannerSP construction or its production publish path.
"""

from contextlib import ExitStack
import math

import pytest

from cereal import car, custom, log
from openpilot.sunnypilot.selfdrive.controls.lib.speed_camera_planner import SpeedCameraPlanner


EPOCH = 1_800_000_000.0
LAT, LON = 55.0, 37.0
USER_SPEED = 30.0
LIMIT = 60 / 3.6


def latitude(north_m):
  return LAT + math.degrees(north_m / 6_371_000.0)


@pytest.fixture
def wire():
  """Keep the actual serialized readers alive through every planner call."""
  with ExitStack() as stack:
    def decode(schema, builder):
      payload = builder.to_bytes()
      assert isinstance(payload, bytes) and payload
      reader = stack.enter_context(schema.from_bytes(payload))
      assert reader.schema.node.id == schema.schema.node.id
      return reader
    yield decode


@pytest.mark.parametrize('valid', [False, True])
def test_live_map_location_roundtrip(wire, valid):
  message = custom.LiveMapDataSP.new_message(
    locationValid=valid, speedCameraLatitude=LAT, speedCameraLongitude=LON,
    speedCameraAheadValid=True, speedCameraSpeedLimit=LIMIT,
    speedCameraType='speedcamonline:1')
  decoded = wire(custom.LiveMapDataSP, message)
  assert decoded.locationValid is valid
  assert decoded.speedCameraLatitude == LAT
  assert decoded.speedCameraLongitude == LON
  assert decoded.speedCameraSpeedLimit == pytest.approx(LIMIT)
  assert decoded.speedCameraType == 'speedcamonline:1'


@pytest.mark.parametrize('state,reason,cap,manual', [
  ('inactive', 'disabled', 0.0, False),
  ('approach', 'kinematic_ceiling', LIMIT, False),
  ('hold', 'gps_unreliable', LIMIT, False),
  ('cancelled', 'gas', 0.0, True),
  ('passed', 'geometric_pass', 0.0, False),
])
def test_plan_control_roundtrip_in_event(wire, state, reason, cap, manual):
  event = log.Event.new_message(valid=True, logMonoTime=123456789)
  control = event.init('longitudinalPlanSP').speedCameraControl
  control.enabled = True
  control.active = cap > 0
  control.speedCap = cap
  control.state = state
  control.reason = reason
  control.manualTakeover = manual
  decoded = wire(log.Event, event)
  assert decoded.which() == 'longitudinalPlanSP'
  assert decoded.valid and decoded.logMonoTime == 123456789
  result = decoded.longitudinalPlanSP.speedCameraControl
  assert result.enabled and result.active == (cap > 0)
  assert result.speedCap == pytest.approx(cap)
  assert (result.state, result.reason, result.manualTakeover) == (state, reason, manual)


def frame(wire, *, north_m=-100, time_s=0, kind=1, location_valid=True,
          gas=False, button=False, camera_valid=True, auto_allowed=None, control_values=None, **options):
  now = EPOCH + time_s
  cs = car.CarState.new_message(vEgo=USER_SPEED, gasPressed=gas)
  cs.cruiseState.enabled = True
  cs.cruiseState.speed = USER_SPEED
  cs.cruiseState.speedCluster = USER_SPEED
  if button:
    event = cs.init('buttonEvents', 1)[0]
    event.type = 'accelCruise'
    event.pressed = True
  cc = car.CarControl.new_message(enabled=True)
  gps = log.GpsLocationData.new_message(
    hasFix=True, horizontalAccuracy=3.0, bearingAccuracyDeg=2.0,
    unixTimestampMillis=int(now * 1000), latitude=latitude(north_m),
    longitude=LON, bearingDeg=0.0)
  lmd = custom.LiveMapDataSP.new_message(
    locationValid=location_valid, speedCameraAheadValid=camera_valid,
    speedCameraLatitude=LAT, speedCameraLongitude=LON,
    speedCameraType=kind if isinstance(kind, str) else f'speedcamonline:{kind}', speedCameraSpeedLimit=LIMIT)
  if auto_allowed is not None:
    lmd.speedCameraAutoControlAllowed = auto_allowed
  for key, value in (control_values or {}).items():
    setattr(lmd, key, value)
  kwargs = dict(enabled=True, gps_valid=True, gps_alive=True, lmd_valid=True,
                lmd_alive=True, lmd_age_s=0.1, lmd_generation=time_s,
                user_cruise_mps=USER_SPEED, minimum_speed_mps=30 / 3.6,
                now_s=now, adapt_accel_mps2=-1.0)
  kwargs.update(options)
  messages = tuple(wire(schema, msg) for schema, msg in (
    (car.CarState, cs), (car.CarControl, cc),
    (log.GpsLocationData, gps), (custom.LiveMapDataSP, lmd)))
  return messages, kwargs


def update(planner, data):
  messages, options = data
  return planner.update(*messages, **options)


@pytest.mark.parametrize('kind', [1, 2, 5])
def test_decoded_point_camera_acquires_real_planner_ceiling(wire, kind):
  data = frame(wire, kind=kind)
  output = update(SpeedCameraPlanner(), data)
  assert output.state.value == 'approach'
  assert output.speed_cap_mps == pytest.approx(math.sqrt(LIMIT ** 2 + 200), abs=1e-5)
  assert output.reason == 'kinematic_ceiling'
  assert not output.manual_takeover and not output.restore_allowed
  assert data[0][0].cruiseState.speed == USER_SPEED


def test_decoded_invalid_location_prevents_acquisition(wire):
  output = update(SpeedCameraPlanner(), frame(wire, location_valid=False))
  assert output.speed_cap_mps is None
  assert output.reason == 'lmd_unreliable'


@pytest.mark.parametrize('allowed', [None, False, True])
def test_auto_control_flag_wire_default_and_explicit_values(wire, allowed):
  message = custom.LiveMapDataSP.new_message()
  if allowed is not None:
    message.speedCameraAutoControlAllowed = allowed
  result = wire(custom.LiveMapDataSP, message)
  assert result.speedCameraAutoControlAllowed is (allowed is True)


@pytest.mark.parametrize('kind', ['18059', '18950', '18958', '99999'])
@pytest.mark.parametrize('allowed', [None, True])
def test_cityguide_decoded_flag_and_known_type_are_both_required(wire, kind, allowed):
  output = update(SpeedCameraPlanner(), frame(
    wire, kind=f'speedcamonline-cg77:{kind}', auto_allowed=allowed))
  if allowed and kind in ('18059', '18950', '18958'):
    assert output.state.value == 'approach'
    assert output.speed_cap_mps == pytest.approx(math.sqrt(LIMIT ** 2 + 200), abs=1e-5)
  else:
    assert output.state.value == 'inactive'
    assert output.reason == 'camera_missing'
    assert output.speed_cap_mps is None


def test_decoded_loss_of_location_holds_existing_ceiling(wire):
  planner = SpeedCameraPlanner()
  initial = update(planner, frame(wire))
  output = update(planner, frame(wire, time_s=1, location_valid=False))
  assert output.state.value == 'hold'
  assert output.speed_cap_mps == initial.speed_cap_mps
  assert output.reason == 'lmd_unreliable'
  assert not output.restore_allowed


@pytest.mark.parametrize('action,reason', [('gas', 'gas'), ('button', 'manual_button')])
def test_decoded_manual_action_cancels_and_roundtrips_output(wire, action, reason):
  planner = SpeedCameraPlanner()
  update(planner, frame(wire))
  data = frame(wire, time_s=1, **{action: True})
  planner.observe_car_state(data[0][0], EPOCH + 1)
  output = update(planner, data)
  assert output.state.value == 'cancelled'
  assert output.manual_takeover and output.speed_cap_mps is None
  assert output.reason == reason and not output.restore_allowed
  message = custom.LongitudinalPlanSP.new_message()
  message.speedCameraControl.state = output.state.value
  message.speedCameraControl.speedCap = output.speed_cap_mps or 0.0
  message.speedCameraControl.reason = output.reason
  message.speedCameraControl.manualTakeover = output.manual_takeover
  result = wire(custom.LongitudinalPlanSP, message).speedCameraControl
  assert (result.state, result.reason, result.manualTakeover, result.speedCap) == (
    'cancelled', reason, True, 0.0)


def test_decoded_pass_requires_two_fixes_then_new_map_generation(wire):
  planner = SpeedCameraPlanner()
  update(planner, frame(wire))
  first = update(planner, frame(wire, north_m=20, time_s=1, lmd_generation=0))
  assert not first.restore_allowed
  second = update(planner, frame(wire, north_m=25, time_s=2, lmd_generation=0))
  assert second.reason == 'awaiting_map_update'
  assert second.speed_cap_mps is not None and not second.restore_allowed
  output = update(planner, frame(wire, north_m=30, time_s=3,
                                camera_valid=False, lmd_generation=3))
  assert output.state.value == 'passed'
  assert output.reason == 'geometric_pass'
  assert output.restore_allowed and output.speed_cap_mps is None


@pytest.mark.parametrize('raw_kph,eligible', [(29, False), (30, True), (60, True)])
def test_decoded_independent_control_candidate_raw_minimum_and_offset(wire, raw_kph, eligible):
  values = dict(speedCameraControlVersion=1, speedCameraControlValid=True,
                speedCameraControlAutoControlAllowed=True, speedCameraControlDistance=500,
                speedCameraControlType='speedcamonline-cg77:18059',
                speedCameraControlLatitude=latitude(400), speedCameraControlLongitude=LON,
                speedCameraControlSpeedLimit=raw_kph / 3.6)
  data = frame(wire, kind='speedcamonline-cg77:18952', control_values=values, camera_speed_offset_kph=19)
  lmd = data[0][3]
  assert lmd.speedCameraType == 'speedcamonline-cg77:18952'
  assert lmd.speedCameraLatitude == LAT and lmd.speedCameraSpeedLimit == pytest.approx(LIMIT)
  assert lmd.speedCameraControlVersion == 1 and lmd.speedCameraControlValid
  assert lmd.speedCameraControlDistance == 500
  assert lmd.speedCameraControlSpeedLimit == pytest.approx(raw_kph / 3.6)
  output = update(SpeedCameraPlanner(), data)
  assert (output.speed_cap_mps is not None) is eligible
  if eligible:
    assert output.camera_id == f'{latitude(400):.7f},{LON:.7f}:speedcamonline-cg77:18059'


@pytest.mark.parametrize('version', [1, 2])
def test_decoded_new_contract_without_candidate_cannot_fall_back_to_warning(wire, version):
  data = frame(wire, control_values={'speedCameraControlVersion': version})
  assert data[0][3].speedCameraAheadValid
  assert update(SpeedCameraPlanner(), data).speed_cap_mps is None


def test_decoded_old_contract_version_defaults_to_zero(wire):
  data = frame(wire)
  assert data[0][3].speedCameraControlVersion == 0
  assert not data[0][3].speedCameraControlValid
  assert update(SpeedCameraPlanner(), data).speed_cap_mps is not None
