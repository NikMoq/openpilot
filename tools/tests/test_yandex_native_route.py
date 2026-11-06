"""Synthetic native Response hierarchy; no saved route data or identities."""
import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest

PACKAGE = 'yandex_route_test_package'
package = ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[2] / 'sunnypilot/mapd/yandex')]
sys.modules[PACKAGE] = package
route = importlib.import_module(PACKAGE + '.native_route')
geometry = importlib.import_module(PACKAGE + '.native_geometry')
wire = importlib.import_module(PACKAGE + '.native_codec')


def blob(number, data):
  return wire._blob(number, data)


def section(points):
  return blob(5, blob(2, geometry.encode_polyline_scaled(points)))


def reply(sections, *, metadata=True, extra_route=b''):
  value = (blob(1, blob(91, b'\x2a\x00')) if metadata else b'') + extra_route
  value += b''.join(blob(6, item) for item in sections)
  return blob(2, blob(6, value))


def test_full_route_joins_all_sections_and_preserves_exact_boundaries():
  points = [(1_000_000, 2_000_000), (1_000_010, 2_000_020), (1_000_030, 2_000_040)]
  value = route.decode_route_geometry(reply([section(points[:2]), section(points[1:])]))
  assert value['geometry_lon_lat_scaled'] == tuple(points)
  assert value['section_ranges'] == ((0, 1), (1, 2))
  assert value['section_segment_counts'] == (1, 1)
  assert sum(value['section_segment_counts']) == len(value['geometry_lon_lat_scaled']) - 1
  assert geometry.decode_polyline_scaled(value['polyline_wire']) == tuple(points)
  assert value['route_metadata_wire'] == b'\x2a\x00'
  assert value['auto_control_eligible'] is False


def test_disconnected_sections_never_invent_connecting_geometry():
  first = section([(1, 2), (3, 4)])
  second = section([(5, 6), (7, 8)])
  with pytest.raises(ValueError, match='disconnected'):
    route.decode_route_geometry(reply([first, second]))


def test_response_error_or_unknown_envelope_is_not_silently_ignored():
  body = reply([section([(1, 2), (3, 4)])])
  with pytest.raises(wire.WireError, match='envelope'):
    route.decode_route_geometry(b'\x08\x01' + body)


@pytest.mark.parametrize('body', [b'', blob(2, b''), blob(2, blob(6, b'') + blob(6, b'')),
                                 reply([], metadata=True), reply([section([(1, 2), (3, 4)])], metadata=False),
                                 reply([section([(1, 2), (3, 4)])], extra_route=blob(1, blob(91, b'\x2a\x00'))),
                                 reply([section([(1, 2)])])])
def test_missing_or_ambiguous_route_components_fail_closed(body):
  with pytest.raises(ValueError):
    route.decode_route_geometry(body)


def test_section_with_point_and_polyline_is_ambiguous():
  ambiguous = blob(5, blob(1, b'') + blob(2, geometry.encode_polyline_scaled([(1, 2), (3, 4)])))
  with pytest.raises(wire.WireError, match='geometry'):
    route.decode_route_geometry(reply([ambiguous]))


def test_aggregate_wire_budget_applies_to_envelope_and_sections(monkeypatch):
  monkeypatch.setattr(wire, 'MAX_FIELDS', 3)
  with pytest.raises(wire.WireError, match='budget'):
    route.decode_route_geometry(reply([section([(1, 2), (3, 4)])]))
