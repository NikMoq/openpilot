"""Verified native wire shapes only; no HTTP contract or speed-unit semantics.

Evidence: docs/yandex_native_wire_contract.json. Polyline remains opaque bytes.
"""
from __future__ import annotations

import math
import struct

MAX_BYTES = 1024 * 1024
MAX_FIELDS = 4096
MAX_VALUES = 4096


class WireError(ValueError):
  pass


def _varint(value):
  result = bytearray()
  while value > 127:
    result.append((value & 127) | 128)
    value >>= 7
  result.append(value)
  return bytes(result)


def _read_varint(body, pos):
  start = pos
  value = 0
  for shift in range(0, 70, 7):
    if pos >= len(body):
      raise WireError('truncated varint')
    octet = body[pos]
    pos += 1
    if shift == 63 and octet > 1:
      raise WireError('varint overflow')
    value |= (octet & 127) << shift
    if octet < 128:
      if body[start:pos] != _varint(value):
        raise WireError('noncanonical varint')
      return value, pos
  raise WireError('varint overflow')


def _fields(body, budget):
  if not isinstance(body, bytes) or len(body) > MAX_BYTES:
    raise WireError('invalid message size/type')
  pos = 0
  while pos < len(body):
    budget[0] += 1
    if budget[0] > MAX_FIELDS:
      raise WireError('field budget exceeded')
    tag, pos = _read_varint(body, pos)
    number, wire = tag >> 3, tag & 7
    if not 1 <= number <= (1 << 29) - 1:
      raise WireError('invalid field number')
    if wire == 0:
      value, pos = _read_varint(body, pos)
    elif wire in (1, 2, 5):
      if wire == 2:
        size, pos = _read_varint(body, pos)
      else:
        size = 8 if wire == 1 else 4
      if size > len(body) - pos:
        raise WireError('truncated field')
      value = body[pos:pos + size]
      pos += size
    else:
      raise WireError('unsupported wire type')
    yield number, wire, value


def _expect(wire, expected):
  if wire != expected:
    raise WireError('wrong known-field wire type')


def _unique(seen, number):
  if number in seen:
    raise WireError('duplicate singular field')
  seen.add(number)


def _representation(body, budget):
  result = {'polyline_wire': None, 'field2_bytes': None, 'unknown_field_numbers': []}
  seen = set()
  for number, wire, value in _fields(body, budget):
    if number in (1, 2):
      _expect(wire, 2)
      _unique(seen, number)
      result['polyline_wire' if number == 1 else 'field2_bytes'] = value
    else:
      result['unknown_field_numbers'].append(number)
  return result


def decode_conditions_request(body):
  result = {'field3_values': [], 'representation': None, 'unknown_field_numbers': []}
  budget, seen = [0], set()
  for number, wire, value in _fields(body, budget):
    if number == 3:
      if wire == 0:
        values = [value]
      elif wire == 2:
        values, pos = [], 0
        while pos < len(value):
          item, pos = _read_varint(value, pos)
          values.append(item)
          if len(values) > MAX_VALUES:
            raise WireError('value budget exceeded')
      else:
        raise WireError('wrong field3 wire type')
      if any(item > 0xffffffff for item in values):
        raise WireError('field3 exceeds 32 bits')
      result['field3_values'].extend(values)
      if len(result['field3_values']) > MAX_VALUES:
        raise WireError('value budget exceeded')
    elif number == 4:
      _expect(wire, 2)
      _unique(seen, number)
      result['representation'] = _representation(value, budget)
    else:
      result['unknown_field_numbers'].append(number)
  return result


def _blob(number, value):
  if not isinstance(value, bytes) or len(value) > MAX_BYTES:
    raise WireError('invalid byte field')
  return _varint(number * 8 + 2) + _varint(len(value)) + value


def encode_conditions_request(field3_values=(), *, polyline_wire=None, field2_bytes=None, representation_present=False):
  """Encode raw values; empty optional representation requires explicit flag."""
  result = bytearray()
  count = 0
  for value in field3_values:
    count += 1
    if count > MAX_VALUES or type(value) is not int or not 0 <= value <= 0xffffffff:
      raise WireError('invalid field3 value/count')
    result.extend(b'\x18' + _varint(value))
  if representation_present or polyline_wire is not None or field2_bytes is not None:
    representation = bytearray()
    if polyline_wire is not None:
      representation.extend(_blob(1, polyline_wire))
    if field2_bytes is not None:
      representation.extend(_blob(2, field2_bytes))
    result.extend(_blob(4, bytes(representation)))
  body = bytes(result)
  decode_conditions_request(body)  # Apply the same aggregate wire budget.
  return body


def _speed_limit(body, budget):
  result = {'field1_float32': None, 'field2_int32': None, 'unknown_field_numbers': []}
  seen = set()
  for number, wire, value in _fields(body, budget):
    if number == 1:
      _expect(wire, 5)
      _unique(seen, number)
      raw = struct.unpack('<f', value)[0]
      if not math.isfinite(raw):
        raise WireError('nonfinite float')
      result['field1_float32'] = raw
    elif number == 2:
      _expect(wire, 0)
      _unique(seen, number)
      signed = value - (1 << 64) if value >= 1 << 63 else value
      if not -(1 << 31) <= signed < 1 << 31:
        raise WireError('invalid sign-extended int32')
      result['field2_int32'] = signed
    else:
      result['unknown_field_numbers'].append(number)
  return result


def decode_speed_limit(body):
  return _speed_limit(body, [0])


def decode_speed_limits(body):
  result = {'field1_limits': [], 'unknown_field_numbers': []}
  budget = [0]
  for number, wire, value in _fields(body, budget):
    if number == 1:
      _expect(wire, 2)
      result['field1_limits'].append(_speed_limit(value, budget))
    else:
      result['unknown_field_numbers'].append(number)
  return result
