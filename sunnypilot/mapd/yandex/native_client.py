"""Bounded native route/conditions acquisition using an injected transport.

Authentication, deadlines and HTTP response status belong to the transport.
Successful wire acquisition does not confer camera/control eligibility.
"""
from dataclasses import dataclass
import math
import time

from . import native_codec, native_events, native_route
from .native_errors import AcquisitionCancelled


@dataclass(frozen=True)
class NativeRouteResult:
  route_wire: bytes
  conditions_wire: bytes
  route: dict
  conditions: dict
  observed_at: float
  complete: bool = True
  auto_control_eligible: bool = False


class NativeYandexClient:
  def __init__(self, request_callable, clock=time.time, *, cancelled=None):  # noqa: TID251 -- UNIX observation timestamp, not a deadline
    self._request = request_callable
    self._clock = clock
    self._cancelled = cancelled or (lambda: False)

  def _check_cancelled(self):
    if self._cancelled():
      raise AcquisitionCancelled("native acquisition cancelled")

  @staticmethod
  def _body(value):
    if not isinstance(value, bytes) or not value or len(value) > native_codec.MAX_BYTES:
      raise native_codec.WireError("invalid native response byte budget")
    return value

  def fetch_route(self, origin_lat, origin_lon, target_lat, target_lon):
    coordinates = (origin_lat, origin_lon, target_lat, target_lon)
    for value, limit in zip(coordinates, (90, 180, 90, 180), strict=True):
      if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > limit:
        raise ValueError("invalid route coordinate")
    self._check_cancelled()
    rll = f"{origin_lon:.8f},{origin_lat:.8f}~{target_lon:.8f},{target_lat:.8f}"
    route_wire = self._body(self._request("GET", "route", {"rll": rll, "results": "1", "lang": "ru_RU"}, b""))
    self._check_cancelled()
    route = native_route.decode_route_geometry(route_wire)
    counts = route['section_segment_counts']
    if any(type(value) is not int or value <= 0 for value in counts) or sum(counts) != len(route['geometry_lon_lat_scaled']) - 1:
      raise native_codec.WireError("inconsistent section segment counts")
    body = native_codec.encode_conditions_request(counts, polyline_wire=route['polyline_wire'])
    self._check_cancelled()
    conditions_wire = self._body(self._request("POST", "conditions", {"segment": "0", "lang": "ru_RU"}, body))
    self._check_cancelled()
    conditions = native_events.decode_conditions_events(conditions_wire)
    events = conditions['field4_route_events']
    if conditions['unknown_field_numbers'] or events is None or events['unknown_field_numbers']:
      raise native_codec.WireError("unsupported or missing conditions events envelope")
    for event in events['field1_events']:
      index, fraction = event['field1_int32'], event['field7_float64']
      if event['unknown_field_numbers'] or index is None or not 0 <= index < sum(counts) or fraction is None or not 0 <= fraction <= 1:
        raise native_codec.WireError("invalid event route position")
    observed_at = self._clock()
    if isinstance(observed_at, bool) or not isinstance(observed_at, (int, float)) or not math.isfinite(observed_at) or observed_at <= 0:
      raise ValueError("invalid observation time")
    return NativeRouteResult(route_wire, conditions_wire, route, conditions, float(observed_at))
