"""Normalize verified native route evidence; this module never enables control."""
import math
from bisect import bisect_left, bisect_right
import struct
import time

from . import native_codec as wire
from .native_limits import decode_route_limits

SDK_VERSION = '4.45.0-full'
LIMIT_CONFIDENCE = 'native_rle_verified_against_sdk_edges'
TAGS = frozenset(('other', 'feedback', 'chat', 'local_chat', 'drawbridge', 'closed', 'reconstruction', 'accident',
                  'danger', 'school', 'overtaking_danger', 'pedestrian_danger', 'cross_road_danger', 'police',
                  'lane_control', 'road_marking_control', 'cross_road_control', 'no_stopping_control', 'mobile_control',
                  'speed_control', 'traffic_control', 'police_patrol'))


def _number(value):
  return type(value) in (int, float) and math.isfinite(value)


def _text(value, limit):
  if not isinstance(value, bytes) or not 0 < len(value) <= limit:
    raise ValueError('invalid native text size')
  result = value.decode('utf-8', errors='strict')
  if any(ord(character) < 32 or ord(character) == 127 for character in result):
    raise ValueError('invalid native text characters')
  return result


def _point(body):
  values = {}
  for number, kind, value in wire._fields(body, [0]):
    if number not in (1, 2) or number in values:
      raise ValueError('unsupported event point fields')
    wire._expect(kind, 1)
    values[number] = struct.unpack('<d', value)[0]
  if set(values) != {1, 2}:
    raise ValueError('incomplete event point')
  lon, lat = values[1], values[2]
  if not _number(lon) or not _number(lat) or abs(lon) > 180 or abs(lat) > 90:
    raise ValueError('invalid event point coordinates')
  return lon, lat


def _distance(a, b):
  lat1, lat2 = math.radians(a[1]), math.radians(b[1])
  dlat, dlon = lat2 - lat1, math.radians(b[0] - a[0])
  h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
  return 6_371_000 * 2 * math.asin(math.sqrt(min(1, h)))


def _bearing(a, b):
  lat1, lat2 = math.radians(a[1]), math.radians(b[1])
  delta = math.radians(b[0] - a[0])
  return math.degrees(math.atan2(math.sin(delta) * math.cos(lat2),
                                math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(delta))) % 360


def approach_context(points, index, fraction, observed, expires, *, distances=None):
  """Keep a bounded witnessed road path, never a straight extrapolation.

  Retain the exact event edge even for unusually dense route vertices. A short
  retained path does not authorize matching beyond its actual endpoints.
  """
  if distances is None:
    distances = [0.]
    for a, b in zip(points, points[1:], strict=False):
      distances.append(distances[-1] + _distance(tuple(v / 1e6 for v in a), tuple(v / 1e6 for v in b)))
  position = distances[index] + fraction * (distances[index + 1] - distances[index])
  begin = max(0, bisect_left(distances, position - 5000) - 1)
  end = min(len(points), bisect_right(distances, position + 300) + 1)
  if end - begin > 512:
    begin = max(begin, index - 383)
    end = min(end, begin + 512)
  geometry = tuple(tuple(v / 1e6 for v in p) for p in points[begin:end])
  return {'approach_geometry': geometry, 'approach_source': 'yandex_native_route',
          'approach_observed_at': observed, 'approach_expires_at': expires}


def normalize_native_snapshot(result, *, now=None):
  """Return fresh source records, preserving acquisition time and raw provenance.

  ``now`` and ``result.observed_at`` are UNIX seconds. TTLs are local policy
  caps, not server promises. Mobile enforcement has no facing filter and the
  supplied limit belongs only to this directed route edge.
  """
  now = time.time() if now is None else now  # noqa: TID251 -- UNIX freshness comparison
  observed = result.observed_at
  if result.complete is not True or not _number(observed) or observed <= 0 or not _number(now) or observed > now + 1:
    raise ValueError('incomplete or invalid native acquisition time')
  route, conditions = result.route, result.conditions
  points = route['geometry_lon_lat_scaled']
  if not isinstance(points, tuple) or not 2 <= len(points) <= 100_000:
    raise ValueError('invalid route geometry size')
  for point in points:
    if len(point) != 2 or any(type(value) is not int or abs(value) > limit for value, limit in zip(point, (180_000_000, 90_000_000), strict=True)):
      raise ValueError('invalid scaled route point')
  limits = decode_route_limits(route)
  if len(limits) != len(points) - 1:
    raise ValueError('route limit geometry mismatch')
  distances = [0.]
  for a, b in zip(points, points[1:], strict=False):
    distances.append(distances[-1] + _distance(tuple(v / 1e6 for v in a), tuple(v / 1e6 for v in b)))
  events = conditions['field4_route_events']
  if conditions['unknown_field_numbers'] or events is None or events['unknown_field_numbers']:
    raise ValueError('unsupported native events envelope')
  if len(events['field1_events']) > wire.MAX_VALUES:
    raise ValueError('native event budget exceeded')
  records = []
  remaining_context_points = 32_768
  seen = set()
  for event in events['field1_events']:
    index, fraction = event['field1_int32'], event['field7_float64']
    if event['unknown_field_numbers'] or type(index) is not int or not 0 <= index < len(limits) or not _number(fraction) or not 0 <= fraction <= 1:
      raise ValueError('invalid event route position')
    event_id = _text(event['field2_bytes'], 256)
    if event_id in seen:
      raise ValueError('duplicate native event identity')
    seen.add(event_id)
    if len(event['field8_bytes']) > wire.MAX_VALUES:
      raise ValueError('native tag budget exceeded')
    raw_tags = tuple(_text(value, 128) for value in event['field8_bytes'])
    known_tags = TAGS.intersection(raw_tags)
    if not known_tags:
      continue
    mobile = bool(known_tags.intersection(('mobile_control', 'police_patrol')))
    kind = 'mobile' if mobile else 'fixed' if 'speed_control' in known_tags else 'warning'
    ttl = 180 if mobile else 300
    expires = observed + ttl
    if now >= expires:
      continue
    a, b = tuple(value / 1_000_000 for value in points[index]), tuple(value / 1_000_000 for value in points[index + 1])
    if a == b or abs(b[0] - a[0]) > 180:
      raise ValueError('unsupported degenerate or wrapping event edge')
    projected = (a[0] + (b[0] - a[0]) * fraction, a[1] + (b[1] - a[1]) * fraction)
    physical = _point(event['field5_point_wire']) if event['field5_point_wire'] is not None else projected
    separation = _distance(physical, projected)
    if separation > 30:
      raise ValueError('event point does not match route position')
    raw = limits[index]
    if raw is not None and (not _number(raw) or raw < 0):
      raise ValueError('invalid raw road limit')
    kph = None
    if raw is not None and raw > 0:
      canonical = raw * 3.6
      rounded = round(canonical)
      if rounded > 0 and abs(canonical - rounded) <= 1e-4:
        kph = rounded
    context = {}
    if kind in ('fixed', 'mobile') and kph is not None:
      candidate = approach_context(points, index, fraction, observed, expires, distances=distances)
      if len(candidate['approach_geometry']) <= remaining_context_points:
        context = candidate
        remaining_context_points -= len(candidate['approach_geometry'])
    records.append({'source': 'yandex', 'source_kind': kind, 'source_id': 'yandex:native:' + event_id,
                    'source_sdk': SDK_VERSION, 'raw_event_id': event_id, 'raw_tags': raw_tags,
                    'tags': tuple(sorted(known_tags)), 'in_face': 'in_face' in raw_tags, 'in_back': 'in_back' in raw_tags,
                    'observed_at': observed, 'expires_at': expires, 'observed_at_ms': int(observed * 1000),
                    'expires_at_ms': int(expires * 1000), 'ttl_basis': 'local_policy_cap',
                    'latitude': physical[1], 'longitude': physical[0], 'position_index': index, 'position_fraction': fraction,
                    'location_source': 'reported_point' if event['field5_point_wire'] is not None else 'route_interpolation',
                    'road_geometry': (a, b), 'road_heading_deg': _bearing(a, b),
                    'route_position_longitude': projected[0], 'route_position_latitude': projected[1],
                    'report_route_distance_m': separation, 'road_limit_raw_mps': raw, 'road_limit_kph': kph,
                    'road_limit_raw_unit': 'm/s',
                    'road_limit_unit': 'km/h' if kph is not None else None,
                    'speed_limit_confidence': LIMIT_CONFIDENCE if kph is not None else None,
                    'road_limit_direction_basis': 'acquired_route_forward_edge',
                    'direction': None, 'auto_control_eligible': False,
                    **context})
  return records
