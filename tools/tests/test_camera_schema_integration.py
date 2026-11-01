"""Real pycapnp wire/planner smoke; run separately from namespace-based tests.

Linux Python 3.12, pycapnp, pytest and numpy; repository packages on PYTHONPATH.
The isolated checkout must resolve cereal/car.capnp to opendbc's real schema.
python -B -m pytest -c tools/tests/camera_replay_pytest.ini -p no:cacheprovider \
  tools/tests/test_camera_schema_integration.py -q

No messaging/Params substitutes: this verifies schemas and CameraSpeedAdapter,
not LongitudinalPlannerSP construction or its production publish path.
"""

from contextlib import ExitStack
from types import SimpleNamespace as NS

import pytest

from cereal import custom, log
from openpilot.sunnypilot.selfdrive.controls.lib.camera_speed_adapter import CameraSpeedAdapter


LAT, LON = 55.0, 37.0
USER_SPEED = 30.0
LIMIT = 60 / 3.6


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


@pytest.mark.parametrize('allowed', [None, False, True])
def test_auto_control_flag_wire_default_and_explicit_values(wire, allowed):
  message = custom.LiveMapDataSP.new_message()
  if allowed is not None:
    message.speedCameraAutoControlAllowed = allowed
  result = wire(custom.LiveMapDataSP, message)
  assert result.speedCameraAutoControlAllowed is (allowed is True)


def camera_source_wire(wire, *, event='wire-camera', state='ACTIVE', passed=False):
  message = custom.LiveMapDataSP.new_message(
    locationValid=True, speedCameraControlVersion=1, speedCameraControlEventId=event,
    speedCameraControlState=state, speedCameraControlValid=not passed,
    speedCameraControlAutoControlAllowed=True, speedCameraControlDistance=100,
    speedCameraControlSpeedLimit=LIMIT, speedCameraControlPassProven=passed)
  return wire(custom.LiveMapDataSP, message)


def adapter_update(adapter, lmd, *, engaged=True):
  return adapter.update(lmd, enabled=True, reliable=True, engaged=engaged, reaction_distance=300)


@pytest.mark.parametrize('action', ['gas', 'button'])
def test_decoded_physical_action_does_not_create_persistent_camera_override(wire, action):
  adapter = CameraSpeedAdapter()
  source = camera_source_wire(wire)
  initial = adapter_update(adapter, source)
  cs = NS(buttonEvents=[NS(type='accelCruise', pressed=True)] if action == 'button' else [],
          gasPressed=action == 'gas', brakePressed=False,
          cruiseState=NS(enabled=True, speedCluster=USER_SPEED))
  adapter.observe_car_state(cs)
  output = adapter_update(adapter, source)
  assert output.state == 'ACTIVE' and output.automatic and not output.manual
  assert output.event_id == initial.event_id and output.raw_limit == pytest.approx(LIMIT)
  message = custom.LongitudinalPlanSP.new_message()
  message.speedCameraControl.state = output.state.lower()
  message.speedCameraControl.speedCap = output.raw_limit
  message.speedCameraControl.reason = output.reason
  message.speedCameraControl.manualTakeover = output.manual
  result = wire(custom.LongitudinalPlanSP, message).speedCameraControl
  assert (result.state, result.manualTakeover) == ('active', False)
  assert result.speedCap == pytest.approx(LIMIT)
  cs.buttonEvents, cs.gasPressed = [], False
  adapter.observe_car_state(cs)
  assert adapter_update(adapter, source).automatic


def test_decoded_explicit_cancel_cannot_latch_manual_state_across_passage(wire):
  adapter = CameraSpeedAdapter()
  source = camera_source_wire(wire)
  initial = adapter_update(adapter, source)
  adapter.observe_car_state(NS(buttonEvents=[NS(type='cancel', pressed=True)],
                               cruiseState=NS(enabled=False, speedCluster=USER_SPEED)))
  paused = adapter_update(adapter, source, engaged=False)
  assert paused.event_id == initial.event_id and paused.state == 'ACTIVE' and not paused.manual
  passed = adapter_update(adapter, camera_source_wire(wire, state='PASSED', passed=True), engaged=False)
  assert passed.state == 'PASSED' and not passed.manual and not passed.automatic
  following = adapter_update(adapter, camera_source_wire(wire, event='next-wire-camera'))
  assert following.state == 'ACTIVE' and following.automatic and not following.manual
  assert following.event_id != initial.event_id
