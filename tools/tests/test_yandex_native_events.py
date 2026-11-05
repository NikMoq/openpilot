"""Synthetic native protobuf vectors; no network or SDK semantic assumptions."""
import importlib
from pathlib import Path
import struct
import sys
import types

import pytest

PACKAGE = '_yandex_native_events_test'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[2] / 'sunnypilot/mapd/yandex')]
sys.modules[PACKAGE] = package
wire = importlib.import_module(PACKAGE + '.native_codec')
events = importlib.import_module(PACKAGE + '.native_events')


def test_fixed_full_segment_vector():
  body = bytes.fromhex('080712016118001a02010c2a02080132016239000000000000f03f420163420164')
  result = events.decode_segment_event(body)
  assert result == {'field1_int32': 7, 'field2_bytes': b'a', 'field3_enums': [0, 1, 12],
                    'field5_point_wire': b'\x08\x01', 'field6_bytes': b'b', 'field7_float64': 1.0,
                    'field8_bytes': [b'c', b'd'], 'unknown_field_numbers': []}


def test_conditions_nested_fixed_vector():
  result = events.decode_conditions_events(bytes.fromhex('22060a0208070a00'))
  assert len(result['field4_route_events']['field1_events']) == 2
  assert result['field4_route_events']['field1_events'][0]['field1_int32'] == 7
  assert result['field4_route_events']['field1_events'][1]['field1_int32'] is None


def test_signed_int32_vector():
  assert events.decode_segment_event(bytes.fromhex('08ffffffffffffffffff01'))['field1_int32'] == -1


def test_unknown_and_opaque_fields():
  result = events.decode_conditions_events(bytes.fromhex('0a01782a01792a017a3801'))
  assert result['unknown_field_numbers'] == [7]
  assert result['opaque_fields'] == [{'number': 1, 'wire_type': 2, 'value': b'x'},
                                     {'number': 5, 'wire_type': 2, 'value': b'y'},
                                     {'number': 5, 'wire_type': 2, 'value': b'z'}]
  assert events.decode_route_events(bytes.fromhex('1001'))['unknown_field_numbers'] == [2]
  assert events.decode_segment_event(bytes.fromhex('2001'))['unknown_field_numbers'] == [4]


@pytest.mark.parametrize('body', [b'\x00', b'\x08\x80', b'\x08\x81\x00', b'\x08' + b'\xff' * 10,
                                 b'\x18\x0d', b'\x1a\x01\x0d', b'\x1d1234', b'\x2a\x02x',
                                 b'\x08\x01\x08\x02', bytes.fromhex('08ffffffff0f')])
def test_bad_segment(body):
  with pytest.raises(wire.WireError):
    events.decode_segment_event(body)


@pytest.mark.parametrize('raw', [float('nan'), float('inf'), -float('inf')])
def test_nonfinite_double(raw):
  with pytest.raises(wire.WireError):
    events.decode_segment_event(b'\x39' + struct.pack('<d', raw))


@pytest.mark.parametrize('body', [b'\x20\x01', b'\x22\x00\x22\x00', b'\x08\x01'])
def test_bad_conditions(body):
  with pytest.raises(wire.WireError):
    events.decode_conditions_events(body)


def test_aggregate_fields_budget(monkeypatch):
  monkeypatch.setattr(wire, 'MAX_FIELDS', 2)
  with pytest.raises(wire.WireError):
    events.decode_conditions_events(bytes.fromhex('22040a020807'))


def test_value_budget(monkeypatch):
  monkeypatch.setattr(wire, 'MAX_VALUES', 1)
  for body in (bytes.fromhex('18001801'), bytes.fromhex('1a020001'), bytes.fromhex('42004200')):
    with pytest.raises(wire.WireError):
      events.decode_segment_event(body)
  with pytest.raises(wire.WireError):
    events.decode_route_events(bytes.fromhex('0a000a00'))


def test_size_budget(monkeypatch):
  monkeypatch.setattr(wire, 'MAX_BYTES', 1)
  with pytest.raises(wire.WireError):
    events.decode_route_events(b'\x0a\x00')
