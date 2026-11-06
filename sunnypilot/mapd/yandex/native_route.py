"""Decode the verified native Response/GeoObject route geometry hierarchy.

This prepares a conditions request, not a camera/control snapshot. Route metadata
stays opaque. No speed limit, event freshness or vehicle passage is inferred.
"""
from __future__ import annotations

from . import native_codec as wire
from .native_geometry import decode_polyline_scaled, join_polylines


def _messages(fields, number):
  result = []
  for n, kind, value in fields:
    if n == number:
      wire._expect(kind, 2)
      result.append(value)
  return result


def _one(fields, number):
  values = _messages(fields, number)
  if len(values) != 1:
    raise wire.WireError('missing or ambiguous native route component')
  return values[0]


def decode_route_geometry(body: bytes) -> dict:
  """Extract one requested route and join every section at exact integer seams.

  Verified path: Response#2 -> GeoObject#6 route -> #6 section -> #5 Geometry
  -> #2 Polyline. Route metadata is extension #91 in GeoObject#1 Metadata.
  Unknown response-level fields fail closed rather than hiding a server error.
  """
  budget = [0]

  def fields(data):
    return list(wire._fields(data, budget))

  response = fields(body)
  if any(number != 2 for number, _, _ in response):
    raise wire.WireError('unsupported native response status or envelope')
  collection = fields(_one(response, 2))
  route_object = fields(_one(collection, 6))
  route_metadata = []
  for metadata in _messages(route_object, 1):
    route_metadata.extend(_messages(fields(metadata), 91))
  if len(route_metadata) != 1 or not route_metadata[0]:
    raise wire.WireError('missing or ambiguous driving route metadata')
  sections = _messages(route_object, 6)
  if not sections:
    raise wire.WireError('native route has no section geometry')
  polylines, ranges, section_metadata = [], [], []
  start = 0
  for section in sections:
    section_fields = fields(section)
    metadata_values = []
    for metadata in _messages(section_fields, 1):
      metadata_values.extend(_messages(fields(metadata), 92))
    if len(metadata_values) > 1:
      raise wire.WireError('ambiguous driving section metadata')
    section_metadata.append(metadata_values[0] if metadata_values else None)
    geometry = fields(_one(section_fields, 5))
    if any(number != 2 for number, _, _ in geometry):
      raise wire.WireError('unsupported native section geometry')
    polyline = _one(geometry, 2)
    count = len(decode_polyline_scaled(polyline))
    ranges.append((start, start + count - 1))
    start += count - 1
    polylines.append(polyline)
  joined = join_polylines(polylines)
  points = decode_polyline_scaled(joined)
  if len(points) < 2:
    raise wire.WireError('native route has no usable length')
  return {'source': 'yandex_native_wire', 'route_metadata_wire': route_metadata[0],
          'polyline_wire': joined, 'geometry_lon_lat_scaled': points, 'section_ranges': tuple(ranges),
          'section_segment_counts': tuple(end - begin for begin, end in ranges),
          'section_metadata_wire': tuple(section_metadata),
          'auto_control_eligible': False}
