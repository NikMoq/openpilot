"""Bounded POINT-only Yandex VECTOR3 road-event decoding, independently implemented.

Schema/geometry facts: official public content_provider.worker, SHA256
09e42fbd9b91ff474d7b1099abf862032a684f5dae0896b62aaed2aef0e0c154:
https://maps.yastatic.net/s3/front-maps-static/maps-front-maps/build/client/worker/content_provider.worker/089ee1eb891e663c037f.js
vmap3.Tile/Feature/Attributes/AttributeValue definitions; $2 delta geometry,
HI sequential geometry allocation; Ve tile-center converter; w2 POINT extraction.
The public app's asset-5 projection explicitly uses EPSG:3395, a=6378137,
e=.0818191908426. See fixtures/provenance.json for URLs and response hashes.

This decoder supplies raw observations only. It establishes neither camera
inventory completeness, POLICE/mobile semantics, posted limits, speed units,
event lifetime nor suitability for vehicle control. Unknown geometry fails
closed; skipping a geometry without advancing offsets would corrupt later points.
No upstream implementation code is copied.
"""
from __future__ import annotations

import math
import struct

MAX_BYTES = 2 * 1024 * 1024
MAX_FIELDS = 100_000
MAX_FEATURES = 4096
MAX_GEOMETRIES = 8192
MAX_COORDINATES = 65536
MAX_ATTRIBUTES = 256
MAX_LAYERS = 64
MAX_STRING_BYTES = 4096
PROPERTY_KEYS = frozenset(('category', 'description', 'eventTags', 'eventType', 'future',
                           'icons', 'moderated', 'road_events_id', 'significance', 'text'))


def _varint(body, offset):
  value = 0
  start = offset
  for shift in range(0, 70, 7):
    if offset >= len(body):
      raise ValueError('Truncated Yandex varint')
    byte = body[offset]
    offset += 1
    if shift == 63 and byte > 1:
      raise ValueError('Yandex varint exceeds uint64')
    value |= (byte & 127) << shift
    if not byte & 128:
      if offset - start > 1 and byte == 0:
        raise ValueError('Noncanonical Yandex varint')
      return value, offset
  raise ValueError('Invalid Yandex varint')


class _Reader:
  def __init__(self):
    self.remaining = MAX_FIELDS

  def fields(self, body):
    result, offset = {}, 0
    while offset < len(body):
      self.remaining -= 1
      if self.remaining < 0:
        raise ValueError('Yandex field budget exceeded')
      tag, offset = _varint(body, offset)
      field, wire = tag >> 3, tag & 7
      if not 0 < field < 2**29:
        raise ValueError('Invalid Yandex field number')
      if wire == 0:
        value, offset = _varint(body, offset)
      elif wire in (1, 2, 5):
        if wire == 2:
          length, offset = _varint(body, offset)
        else:
          length = 8 if wire == 1 else 4
        if length > len(body) - offset:
          raise ValueError('Truncated Yandex field')
        value = body[offset:offset + length]
        offset += length
      else:
        raise ValueError('Unsupported Yandex wire type')
      result.setdefault(field, []).append((wire, value))
    return result


def _known(fields, allowed):
  if fields.keys() - allowed:
    raise ValueError('Unsupported Yandex schema field')


def _one(fields, field, wire, default=None):
  values = fields.get(field, ())
  if not values:
    return default
  if len(values) != 1 or values[0][0] != wire:
    raise ValueError('Duplicate or mistyped Yandex singular field')
  return values[0][1]


def _blobs(fields, field, maximum):
  values = fields.get(field, ())
  if len(values) > maximum or any(wire != 2 for wire, _ in values):
    raise ValueError('Invalid Yandex repeated message')
  return [value for _, value in values]


def _numbers(fields, field, maximum, signed=False):
  result = []
  for wire, value in fields.get(field, ()):
    if wire == 0:
      values = [value]
    elif wire == 2:
      values, offset = [], 0
      while offset < len(value):
        number, offset = _varint(value, offset)
        values.append(number)
        if len(values) + len(result) > maximum:
          raise ValueError('Yandex numeric array budget exceeded')
    else:
      raise ValueError('Invalid Yandex numeric array')
    for number in values:
      if number >= 2**32:
        raise ValueError('Yandex integer exceeds uint32')
      result.append((number >> 1) ^ -(number & 1) if signed else number)
    if len(result) > maximum:
      raise ValueError('Yandex numeric array budget exceeded')
  return result


def _text(body):
  if not isinstance(body, bytes) or len(body) > MAX_STRING_BYTES:
    raise ValueError('Invalid Yandex string size')
  try:
    return body.decode('utf-8', errors='strict')
  except UnicodeDecodeError:
    raise ValueError('Invalid Yandex UTF-8') from None


def _value(body, reader):
  fields = reader.fields(body)
  _known(fields, set(range(1, 9)))
  if len(fields) != 1:
    raise ValueError('Yandex attribute must have one value')
  field = next(iter(fields))
  wire = {1: 2, 2: 0, 3: 0, 4: 0, 5: 5, 6: 0, 7: 2, 8: 2}[field]
  value = _one(fields, field, wire)
  if field == 1:
    return _text(value)
  if field in (2, 3):
    if value >= 2**32:
      raise ValueError('Yandex attribute integer exceeds uint32')
    return (value >> 1) ^ -(value & 1) if field == 2 else value
  if field == 4:
    return value
  if field == 5:
    result, = struct.unpack('<f', value)
    if not math.isfinite(result):
      raise ValueError('Nonfinite Yandex attribute')
    return result
  if field == 6:
    if value not in (0, 1):
      raise ValueError('Invalid Yandex boolean')
    return bool(value)
  nested = reader.fields(value)
  _known(nested, {1})
  if field == 8:
    return [_text(item) for item in _blobs(nested, 1, MAX_ATTRIBUTES)]
  result = []
  for kind, item in nested.get(1, ()):
    if kind not in (2, 5) or len(item) % 4:
      raise ValueError('Invalid Yandex float array')
    result.extend(number for number, in struct.iter_unpack('<f', item))
    if len(result) > MAX_ATTRIBUTES or not all(math.isfinite(number) for number in result):
      raise ValueError('Invalid Yandex float array bounds')
  return result


def point_to_lonlat(local_x, local_y, x, y, z, bitness):
  """Convert source delta-decoded coordinates using the confirmed public projection."""
  if (any(type(v) is not int for v in (local_x, local_y, x, y, z, bitness)) or
      not 0 <= z <= 23 or not 0 <= x < 2**z or not 0 <= y < 2**z or not 2 <= bitness <= 24 or
      not -2**(bitness - 1) <= local_x < 2**(bitness - 1) or
      not -2**(bitness - 1) <= local_y < 2**(bitness - 1)):
    raise ValueError('Invalid Yandex projection inputs')
  size = 2 / 2**z
  ratio = size / (2**bitness - 1)
  world_x = size * (x + .5) - 1 + local_x * ratio
  world_y = 1 - size * (y + .5) - local_y * ratio
  if not -1 <= world_x <= 1 or not -1 <= world_y <= 1:
    raise ValueError('Yandex point outside supported world')
  # Ellipsoidal inverse from the public app's stated EPSG:3395 projection,
  # with its documented eccentricity and eighth-order inverse coefficients.
  e2 = .0818191908426**2
  e4, e6, e8 = e2**2, e2**3, e2**4
  t = math.pi / 2 - 2 * math.atan(math.exp(-math.pi * world_y))
  latitude = t + (e2/2 + 5*e4/24 + e6/12 + 13*e8/360) * math.sin(2*t)
  latitude += (7*e4/48 + 29*e6/240 + 811*e8/11520) * math.sin(4*t)
  latitude += (7*e6/120 + 81*e8/1120) * math.sin(6*t) + 4279*e8/161280 * math.sin(8*t)
  return world_x * 180, math.degrees(latitude)


def parse_tile(body: bytes, x: int, y: int, z: int) -> list[dict]:
  """Return raw POINT observations; unsupported geometry/schema raises ValueError.

  Empty 0808 is valid empty transport data, not proof of camera completeness.
  This function has no network, registration, cache, TTL or control side effects.
  """
  if not isinstance(body, bytes) or not 0 < len(body) <= MAX_BYTES:
    raise ValueError('Invalid Yandex tile size')
  # Validate the requested tile even for an empty response.
  point_to_lonlat(0, 0, x, y, z, 8)
  reader = _Reader()
  tile = reader.fields(body)
  _known(tile, set(range(1, 8)))
  bitness = _one(tile, 1, 0, 8)
  point_to_lonlat(0, 0, x, y, z, bitness)
  geometry = _numbers(tile, 2, MAX_COORDINATES, signed=True)
  sizes = _numbers(tile, 3, MAX_GEOMETRIES)
  if any(size <= 0 for size in sizes) or sum(sizes)*2 != len(geometry):
    raise ValueError('Yandex geometry size mismatch')
  keys = []
  for item in _blobs(tile, 4, MAX_ATTRIBUTES):
    fields = reader.fields(item)
    _known(fields, {1})
    names = [_text(value) for value in _blobs(fields, 1, MAX_ATTRIBUTES)]
    if any(not name or len(name) > 128 for name in names) or len(set(names)) != len(names):
      raise ValueError('Invalid Yandex attribute keys')
    keys.append(names)
  values = [_value(item, reader) for item in _blobs(tile, 5, MAX_FEATURES * 8)]
  if _one(tile, 7, 2, b''):
    raise ValueError('Unsupported Yandex 3D geometry')
  result, geometry_index, coordinate_offset, feature_count = [], 0, 0, 0
  for layer_body in _blobs(tile, 6, MAX_LAYERS):
    layer = reader.fields(layer_body)
    _known(layer, {1, 2})
    layer_name = _text(_one(layer, 1, 2))
    for item in _blobs(layer, 2, MAX_FEATURES):
      feature_count += 1
      if feature_count > MAX_FEATURES:
        raise ValueError('Yandex feature budget exceeded')
      feature = reader.fields(item)
      _known(feature, set(range(1, 8)))
      if 2 in feature or 3 in feature or 1 not in feature:
        raise ValueError('Unsupported Yandex non-POINT geometry')
      # Point is an empty marker message, not a coordinate container.
      if reader.fields(_one(feature, 1, 2)):
        raise ValueError('Unsupported Yandex POINT schema')
      _numbers(feature, 4, 2)
      _one(feature, 5, 0, 0)  # Optional source uint64 feature ID, not invented identity.
      _one(feature, 7, 2, b'')  # Opaque metadata is neither interpreted nor exported.
      if geometry_index >= len(sizes) or sizes[geometry_index] != 1:
        raise ValueError('Yandex POINT geometry size mismatch')
      local_x, local_y = geometry[coordinate_offset:coordinate_offset+2]
      geometry_index += 1
      coordinate_offset += 2
      longitude, latitude = point_to_lonlat(local_x, local_y, x, y, z, bitness)
      properties = {}
      attributes = _one(feature, 6, 2)
      if attributes is not None:
        fields = reader.fields(attributes)
        _known(fields, {1, 2})
        key_id = _one(fields, 1, 0, 0)
        value_ids = _numbers(fields, 2, MAX_ATTRIBUTES)
        if key_id >= len(keys) or len(value_ids) != len(keys[key_id]) or any(i >= len(values) for i in value_ids):
          raise ValueError('Invalid Yandex attribute reference')
        properties = {name: values[index] for name, index in zip(keys[key_id], value_ids, strict=True) if name in PROPERTY_KEYS}
      # All source geometries are consumed to preserve offsets, but only the
      # actually observed public road-events layer supplies event records.
      if layer_name != 'trfe':
        continue
      identity, raw_type, tags = properties.get('road_events_id'), properties.get('eventType'), properties.get('eventTags', [])
      if ((identity is not None and (not isinstance(identity, str) or not 0 < len(identity) <= 512)) or
          (raw_type is not None and not isinstance(raw_type, str)) or
          not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags)):
        raise ValueError('Invalid Yandex raw event attributes')
      result.append({'latitude': latitude, 'longitude': longitude, 'source_id': identity,
                     'raw_type': raw_type, 'raw_tags': tags, 'properties': properties,
                     'projection': 'EPSG:3395', 'geometry_bitness': bitness})
  if geometry_index != len(sizes) or coordinate_offset != len(geometry):
    raise ValueError('Yandex unused geometry records')
  return result
