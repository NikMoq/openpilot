"""Bounded raw Section speed-limit RLE expansion; units remain unverified."""
from __future__ import annotations

from . import native_codec as wire

MAX_SEGMENTS = 100_000
MAX_ROUTE_BYTES = 4 * 1024 * 1024


def _expected(value):
  if type(value) is not int or not 0 <= value <= MAX_SEGMENTS:
    raise wire.WireError('invalid expected segment count')
  return value


def _section(body, expected, budget):
  expected = _expected(expected)
  if body is None:
    return (None,) * expected
  limits = None
  for number, kind, value in wire._fields(body, budget):
    if number == 5:
      wire._expect(kind, 2)
      if limits is not None:
        raise wire.WireError('duplicate Section limits')
      limits = value
  if limits is None:
    return (None,) * expected
  result = []
  for number, kind, value in wire._fields(limits, budget):
    if number != 1:
      continue
    wire._expect(kind, 2)
    record = wire._speed_limit(value, budget)
    count = record['field2_int32']
    if type(count) is not int or count <= 0 or count > expected - len(result):
      raise wire.WireError('invalid limit run count')
    result.extend([record['field1_float32']] * count)
  if len(result) != expected:
    raise wire.WireError('limit runs do not cover section')
  return tuple(result)


def decode_section_limits(section92wire, expected_segments):
  """Return one raw float/None per edge. Missing metadata/field5 is unknown."""
  return _section(section92wire, expected_segments, [0])


def decode_route_limits(route_snapshot):
  """Expand native_route snapshot fields without assigning units or defaults."""
  metadata = route_snapshot['section_metadata_wire']
  counts = route_snapshot['section_segment_counts']
  if not isinstance(metadata, tuple) or not isinstance(counts, tuple) or len(metadata) != len(counts):
    raise wire.WireError('section metadata/count mismatch')
  total = sum(_expected(count) for count in counts)
  if total > MAX_SEGMENTS or len(counts) > wire.MAX_FIELDS:
    raise wire.WireError('route expansion budget exceeded')
  byte_count = 0
  for body in metadata:
    if body is not None:
      if not isinstance(body, bytes):
        raise wire.WireError('invalid section metadata')
      byte_count += len(body)
  if byte_count > MAX_ROUTE_BYTES:
    raise wire.WireError('route byte budget exceeded')
  result, budget = [], [0]
  for body, count in zip(metadata, counts, strict=True):
    result.extend(_section(body, count, budget))
  return tuple(result)
