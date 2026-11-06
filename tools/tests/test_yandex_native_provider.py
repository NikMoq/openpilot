"""Injected native route windows; no network or credential access."""
import importlib
from pathlib import Path
import struct
import sys
from types import ModuleType, SimpleNamespace

import pytest

PACKAGE = '_native_provider_test'
package = ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[2] / 'sunnypilot/mapd/yandex')]
sys.modules[PACKAGE] = package
provider = importlib.import_module(PACKAGE + '.native_provider')
wire = importlib.import_module(PACKAGE + '.native_codec')
NOW = 1_800_000_000


def result(start, end, *, tags=(b'speed_control',), limit=50, observed=NOW, shift=0):
  points = tuple((round((lon + shift) * 1e6), round(lat * 1e6)) for lat, lon in (start, end))
  record = b'\x0d' + struct.pack('<f', limit / 3.6) + b'\x10\x01'
  route = {'geometry_lon_lat_scaled': points, 'section_segment_counts': (1,),
           'section_metadata_wire': (wire._blob(5, wire._blob(1, record)),)}
  event = {'field1_int32': 0, 'field2_bytes': b'synthetic-report', 'field3_enums': [], 'field7_float64': .5,
           'field5_point_wire': None, 'field6_bytes': None, 'field8_bytes': list(tags), 'unknown_field_numbers': []}
  events = [] if tags is None else [event]
  conditions = {'field4_route_events': {'field1_events': events, 'unknown_field_numbers': []}, 'unknown_field_numbers': []}
  return SimpleNamespace(complete=True, observed_at=observed, route=route, conditions=conditions)


class FakeClient:
  def __init__(self, callback=None):
    self.calls = []
    self.callback = callback

  def fetch_route(self, a, b, c, d):
    self.calls.append(((a, b), (c, d)))
    return self.callback(len(self.calls), (a, b), (c, d)) if self.callback else result((a, b), (c, d))


def test_forward_reverse_exact_endpoints():
  client = FakeClient()
  value = provider.NativeYandexProvider('/unused', client=client, clock=lambda: NOW, radius_m=1000).fetch_window(45, 38, 0)
  assert value.complete and value.errors == () and len(value.events) == 2
  assert client.calls[0] == tuple(reversed(client.calls[1]))
  assert client.calls[0][0] == (45, 38)
  assert provider._distance((38, 45), tuple(reversed(client.calls[0][1]))) == pytest.approx(1000)


def test_cancel_before_or_between_routes():
  client = FakeClient()
  source = provider.NativeYandexProvider('/unused', client=client, clock=lambda: NOW)
  with pytest.raises(provider.AcquisitionCancelled):
    source.fetch_window(45, 38, 0, should_continue=lambda: False)
  assert not client.calls
  with pytest.raises(provider.AcquisitionCancelled):
    source.fetch_window(45, 38, 0, should_continue=lambda: not client.calls)
  assert len(client.calls) == 1


def test_partial_reverse_failure_and_all_failure():
  def callback(number, start, end):
    if number == 2:
      raise TimeoutError('private text must not escape')
    return result(start, end)
  source = provider.NativeYandexProvider('/unused', client=FakeClient(callback), clock=lambda: NOW)
  value = source.fetch_window(45, 38, 0)
  assert not value.complete and value.errors == ('reverse:TimeoutError',) and len(value.events) == 1

  def fail(*args):
    raise TimeoutError('synthetic')
  with pytest.raises(TimeoutError):
    provider.NativeYandexProvider('/unused', client=FakeClient(fail), clock=lambda: NOW).fetch_window(45, 38, 0)


def test_mobile_parallel_variant_uses_forward_road_limit():
  def callback(number, start, end):
    return result(start, end, tags=(b'mobile_control', b'in_face'), limit=50 if number == 1 else 70,
                  shift=0 if number == 1 else .00025)
  value = provider.NativeYandexProvider('/unused', client=FakeClient(callback), clock=lambda: NOW, radius_m=1000).fetch_window(45, 38, 0)
  variants = [event for event in value.events if event['road_limit_direction_basis'] == 'matched_mobile_parallel_route']
  assert value.complete and len(variants) == 1
  variant = variants[0]
  assert variant['road_limit_kph'] == 50 and variant['original_road_limit_kph'] == 70
  assert variant['longitude'] == pytest.approx(38.00025)
  assert variant['road_geometry'][0][0] == 38
  assert 15 < variant['report_route_distance_m'] < 25
  assert variant['direction'] is None and not variant['auto_control_eligible']


def test_expired_forward_snapshot_cannot_rejuvenate_parallel_mobile():
  def callback(number, start, end):
    return result(start, end, tags=(b'mobile_control',), observed=NOW - 500 if number == 1 else NOW,
                  shift=0 if number == 1 else .00025)
  value = provider.NativeYandexProvider('/unused', client=FakeClient(callback), clock=lambda: NOW, radius_m=1000).fetch_window(45, 38, 0)
  assert len(value.events) == 1
  assert value.events[0]['road_limit_direction_basis'] == 'acquired_route_forward_edge'
  assert value.events[0]['observed_at'] == NOW


def test_endpoint_mismatch_marks_partial():
  def callback(number, start, end):
    return result(start, end, shift=.1 if number == 1 else 0)
  value = provider.NativeYandexProvider('/unused', client=FakeClient(callback), clock=lambda: NOW).fetch_window(45, 38, 0)
  assert not value.complete and value.errors == ('forward:ValueError',)
  assert len(value.events) == 1


@pytest.mark.parametrize('args', [(85, 38, 0), (45, 181, 0), (45, 38, 360), (45, 38, float('nan')), (True, 38, 0)])
def test_invalid_window_never_requests(args):
  client = FakeClient()
  with pytest.raises(ValueError):
    provider.NativeYandexProvider('/unused', client=client).fetch_window(*args)
  assert not client.calls


@pytest.mark.parametrize('radius', [99, 5001, True, float('inf')])
def test_radius_bounds(radius):
  with pytest.raises(ValueError):
    provider.NativeYandexProvider('/unused', radius_m=radius)


def test_projection_budget_preserves_source_events_without_partial_matches(monkeypatch):
  monkeypatch.setattr(provider, 'MAX_PROJECTION_CHECKS', 0)

  def callback(number, start, end):
    return result(start, end, tags=(b'mobile_control',), shift=0 if number == 1 else .00025)

  value = provider.NativeYandexProvider('/unused', client=FakeClient(callback), clock=lambda: NOW, radius_m=1000).fetch_window(45, 38, 0)
  assert value.complete and len(value.events) == 2
  assert all(event['road_limit_direction_basis'] == 'acquired_route_forward_edge' for event in value.events)


def test_long_projection_scan_checks_cancellation():
  forward = result((45, 38), (45.01, 38))
  forward.route['geometry_lon_lat_scaled'] = tuple((38_000_000, 45_000_000 + index) for index in range(1000))
  reverse = result((45.01, 38.00025), (45, 38.00025), tags=(b'mobile_control',))
  event = provider.normalize_native_snapshot(reverse, now=NOW)[0]
  checks = []

  def keep_going():
    checks.append(True)
    return len(checks) < 2

  with pytest.raises(provider.AcquisitionCancelled):
    provider._parallel_variant(event, forward, limits=(50 / 3.6,) * 999,
                               projection_budget=[200_000], should_continue=keep_going)
  assert len(checks) == 2


@pytest.mark.parametrize('direction', ['forward', 'reverse'])
@pytest.mark.parametrize('distance,accepted', [(600, True), (1001, False)])
def test_synthetic_endpoint_snap_is_bounded_and_marks_partial(direction, distance, accepted):
  def callback(number, start, end):
    value = result(start, end)
    if number == (1 if direction == 'forward' else 2):
      points = list(value.route['geometry_lon_lat_scaled'])
      index = -1 if direction == 'forward' else 0
      far = provider._destination(points[index][1] / 1e6, points[index][0] / 1e6, 180, distance)
      points[index] = (round(far[1] * 1e6), round(far[0] * 1e6))
      value.route['geometry_lon_lat_scaled'] = tuple(points)
    return value

  value = provider.NativeYandexProvider('/unused', client=FakeClient(callback), clock=lambda: NOW, radius_m=5000).fetch_window(45, 38, 0)
  assert not value.complete
  assert value.errors == (direction + (':target_snapped' if accepted else ':ValueError'),)
  assert len(value.events) == (2 if accepted else 1)


@pytest.mark.parametrize('direction', ['forward', 'reverse'])
def test_real_vehicle_endpoint_remains_strict(direction):
  def callback(number, start, end):
    value = result(start, end)
    if number == (1 if direction == 'forward' else 2):
      points = list(value.route['geometry_lon_lat_scaled'])
      index = 0 if direction == 'forward' else -1
      points[index] = (points[index][0] + 3000, points[index][1])
      value.route['geometry_lon_lat_scaled'] = tuple(points)
    return value

  value = provider.NativeYandexProvider('/unused', client=FakeClient(callback), clock=lambda: NOW).fetch_window(45, 38, 0)
  assert not value.complete and value.errors == (direction + ':ValueError',)
  assert len(value.events) == 1
