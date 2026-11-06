"""Synthetic native wire acquisition; no credentials or network calls."""
import importlib
from pathlib import Path
import struct
import sys
from types import ModuleType

import pytest

PACKAGE = 'yandex_native_client_test_package'
package = ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[2] / 'sunnypilot/mapd/yandex')]
sys.modules[PACKAGE] = package
client = importlib.import_module(PACKAGE + '.native_client')
geometry = importlib.import_module(PACKAGE + '.native_geometry')
wire = importlib.import_module(PACKAGE + '.native_codec')


def route_reply():
  polyline = geometry.encode_polyline_scaled(((1, 2), (3, 4), (5, 6)))
  section = wire._blob(5, wire._blob(2, polyline))
  route = wire._blob(1, wire._blob(91, b'\x2a\x00')) + wire._blob(6, section)
  return wire._blob(2, wire._blob(6, route))


def events_reply(index=1, fraction=0.5):
  event = b'\x08' + wire._varint(index) + b'\x39' + struct.pack('<d', fraction) + wire._blob(8, b'speed_control')
  return wire._blob(4, wire._blob(1, event))


def test_exact_request_flow_and_raw_result():
  calls = []

  def transport(method, resource, params, body):
    calls.append((method, resource, params, body))
    return route_reply() if resource == 'route' else events_reply()

  result = client.NativeYandexClient(transport, clock=lambda: 123).fetch_route(45, 38, 46, 39)
  assert calls[0] == ('GET', 'route', {'rll': '38.00000000,45.00000000~39.00000000,46.00000000', 'results': '1', 'lang': 'ru_RU'}, b'')
  assert calls[1][:3] == ('POST', 'conditions', {'segment': '0', 'lang': 'ru_RU'})
  assert calls[1][3] == wire.encode_conditions_request((2,), polyline_wire=result.route['polyline_wire'])
  assert result.route_wire == route_reply() and result.conditions_wire == events_reply()
  assert result.observed_at == 123 and result.complete and not result.auto_control_eligible


@pytest.mark.parametrize('value', [float('nan'), float('inf'), True, '45', 91])
def test_invalid_input_never_calls_transport(value):
  def transport(*args):
    pytest.fail('invalid input reached network boundary')
  with pytest.raises(ValueError):
    client.NativeYandexClient(transport).fetch_route(value, 38, 46, 39)


def test_timeout_propagates_without_retry():
  calls = []

  def transport(*args):
    calls.append(args)
    raise TimeoutError('synthetic')

  with pytest.raises(TimeoutError):
    client.NativeYandexClient(transport).fetch_route(45, 38, 46, 39)
  assert len(calls) == 1


def test_cancel_between_requests():
  calls = []

  def transport(*args):
    calls.append(args)
    return route_reply()

  with pytest.raises(client.AcquisitionCancelled):
    client.NativeYandexClient(transport, cancelled=lambda: bool(calls)).fetch_route(45, 38, 46, 39)
  assert len(calls) == 1


@pytest.mark.parametrize('body', [b'', b'\x08\x01', b'x' * (wire.MAX_BYTES + 1), 'not bytes'])
def test_invalid_route_response_stops_post(body):
  calls = []

  def transport(*args):
    calls.append(args)
    return body

  with pytest.raises(ValueError):
    client.NativeYandexClient(transport).fetch_route(45, 38, 46, 39)
  assert len(calls) == 1


@pytest.mark.parametrize('body', [b'\x0a\x00', events_reply(index=2), events_reply(fraction=1.1), b'\x22\x00\x38\x00'])
def test_invalid_conditions_never_complete(body):
  def transport(method, resource, params, data):
    return route_reply() if resource == 'route' else body
  with pytest.raises(ValueError):
    client.NativeYandexClient(transport).fetch_route(45, 38, 46, 39)
