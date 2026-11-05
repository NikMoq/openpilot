"""Real public VECTOR3 fixtures; no sockets, registration or control assertions."""
import importlib.util
import math
from pathlib import Path
import socket

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('yandex_point_decoder_test', ROOT/'sunnypilot/mapd/yandex/tiles.py')
tiles = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tiles)
FIXTURES = ROOT/'sunnypilot/mapd/yandex/fixtures'


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
  def forbidden(*_args, **_kwargs):
    pytest.fail('Decoder tests must never open sockets')
  monkeypatch.setattr(socket, 'create_connection', forbidden)
  monkeypatch.setattr(socket, 'getaddrinfo', forbidden)


def varint(value):
  result = bytearray()
  while value >= 128:
    result.append((value & 127) | 128)
    value >>= 7
  result.append(value)
  return bytes(result)


def field(number, value, wire=2):
  return varint(number*8 + wire) + (varint(len(value)) + value if wire == 2 else varint(value))


def point_tile(*, geometry=None, sizes=b'\1', point=b'', feature_geometry=1, property_type='POLICE', tags=('police',), layer='trfe'):
  if geometry is None:
    geometry = varint(11) + varint(63)  # Source sint32 -6,-32.
  keys = ('road_events_id', 'eventType', 'eventTags', 'category')
  key_table = b''.join(field(1, name.encode()) for name in keys)
  values = (field(1, b'raw-event-id'), field(1, property_type.encode()),
            field(8, b''.join(field(1, tag.encode()) for tag in tags)), field(2, 16, wire=0))
  # Category AttributeValue.field2 is sint32 wire0, not an enum classification.
  attrs = field(1, 0, wire=0) + field(2, bytes(range(4)))
  feature = field(feature_geometry, point) + field(6, attrs)
  layer_data = field(1, layer.encode()) + field(2, feature)
  return (field(1, 8, wire=0) + field(2, geometry) + field(3, sizes) + field(4, key_table) +
          b''.join(field(5, value) for value in values) + field(6, layer_data))


def parse(body):
  return tiles.parse_tile(body, 4952, 2568, 13)


def test_actual_moscow_points_and_raw_attributes():
  records = parse((FIXTURES/'moscow-4952-2568-z13.bin').read_bytes())
  assert len(records) == 2
  assert records[0]['source_id'] == 'ud02eda42-b066-5287-81b9-9249b5c7b586'
  assert records[0]['raw_type'] == 'ACCIDENT'
  assert records[0]['raw_tags'] == ['accident']
  assert records[0]['properties']['category'] == 4  # Raw sint32(8) zigzag-decodes to4.
  assert records[0]['longitude'] == pytest.approx(37.63812614889706, abs=1e-10)
  assert records[0]['latitude'] == pytest.approx(55.74816254430081, abs=1e-10)
  assert records[1]['raw_type'] == 'RECONSTRUCTION'
  assert records[1]['raw_tags'] == ['reconstruction']
  # The second geometry resets deltas; it must not start at the first point.
  assert records[1]['longitude'] == pytest.approx(37.634162454044116, abs=1e-10)
  assert records[1]['latitude'] == pytest.approx(55.74787093604948, abs=1e-10)
  assert all(row['projection'] == 'EPSG:3395' and row['geometry_bitness'] == 8 for row in records)
  assert all('speed_limit_kph' not in row and 'allowed_auto_control' not in row for row in records)


def test_empty_real_response_and_default_bitness():
  body = (FIXTURES/'empty.bin').read_bytes()
  assert body == b'\x08\x08'
  assert parse(body) == []
  actual = (FIXTURES/'moscow-4952-2568-z13.bin').read_bytes()
  assert actual[:2] == body
  assert parse(actual[2:]) == parse(actual)


def test_projection_is_ellipsoidal_and_has_correct_axis_signs():
  center = tiles.point_to_lonlat(0, 0, 4952, 2568, 13, 8)
  left_south = tiles.point_to_lonlat(-6, 32, 4952, 2568, 13, 8)
  assert left_south[0] < center[0] and left_south[1] < center[1]
  spherical = math.degrees(math.atan(math.sinh(math.pi*(1 - 2*(2568+.5)/8192))))
  assert center[1] - spherical > .1


def test_police_remains_raw_without_mobile_or_camera_inference():
  record, = parse(point_tile())
  assert record['raw_type'] == 'POLICE'
  assert record['raw_tags'] == ['police']
  assert record['source_id'] == 'raw-event-id'
  assert 'source_kind' not in record and 'speed_unit' not in record
  assert record['properties']['category'] == 8


@pytest.mark.parametrize('body', [b'', b'\x00', b'\x08', b'\x08\x80\x00', b'\x08'+b'\xff'*11,
                                b'\x12\x10\x00', b'\x0b', b'\x08\x08\x08\x08', b'\x40\x00'])
def test_malformed_or_unknown_wire_schema_rejected(body):
  with pytest.raises(ValueError):
    parse(body)


@pytest.mark.parametrize('geometry_field', [2, 3])
def test_unsupported_geometry_rejects_without_corrupting_later_point_offsets(geometry_field):
  with pytest.raises(ValueError, match='non-POINT'):
    parse(point_tile(feature_geometry=geometry_field))


def test_nonempty_point_message_is_unknown_schema():
  with pytest.raises(ValueError, match='POINT schema'):
    parse(point_tile(point=field(1, 5, wire=0)))


@pytest.mark.parametrize('changes', [{'sizes':b'\2'}, {'sizes':b''}, {'sizes':b'\0'}, {'geometry':b'\x01'}])
def test_geometry_counts_are_not_guessed(changes):
  with pytest.raises(ValueError, match='geometry'):
    parse(point_tile(**changes))


def test_global_budgets_and_coordinate_range():
  with pytest.raises(ValueError, match='size'):
    parse(b'x'*(tiles.MAX_BYTES+1))
  with pytest.raises(ValueError, match='budget'):
    parse(point_tile(geometry=field(1, 0, wire=0)*(tiles.MAX_COORDINATES+1)))
  with pytest.raises(ValueError, match='projection'):
    parse(point_tile(geometry=varint(512)+varint(0)))


@pytest.mark.parametrize('coordinates', [(True,2568,13),(-1,2568,13),(8192,2568,13),(4952,8192,13),(0,0,24),(0,0,-1)])
def test_requested_tile_bounds_even_for_empty_response(coordinates):
  with pytest.raises(ValueError, match='projection'):
    tiles.parse_tile(b'\x08\x08', *coordinates)


def test_attribute_reference_and_utf8_errors_fail_closed():
  body = point_tile()
  marker = field(6, field(1,0,wire=0) + field(2,bytes(range(4))))
  bad = field(6, field(1,99,wire=0) + field(2,bytes(range(4))))
  with pytest.raises(ValueError, match='reference'):
    parse(body.replace(marker, bad))
  with pytest.raises(ValueError, match='UTF-8'):
    parse(body.replace(b'POLICE', b'\xffOLICE'))


def test_other_layer_points_consumed_but_not_published_as_road_events():
  assert parse(point_tile(layer='unrelated')) == []
