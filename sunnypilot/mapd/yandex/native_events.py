"""Raw generated Conditions/RouteEvents shapes; no HTTP root or event semantics."""
from __future__ import annotations

import math
import struct

from . import native_codec as wire


def _append(values, value):
  if len(values) >= wire.MAX_VALUES:
    raise wire.WireError('value budget exceeded')
  values.append(value)


def _segment(body, budget):
  result = {'field1_int32': None, 'field2_bytes': None, 'field3_enums': [], 'field5_point_wire': None,
            'field6_bytes': None, 'field7_float64': None, 'field8_bytes': [], 'unknown_field_numbers': []}
  seen = set()
  for number, kind, value in wire._fields(body, budget):
    if number == 3:
      if kind == 0:
        values = [value]
      elif kind == 2:
        values, pos = [], 0
        while pos < len(value):
          item, pos = wire._read_varint(value, pos)
          _append(values, item)
      else:
        raise wire.WireError('wrong enum wire type')
      for item in values:
        if not 0 <= item <= 12:
          raise wire.WireError('unsupported raw enum')
        _append(result['field3_enums'], item)
    elif number == 8:
      wire._expect(kind, 2)
      _append(result['field8_bytes'], value)
    elif number in (1, 2, 5, 6, 7):
      wire._unique(seen, number)
      if number == 1:
        wire._expect(kind, 0)
        signed = value - (1 << 64) if value >= 1 << 63 else value
        if not -(1 << 31) <= signed < 1 << 31:
          raise wire.WireError('invalid signed int32')
        result['field1_int32'] = signed
      elif number == 7:
        wire._expect(kind, 1)
        raw = struct.unpack('<d', value)[0]
        if not math.isfinite(raw):
          raise wire.WireError('nonfinite double')
        result['field7_float64'] = raw
      else:
        wire._expect(kind, 2)
        result[{2: 'field2_bytes', 5: 'field5_point_wire', 6: 'field6_bytes'}[number]] = value
    else:
      result['unknown_field_numbers'].append(number)
  return result


def decode_segment_event(body):
  return _segment(body, [0])


def _events(body, budget):
  result = {'field1_events': [], 'unknown_field_numbers': []}
  for number, kind, value in wire._fields(body, budget):
    if number == 1:
      wire._expect(kind, 2)
      _append(result['field1_events'], _segment(value, budget))
    else:
      result['unknown_field_numbers'].append(number)
  return result


def decode_route_events(body):
  return _events(body, [0])


def decode_conditions_events(body):
  """Only Conditions field4 is decoded; other fields stay opaque, with numbers."""
  result = {'field4_route_events': None, 'opaque_fields': [], 'unknown_field_numbers': []}
  seen, budget = set(), [0]
  for number, kind, value in wire._fields(body, budget):
    if number == 4:
      wire._expect(kind, 2)
      wire._unique(seen, number)
      result['field4_route_events'] = _events(value, budget)
    elif number in (1, 2, 3, 5, 6):
      wire._expect(kind, 2)
      if number != 5:
        wire._unique(seen, number)
      result['opaque_fields'].append({'number': number, 'wire_type': kind, 'value': value})
    else:
      result['unknown_field_numbers'].append(number)
  return result
