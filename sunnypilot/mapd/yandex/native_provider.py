"""Native Linux forward/reverse acquisition for the bounded camera worker."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import math
from pathlib import Path

from .native_client import AcquisitionCancelled, NativeYandexClient
from .native_limits import decode_route_limits
from .native_snapshot import LIMIT_CONFIDENCE, _bearing, _distance, approach_context, normalize_native_snapshot
from .native_transport import NativeHTTPTransport, load_private_credentials

MAX_WINDOW_POINTS = 10_000
MAX_EVENTS = 5000
MAX_PROJECTION_CHECKS = 200_000


def _utc_now_s():
  return datetime.now(UTC).timestamp()


@dataclass(frozen=True)
class NativeWindowResult:
  events: tuple[dict, ...]
  complete: bool
  errors: tuple[str, ...] = ()


def _destination(latitude, longitude, heading, distance):
  latitude, longitude, bearing = map(math.radians, (latitude, longitude, heading))
  angular = distance / 6_371_000
  target_lat = math.asin(math.sin(latitude) * math.cos(angular) + math.cos(latitude) * math.sin(angular) * math.cos(bearing))
  target_lon = longitude + math.atan2(math.sin(bearing) * math.sin(angular) * math.cos(latitude),
                                    math.cos(angular) - math.sin(latitude) * math.sin(target_lat))
  return math.degrees(target_lat), (math.degrees(target_lon) + 180) % 360 - 180


def _canonical_limit(raw):
  if raw is None or not math.isfinite(raw) or raw <= 0:
    return None
  value = raw * 3.6
  rounded = round(value)
  return rounded if 0 < rounded <= 300 and abs(value - rounded) <= 1e-4 else None


def _parallel_variant(event, forward, *, limits=None, projection_budget=None, should_continue=lambda: True):
  """Use the driven road's limit, never the opposite carriageway's limit."""
  if event['source_kind'] != 'mobile':
    return None
  points = forward.route['geometry_lon_lat_scaled']
  if len(points) > MAX_WINDOW_POINTS:
    return None
  if projection_budget is not None:
    if projection_budget[0] < len(points) - 1:
      return None  # An incomplete scan cannot establish unique association.
    projection_budget[0] -= len(points) - 1
  physical = (event['longitude'], event['latitude'])
  origin_lat = math.radians(physical[1])
  candidates = []
  for index, (qa, qb) in enumerate(zip(points, points[1:], strict=False)):
    if index % 128 == 0 and not should_continue():
      raise AcquisitionCancelled('native window cancelled')
    a, b = tuple(v / 1e6 for v in qa), tuple(v / 1e6 for v in qb)
    if a == b or abs(a[0] - b[0]) > 180:
      continue
    heading = _bearing(a, b)
    if abs((heading - event['road_heading_deg'] + 180) % 360 - 180) < 150:
      continue
    ax = math.radians(a[0] - physical[0]) * math.cos(origin_lat)
    ay = math.radians(a[1] - physical[1])
    bx = math.radians(b[0] - physical[0]) * math.cos(origin_lat)
    by = math.radians(b[1] - physical[1])
    dx, dy = bx - ax, by - ay
    norm = dx * dx + dy * dy
    if not norm:
      continue
    fraction = max(0., min(1., -(ax * dx + ay * dy) / norm))
    projected = (a[0] + fraction * (b[0] - a[0]), a[1] + fraction * (b[1] - a[1]))
    distance = _distance(physical, projected)
    if distance <= 40:
      candidates.append((distance, index, fraction, projected, (a, b), heading))
  if not candidates:
    return None
  candidates.sort()
  best = candidates[0]
  # Adjacent edges share a junction; a separate equally close road is ambiguous.
  if any(abs(item[1] - best[1]) > 1 and item[0] <= best[0] + 3 for item in candidates[1:]):
    return None
  limits = decode_route_limits(forward.route) if limits is None else limits
  raw = limits[best[1]]
  limit = _canonical_limit(raw)
  if limit is None:
    return None
  # At a limit boundary the opposite-side report does not identify which edge.
  if any(item[0] <= best[0] + .1 and limits[item[1]] != raw for item in candidates[1:]):
    return None
  distance, index, fraction, projected, geometry, heading = best
  observed = min(event['observed_at'], forward.observed_at)
  result = dict(event, original_road_geometry=event['road_geometry'],
                original_road_limit_kph=event['road_limit_kph'], original_road_limit_raw_mps=event['road_limit_raw_mps'],
                original_road_heading_deg=event['road_heading_deg'],
                original_route_position_longitude=event['route_position_longitude'],
                original_route_position_latitude=event['route_position_latitude'],
                original_position_index=event['position_index'], original_position_fraction=event['position_fraction'],
                original_report_route_distance_m=event['report_route_distance_m'],
                road_geometry=geometry, road_heading_deg=heading, position_index=index, position_fraction=fraction,
                route_position_longitude=projected[0], route_position_latitude=projected[1], report_route_distance_m=distance,
                road_limit_raw_mps=raw, road_limit_kph=limit, road_limit_unit='km/h',
                speed_limit_confidence=LIMIT_CONFIDENCE, road_limit_direction_basis='matched_mobile_parallel_route',
                match_unique=True, observed_at=observed, observed_at_ms=int(observed * 1000),
                expires_at=min(event['expires_at'], observed + 180))
  result['expires_at_ms'] = int(result['expires_at'] * 1000)
  result.update(approach_context(points, index, fraction, observed, result['expires_at']))
  return result


class NativeYandexProvider:
  def __init__(self, credentials_path, *, client=None, clock=_utc_now_s, radius_m=5000):
    if type(radius_m) not in (int, float) or not math.isfinite(radius_m) or not 100 <= radius_m <= 5000:
      raise ValueError('invalid native window radius')
    self.credentials_path, self.client = Path(credentials_path), client
    self.clock, self.radius_m = clock, radius_m

  def fetch_window(self, latitude, longitude, heading, *, should_continue=lambda: True):
    if (any(type(value) not in (int, float) or not math.isfinite(value) for value in (latitude, longitude, heading))
        or not -85 < latitude < 85 or not -180 <= longitude <= 180 or not 0 <= heading < 360):
      raise ValueError('invalid native window position')
    if not should_continue():
      raise AcquisitionCancelled('native window cancelled')
    transport = None
    try:
      client = self.client
      if client is None:
        # Each generation gets its own HTTPS session: cancelled and new workers
        # may overlap while the old socket reaches its bounded timeout.
        transport = NativeHTTPTransport(*load_private_credentials(self.credentials_path))
        client = NativeYandexClient(transport, clock=self.clock, cancelled=lambda: not should_continue())
      target = _destination(latitude, longitude, heading, self.radius_m)
      origin = (latitude, longitude)
      records, errors, snapshots = [], [], {}
      first_error = None
      for name, start, end in (('forward', origin, target), ('reverse', target, origin)):
        if not should_continue():
          raise AcquisitionCancelled('native window cancelled')
        try:
          snapshot = client.fetch_route(*start, *end)
          points = snapshot.route['geometry_lon_lat_scaled']
          if len(points) > MAX_WINDOW_POINTS:
            raise ValueError('native window point budget')
          first, last = points[0], points[-1]
          vehicle_point, far_point = (first, last) if name == 'forward' else (last, first)
          vehicle_point = (vehicle_point[0] / 1e6, vehicle_point[1] / 1e6)
          far_point = (far_point[0] / 1e6, far_point[1] / 1e6)
          target_snap = _distance((target[1], target[0]), far_point)
          if (_distance((longitude, latitude), vehicle_point) > 150 or target_snap > 1000 or
              _distance((longitude, latitude), far_point) > self.radius_m + 1000):
            raise ValueError('native route endpoints differ from requested window')
          normalized = normalize_native_snapshot(snapshot, now=self.clock())
          snapshots[name] = snapshot
          records.extend(normalized)
          if target_snap > 150:
            errors.append(name + ':target_snapped')
          if len(records) > MAX_EVENTS:
            raise ValueError('native window event budget')
        except AcquisitionCancelled:
          raise
        except Exception as error:
          first_error = first_error or error
          errors.append(name + ':' + type(error).__name__)
      if not snapshots:
        raise first_error
      if 'forward' in snapshots:
        projection_budget = [MAX_PROJECTION_CHECKS]
        forward_limits = decode_route_limits(snapshots['forward'].route)
        for event in tuple(records):
          if not should_continue():
            raise AcquisitionCancelled('native window cancelled')
          if event['source_kind'] == 'mobile':
            variant = _parallel_variant(event, snapshots['forward'], limits=forward_limits,
                                        projection_budget=projection_budget, should_continue=should_continue)
            if variant is not None:
              records.append(variant)
      if len(records) > MAX_EVENTS:
        raise ValueError('native window event budget')
      now = self.clock()
      near = tuple(event for event in records
                   if event['expires_at'] > now
                   and _distance((longitude, latitude), (event['longitude'], event['latitude'])) <= self.radius_m + 1000)
      if not should_continue():
        raise AcquisitionCancelled('native window cancelled')
      return NativeWindowResult(near, len(snapshots) == 2 and not errors, tuple(errors))
    finally:
      if transport is not None:
        transport.close()
