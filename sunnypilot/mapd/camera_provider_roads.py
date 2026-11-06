"""Per-camera provider geometry, only where offline road coverage is absent.

No road is extrapolated and no covered OSM ambiguity is overridden. The shared
approach/pass state machine remains responsible for confirmation and passage.
"""
import json
import math

from openpilot.sunnypilot.mapd.camera_speed_source import angle_difference, xy

MAX_PROJECTIONS = 200_000


class ProviderRoadGraph:
  supports_vehicle_uncertainty = True

  def __init__(self, offline, *, now_s):
    self.offline, self.now_s = offline, now_s
    self._paths = {}
    self._fresh = {}
    self._budget = MAX_PROJECTIONS

  def _fresh_camera(self, camera):
    key = id(camera)
    if key not in self._fresh:
      if camera.camera_type.startswith('waze:'):
        from openpilot.sunnypilot.mapd.waze_policy import filter_fresh_waze
        self._fresh[key] = bool(camera.allowed_auto_control and filter_fresh_waze((camera,), self.now_s))
      elif camera.camera_type.startswith('yandex:'):
        from openpilot.sunnypilot.mapd.yandex_policy import filter_fresh_yandex
        self._fresh[key] = bool(camera.allowed_auto_control and filter_fresh_yandex((camera,), self.now_s))
      else:
        self._fresh[key] = True
    return self._fresh[key]

  def __getattr__(self, name):
    if self.offline is not None:
      return getattr(self.offline, name)
    raise AttributeError(name)

  def camera_is_fresh(self, camera):
    return self._fresh_camera(camera)

  def _path(self, camera):
    key = id(camera)
    if key in self._paths:
      return self._paths[key]
    path = None
    try:
      if camera.camera_type.startswith('waze:'):
        from openpilot.sunnypilot.mapd.waze_policy import filter_fresh_waze
        if not camera.allowed_auto_control or not filter_fresh_waze((camera,), self.now_s):
          return None
        event = json.loads(camera.source_params['waze_event'])
        geometry = event['road_geometry']
        maximum = 10000
      elif camera.camera_type.startswith('yandex:'):
        from openpilot.sunnypilot.mapd.yandex_policy import filter_fresh_yandex
        if not camera.allowed_auto_control or not filter_fresh_yandex((camera,), self.now_s):
          return None
        event = json.loads(camera.source_params['yandex_event'])
        if (event.get('approach_source') != 'yandex_native_route'
            or event.get('approach_observed_at') != event['observed_at']
            or event.get('approach_expires_at') != event['expires_at']):
          return None
        geometry = event['approach_geometry']
        maximum = 512
      else:
        return None
      if not isinstance(geometry, (tuple, list)) or not 2 <= len(geometry) <= maximum:
        return None
      if len(geometry) > self._budget:
        return None
      self._budget -= len(geometry)
      points = []
      for point in geometry:
        if (not isinstance(point, (tuple, list)) or len(point) != 2
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in point)
            or abs(point[0]) > 180 or abs(point[1]) > 90):
          return None
        points.append((point[1], point[0]))
      segments, distance = [], 0.
      for a, b in zip(points, points[1:], strict=False):
        if a == b:
          continue  # Repeated route vertices do not invent a gap or an edge.
        dx, dy = xy(b, a)
        length = math.hypot(dx, dy)
        if length == 0 or abs(b[1] - a[1]) > 180:
          return None
        segments.append((a, b, length, distance, math.degrees(math.atan2(dx, dy)) % 360))
        distance += length
      path = segments or None
    except (KeyError, ValueError, TypeError, AttributeError):
      pass
    finally:
      self._paths[key] = path
    return path

  def _project(self, path, point, heading, tolerance, heading_tolerance):
    if self._budget < len(path):
      return None
    self._budget -= len(path)
    candidates = []
    for a, b, length, offset, bearing in path:
      dx, dy = xy(b, a)
      x, y = xy(point, a)
      along = (x * dx + y * dy) / length
      if not 0 <= along <= length:
        continue
      distance = math.hypot(x - along * dx / length, y - along * dy / length)
      forward = angle_difference(bearing, heading) <= heading_tolerance
      reverse = angle_difference((bearing + 180) % 360, heading) <= heading_tolerance
      direction = 1 if forward else -1 if reverse else 0
      if distance <= tolerance and direction:
        candidates.append((distance, offset + along, direction))
    if not candidates:
      return None
    candidates.sort()
    best = candidates[0]
    if any(distance <= best[0] + 8 and (direction != best[2] or abs(along - best[1]) > 2 * tolerance)
           for distance, along, direction in candidates[1:]):
      return None
    return best[1], best[2]

  def _fallback_allowed(self, path, point, heading, target, target_heading, options, interval):
    offline = self.offline
    if offline is None:
      return True
    inside = getattr(offline, 'inside', None)
    associate = getattr(offline, 'associate', None)
    if not callable(inside) or not callable(associate):
      return False
    # Inside covered maps, failure to associate can mean competing parallel
    # roads or a junction. It is not permission to choose the provider road.
    if inside(point) and associate(point, heading, tolerance=options.get('vehicle_tolerance_m', 12.),
                                   heading_tolerance=options.get('vehicle_heading_tolerance_deg', 35.)) is None:
      return False
    if inside(target) and associate(target, target_heading, tolerance=25.) is None:
      return False
    if not inside(point) or not inside(target):
      return True
    covered = getattr(offline, 'segment_covered', None)
    if not callable(covered) or self._budget < len(path):
      return False
    self._budget -= len(path)
    low, high = sorted(interval)
    for a, b, length, offset, _ in path:
      begin, end = max(low, offset), min(high, offset + length)
      if begin >= end:
        continue
      first = tuple(a[axis] + (b[axis] - a[axis]) * (begin - offset) / length for axis in (0, 1))
      last = tuple(a[axis] + (b[axis] - a[axis]) * (end - offset) / length for axis in (0, 1))
      if not covered(first, last):
        return True
    return False

  def _distance(self, camera, point, heading, camera_heading, *, passed=False, **options):
    target = (camera.latitude, camera.longitude)
    if not self._fresh_camera(camera):
      return None
    if self.offline is not None:
      method = getattr(self.offline, 'passed_distance' if passed else 'camera_distance', None)
      if callable(method):
        distance = method(point, heading, target, camera_heading, **options)
        if distance is not None:
          return distance
    path = self._path(camera)
    if path is None:
      return None
    vehicle = self._project(path, point, heading, options.get('vehicle_tolerance_m', 12.),
                            options.get('vehicle_heading_tolerance_deg', 35.))
    control = self._project(path, target, camera_heading, 25., 35.)
    if vehicle is None or control is None or vehicle[1] != control[1]:
      return None
    distance = (control[0] - vehicle[0]) * vehicle[1] * (-1 if passed else 1)
    if distance < 0 or not self._fallback_allowed(path, point, heading, target, camera_heading, options, (vehicle[0], control[0])):
      return None
    return distance

  def camera_distance_for_camera(self, camera, point, heading, camera_heading, **options):
    return self._distance(camera, point, heading, camera_heading, **options)

  def passed_distance_for_camera(self, camera, point, heading, camera_heading, **options):
    return self._distance(camera, point, heading, camera_heading, passed=True, **options)
