"""Per-ID camera events backed by local OSM geometry, without control outputs."""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from openpilot.sunnypilot.mapd.camera_road_graph import xy
from openpilot.sunnypilot.mapd.speed_cameras import CameraSpatialIndex, find_nearest_camera


@dataclass(frozen=True)
class CameraSelection:
  state: str = 'IDLE'
  camera_id: str = ''
  camera: object = None
  distance_m: float = 0.
  valid: bool = False
  pass_proven: bool = False
  reason: str = 'no_camera'


def camera_identity(camera):
  # Provider labels are not globally unique. Preserve identity across speed
  # edits while distinguishing collocated records with different directions.
  value = f'{camera.camera_type}|{camera.source_id or ""}|{camera.latitude:.7f}|{camera.longitude:.7f}|{camera.direction_deg}|{camera.second_direction_deg}'
  return hashlib.sha256(value.encode('utf-8')).hexdigest()[:32]


def camera_gps_reliable(gps, now_s):
  """Raw GPS contract uses documented meters/degrees/Unix units, not LLK std."""
  values = (gps.latitude, gps.longitude, gps.bearingDeg, gps.horizontalAccuracy,
            gps.bearingAccuracyDeg, gps.speed, gps.unixTimestampMillis, now_s)
  return bool(all(isinstance(v, (int, float)) and math.isfinite(v) for v in values) and gps.hasFix and
              -90 <= gps.latitude <= 90 and -180 <= gps.longitude <= 180 and
              0 <= gps.bearingDeg <= 360 and 0 < gps.horizontalAccuracy <= 12 and
              0 <= gps.bearingAccuracyDeg <= 15 and gps.speed >= 1 and
              0 <= now_s - gps.unixTimestampMillis * .001 <= 2)


class CameraSpeedSource:
  def __init__(self):
    self.selection = CameraSelection()
    self._camera = None
    self._completed = set()
    self._last_position = None
    self._fix_time = None
    self._behind_fixes = 0
    self._travel_heading = None

  def update(self, cameras, graph, latitude, longitude, heading, *, gps_valid,
             fix_time_s=None, now_s=None, reaction_distance_m=300, driver_override_id=''):
    if reaction_distance_m not in range(50, 1001, 50):
      reaction_distance_m = 300
    point = (latitude, longitude)
    reliable = (gps_valid and all(isinstance(v, (float, int)) and math.isfinite(v) for v in (*point, heading, fix_time_s, now_s))
                and -90 <= latitude <= 90 and -180 <= longitude <= 180 and 0 <= now_s - fix_time_s <= 2)
    duplicate = reliable and self._fix_time == fix_time_s
    if reliable and self._fix_time is not None and fix_time_s < self._fix_time:
      reliable = False
    if not reliable or graph is None:
      # Existing camera identity is retained; invalid never means proven pass.
      self.selection = CameraSelection(state=self.selection.state, camera_id=self.selection.camera_id,
                                       camera=self._camera, reason='gps_unreliable' if not reliable else 'road_graph_unavailable')
      self._last_position = None
      self._behind_fixes = 0
      return self.selection
    if duplicate:
      return self.selection
    previous_time = self._fix_time
    self._fix_time = fix_time_s
    if self._last_position is not None and math.hypot(*xy(point, self._last_position)) > min(100., 60 * (fix_time_s - previous_time) + 5):
      self._last_position = point
      self.selection = CameraSelection(state=self.selection.state, camera_id=self.selection.camera_id,
                                       camera=self._camera, reason='position_jump')
      self._behind_fixes = 0
      return self.selection
    self._last_position = point
    if self.selection.state == 'PASSED':
      self._camera = None
    if self._camera is not None:
      camera = self._camera
      identity = camera_identity(camera)
      distance = graph.camera_distance(point, heading, (camera.latitude, camera.longitude), self._travel_heading)
      if distance is None or distance < -20:
        # Prove forward connectivity from the camera to the current fix, with
        # current travel bearing. Parallel/stacked/branching paths fail closed.
        behind = graph.camera_distance((camera.latitude, camera.longitude), self._travel_heading, point, heading)
        if behind is not None and 20 < behind <= 1000:
          self._behind_fixes += 1
          if self._behind_fixes >= 2:
            self._completed.add(identity)
            self.selection = CameraSelection('PASSED', identity, camera, -behind, False, True, 'route_passed')
            return self.selection
          self.selection = CameraSelection(self.selection.state, identity, camera, reason='awaiting_second_pass_fix')
          return self.selection
      self._behind_fixes = 0
      if distance is None:
        self.selection = CameraSelection(self.selection.state, identity, camera, reason='road_association_ambiguous')
        return self.selection
      state = 'DRIVER_OVERRIDE' if driver_override_id == identity or self.selection.state == 'DRIVER_OVERRIDE' else (
        'ACTIVE' if distance <= reaction_distance_m or self.selection.state == 'ACTIVE' else 'APPROACHING')
      self.selection = CameraSelection(state, identity, camera, distance, True, False, 'route_associated')
      return self.selection
    iterable = cameras.query(latitude, longitude, 5000) if isinstance(cameras, CameraSpatialIndex) else cameras
    candidates = []
    for camera in iterable:
      # Native offset is applied downstream exactly once. A raw20 camera may
      # legitimately become35; stock ACC minimum remains enforced by ICBM.
      if not camera.allowed_auto_control or camera_identity(camera) in self._completed:
        continue
      if find_nearest_camera((camera,), latitude, longitude, heading) is None:
        continue
      directions = [camera.direction_deg] if camera.direction_deg is not None else []
      if camera.second_direction_deg is not None:
        directions.append(camera.second_direction_deg)
      if camera.direction_type == 2:
        directions += [(direction + 180) % 360 for direction in tuple(directions)]
      if camera.direction_type == 0:
        directions = [heading]
      compatible = [d for d in directions if abs((d - heading + 180) % 360 - 180) <= 60]
      if not compatible:
        continue
      travel_heading = min(compatible, key=lambda d: abs((d - heading + 180) % 360 - 180))
      distance = graph.camera_distance(point, heading, (camera.latitude, camera.longitude), travel_heading)
      if distance is not None and 0 <= distance <= 5000:
        candidates.append((distance, camera, travel_heading))
    if not candidates:
      self.selection = CameraSelection(reason='no_unambiguous_camera')
      return self.selection
    distance, self._camera, self._travel_heading = min(candidates, key=lambda item: item[0])
    self.selection = CameraSelection('ACTIVE' if distance <= reaction_distance_m else 'APPROACHING',
                                     camera_identity(self._camera), self._camera, distance, True, False, 'route_associated')
    return self.selection
