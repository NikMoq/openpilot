"""Synthetic source semantics only; no real event identities or credentials."""
import importlib
from pathlib import Path
import struct
import sys
from types import ModuleType, SimpleNamespace

import pytest

PACKAGE = '_native_snapshot_test'
package = ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[2] / 'sunnypilot/mapd/yandex')]
sys.modules[PACKAGE] = package
snapshot = importlib.import_module(PACKAGE + '.native_snapshot')
wire = importlib.import_module(PACKAGE + '.native_codec')
NOW = 1_800_000_000


def acquisition(tags=(b'speed_control',), raw=16.66666603088379, point=None):
  metadata = None if raw is None else wire._blob(5, wire._blob(1, b'\x0d' + struct.pack('<f', raw) + b'\x10\x01'))
  route = {'geometry_lon_lat_scaled': ((38_000_000, 45_000_000), (38_000_000, 45_001_000)),
           'section_metadata_wire': (metadata,), 'section_segment_counts': (1,)}
  event = {'field1_int32': 0, 'field2_bytes': b'synthetic-event', 'field3_enums': [], 'field7_float64': 0.5,
           'field5_point_wire': point, 'field6_bytes': b'not exported', 'field8_bytes': list(tags), 'unknown_field_numbers': []}
  conditions = {'field4_route_events': {'field1_events': [event], 'unknown_field_numbers': []}, 'unknown_field_numbers': []}
  return SimpleNamespace(complete=True, observed_at=NOW, route=route, conditions=conditions)


@pytest.mark.parametrize('raw,kph', [(5.55555534362793, 20), (11.11111068725586, 40), (16.66666603088379, 60),
                                    (None, None), (0, None), (1, None)])
def test_verified_si_conversion(raw, kph):
  result = snapshot.normalize_native_snapshot(acquisition(raw=raw), now=NOW)[0]
  assert result['road_limit_kph'] == kph
  assert result['source'] == 'yandex' and result['source_sdk'] == '4.45.0-full'
  assert result['speed_limit_confidence'] == (snapshot.LIMIT_CONFIDENCE if kph else None)
  assert result['source_id'] == 'yandex:native:synthetic-event'
  assert result['road_geometry'] == ((38, 45), (38, 45.001))
  assert result['latitude'] == pytest.approx(45.0005)
  assert result['road_heading_deg'] == pytest.approx(0)
  assert result['auto_control_eligible'] is False
  assert 'description' not in result and 'descriptionText' not in result


@pytest.mark.parametrize('tags,kind', [((b'police',), 'warning'), ((b'school',), 'warning'),
                                      ((b'speed_control', b'police'), 'fixed'),
                                      ((b'mobile_control', b'police', b'in_face'), 'mobile'),
                                      ((b'police_patrol', b'in_back'), 'mobile')])
def test_tag_semantics_and_mobile_facing(tags, kind):
  value = snapshot.normalize_native_snapshot(acquisition(tags=tags), now=NOW)[0]
  assert value['source_kind'] == kind
  assert value['direction'] is None
  assert value['in_face'] == (b'in_face' in tags)
  assert value['in_back'] == (b'in_back' in tags)
  assert value['road_limit_direction_basis'] == 'acquired_route_forward_edge'


def test_unknown_tokens_are_not_police_or_camera():
  assert snapshot.normalize_native_snapshot(acquisition(tags=(b'future_tag',)), now=NOW) == []
  value = snapshot.normalize_native_snapshot(acquisition(tags=(b'speed_control', b'future_tag')), now=NOW)[0]
  assert 'future_tag' in value['raw_tags'] and value['tags'] == ('speed_control',)


@pytest.mark.parametrize('tags,ttl', [((b'mobile_control',), 180), ((b'speed_control',), 300)])
def test_ttl_never_renews_on_read(tags, ttl):
  result = acquisition(tags=tags)
  early = snapshot.normalize_native_snapshot(result, now=NOW + 1)[0]
  late = snapshot.normalize_native_snapshot(result, now=NOW + ttl - 1)[0]
  assert early['observed_at'] == late['observed_at'] == NOW
  assert early['expires_at'] == late['expires_at'] == NOW + ttl
  assert snapshot.normalize_native_snapshot(result, now=NOW + ttl) == []


def test_point_double_order_and_route_mismatch():
  point = b'\x09' + struct.pack('<d', 38) + b'\x11' + struct.pack('<d', 45.0005)
  value = snapshot.normalize_native_snapshot(acquisition(point=point), now=NOW)[0]
  assert value['latitude'] == 45.0005 and value['longitude'] == 38
  point = b'\x09' + struct.pack('<d', 39) + b'\x11' + struct.pack('<d', 45)
  with pytest.raises(ValueError, match='does not match'):
    snapshot.normalize_native_snapshot(acquisition(point=point), now=NOW)


@pytest.mark.parametrize('field,value', [('field1_int32', -1), ('field1_int32', 1), ('field7_float64', 1.1),
                                       ('field7_float64', float('nan')), ('field2_bytes', b'\xff'),
                                       ('field5_point_wire', b'\x09' + struct.pack('<d', 38))])
def test_malformed_events_fail_closed(field, value):
  result = acquisition()
  result.conditions['field4_route_events']['field1_events'][0][field] = value
  with pytest.raises(ValueError):
    snapshot.normalize_native_snapshot(result, now=NOW)


def test_incomplete_future_and_mismatched_limits():
  result = acquisition()
  result.complete = False
  with pytest.raises(ValueError):
    snapshot.normalize_native_snapshot(result, now=NOW)
  result.complete = True
  with pytest.raises(ValueError):
    snapshot.normalize_native_snapshot(result, now=NOW - 10)
  result.route['section_segment_counts'] = (2,)
  with pytest.raises(ValueError):
    snapshot.normalize_native_snapshot(result, now=NOW)
