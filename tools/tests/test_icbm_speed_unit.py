import pytest

from cereal import custom
from openpilot.selfdrive.car.helpers import convert_carControlSP
from opendbc.car.structs import IntelligentCruiseButtonManagement


@pytest.mark.parametrize(('is_metric', 'expected'), [(True, 'kph'), (False, 'mph')])
def test_icbm_speed_unit_capnp_roundtrip(is_metric, expected):
  unit = IntelligentCruiseButtonManagement.SpeedUnit.from_metric(is_metric)
  message = custom.IntelligentCruiseButtonManagement.new_message(
    state='increasing', sendButton='increase', vTarget=27.5, speedUnit=unit.value)

  with custom.IntelligentCruiseButtonManagement.from_bytes(message.to_bytes()) as decoded:
    assert decoded.speedUnit == expected
    assert decoded.state == 'increasing'
    assert decoded.sendButton == 'increase'
    assert decoded.vTarget == pytest.approx(27.5)


def test_icbm_old_wire_message_defaults_speed_unit_to_unknown():
  # Legacy writers omit ordinal @3. Serializing without the appended field
  # exercises the same wire representation as an older producer.
  legacy = custom.IntelligentCruiseButtonManagement.new_message(
    state='decreasing', sendButton='decrease', vTarget=19.25)
  data = IntelligentCruiseButtonManagement(vTarget=19.25)

  with custom.IntelligentCruiseButtonManagement.from_bytes(legacy.to_bytes()) as decoded:
    assert decoded.speedUnit == 'unknown'
    assert decoded.vTarget == pytest.approx(19.25)
  assert data.speedUnit is IntelligentCruiseButtonManagement.SpeedUnit.unknown


@pytest.mark.parametrize(('is_metric', 'expected'), [(True, 'kph'), (False, 'mph')])
def test_icbm_speed_unit_nested_car_control_sp_conversion(is_metric, expected):
  unit = IntelligentCruiseButtonManagement.SpeedUnit.from_metric(is_metric)
  message = custom.CarControlSP.new_message()
  nested = message.intelligentCruiseButtonManagement
  nested.state = 'holding'
  nested.sendButton = 'none'
  nested.vTarget = 31.75
  nested.speedUnit = unit.value

  with custom.CarControlSP.from_bytes(message.to_bytes()) as decoded:
    converted = convert_carControlSP(decoded)

  assert converted.intelligentCruiseButtonManagement.speedUnit == expected
  assert converted.intelligentCruiseButtonManagement.vTarget == pytest.approx(31.75)


def test_legacy_nested_car_control_sp_converts_missing_unit_to_unknown():
  message = custom.CarControlSP.new_message()
  nested = message.intelligentCruiseButtonManagement
  nested.state = 'holding'
  nested.sendButton = 'none'
  nested.vTarget = 31.75
  # Deliberately leave appended ordinal @3 unset, as an older writer would.

  with custom.CarControlSP.from_bytes(message.to_bytes()) as decoded:
    converted = convert_carControlSP(decoded)

  assert converted.intelligentCruiseButtonManagement.speedUnit == 'unknown'
  assert converted.intelligentCruiseButtonManagement.vTarget == pytest.approx(31.75)
