"""Real Cap'n Proto wire checks; no messaging transport or vehicle IO."""
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason='run real schema parser in the fresh Linux test container')
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def schema():
  import capnp
  parser = capnp.SchemaParser()
  return parser.load(str(ROOT / 'cereal/custom.capnp'))


def test_selected_event_roundtrip_is_distinct_from_warning(schema):
  msg = schema.LiveMapDataSP.new_message()
  msg.speedCameraControlVersion = 1
  msg.speedCameraControlEventId = 'controlling-road-camera'
  msg.speedCameraEventId = 'nearer-warning-on-other-road'
  msg.speedCameraControlState = 'ACTIVE'
  msg.speedCameraControlReason = 'route_associated'
  msg.speedCameraControlPassProven = False
  msg.speedCameraControlValid = True
  msg.speedCameraControlDistance = 275
  msg.speedCameraControlSpeedLimit = 60 / 3.6
  with schema.LiveMapDataSP.from_bytes(msg.to_bytes()) as decoded:
    assert decoded.speedCameraControlEventId != decoded.speedCameraEventId
    assert decoded.speedCameraControlDistance == pytest.approx(275)
    assert decoded.speedCameraControlSpeedLimit == pytest.approx(60 / 3.6)
    assert not decoded.speedCameraControlPassProven


def test_new_fields_default_to_no_event_or_passage(schema):
  msg = schema.LiveMapDataSP.new_message()
  msg.speedCameraControlVersion = 1
  msg.speedCameraControlValid = True  # An older producer can set only old fields.
  msg.speedCameraControlSpeedLimit = 60 / 3.6
  with schema.LiveMapDataSP.from_bytes(msg.to_bytes()) as decoded:
    assert decoded.speedCameraControlEventId == ''
    assert decoded.speedCameraControlState == ''
    assert not decoded.speedCameraControlPassProven


@pytest.mark.parametrize('threshold', range(50, 1001, 50))
def test_plan_keeps_native_offset_and_countdown_separate(schema, threshold):
  msg = schema.LongitudinalPlanSP.new_message()
  resolver = msg.speedLimit.resolver
  resolver.speedLimit = 60 / 3.6
  resolver.speedLimitOffset = 15 / 3.6
  resolver.speedLimitFinal = 75 / 3.6
  camera = msg.speedCameraControl
  camera.eventId = 'same-event-as-native-sign'
  camera.distance = threshold
  camera.reactionDistance = threshold
  camera.countdownValid = True
  camera.driverSetSpeed = 100 / 3.6
  with schema.LongitudinalPlanSP.from_bytes(msg.to_bytes()) as decoded:
    assert decoded.speedLimit.resolver.speedLimitFinal == pytest.approx(75 / 3.6)
    assert decoded.speedCameraControl.driverSetSpeed == pytest.approx(100 / 3.6)
    assert decoded.speedCameraControl.distance == threshold
    assert decoded.speedCameraControl.reactionDistance == threshold


def test_existing_field_ordinals_are_preserved(schema):
  original = {'enabled': 0, 'active': 1, 'speedCap': 2, 'state': 3, 'reason': 4, 'manualTakeover': 5}
  fields = schema.LongitudinalPlanSP.SpeedCameraControl.schema.fields
  assert {name: fields[name].proto.ordinal.explicit for name in original} == original
