"""Offline per-camera events from provider metadata and localized approach.

No map or network is required. Direction and a narrow approach corridor reject
opposing and laterally separated cameras; point records cannot prove which of
closely parallel same-direction roads a vehicle occupies. Distances are signed
local projections, not invented road-route lengths.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from openpilot.sunnypilot.mapd.speed_cameras import CameraSpatialIndex, EARTH_RADIUS_M
from openpilot.sunnypilot.mapd.camera_odometry import CameraOdometry, MAX_PLAUSIBLE_SPEED_MPS

# Maximum age of a localized position sample, not a satellite-fix timeout.
MAX_POSITION_AGE_S = 3.

LATERAL_CORRIDOR_M = 25.0
HEADING_TOLERANCE_DEG = 45.0
LOOKAHEAD_M = 5000.0
APPROACH_MOVEMENT_M = 2.0
APPROACH_PROGRESS_M = 1.0
PASS_DISTANCE_M = 20.0


def xy(point, origin):
  return (math.radians(point[1] - origin[1]) * EARTH_RADIUS_M * math.cos(math.radians(origin[0])),
          math.radians(point[0] - origin[0]) * EARTH_RADIUS_M)


def angle_difference(a, b):
  return abs((a - b + 180) % 360 - 180)


def projection(vector, heading):
  radians = math.radians(heading)
  x, y = vector
  return x * math.sin(radians) + y * math.cos(radians), x * math.cos(radians) - y * math.sin(radians)


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
  value = f'{camera.camera_type}|{camera.source_id or ""}|{camera.latitude:.7f}|{camera.longitude:.7f}|{camera.direction_deg}|{camera.second_direction_deg}'
  return hashlib.sha256(value.encode('utf-8')).hexdigest()[:32]


def travel_directions(camera, heading, *, filter_heading=True):
  if camera.direction_type == 0:
    return (heading,)  # Explicit all-directions metadata, not a missing bearing.
  if camera.direction_type not in (1, 2):
    return ()
  directions = [d for d in (camera.direction_deg, camera.second_direction_deg)
                if isinstance(d, (int, float)) and math.isfinite(d) and 0 <= d <= 360]
  if camera.direction_type == 2:
    directions += [(d + 180) % 360 for d in tuple(directions)]
  return tuple(d for d in directions if not filter_heading or angle_difference(d, heading) <= HEADING_TOLERANCE_DEG)


def camera_projection(camera, point, heading, travel_heading):
  if angle_difference(travel_heading, heading) > HEADING_TOLERANCE_DEG:
    return None
  delta = xy((camera.latitude, camera.longitude), point)
  ahead, lateral = projection(delta, travel_heading)
  vehicle_ahead, _ = projection(delta, heading)
  # Course is an angular measurement: a fixed lateral gate projected from it
  # would amplify even 2 degrees of GPS noise into a false 31-m offset at900m.
  # Provider direction supplies the fixed corridor; measured movement below
  # independently confirms that we are approaching along it.
  if abs(lateral) > LATERAL_CORRIDOR_M:
    return None
  if ahead * vehicle_ahead < 0:
    return None
  return ahead, lateral


class CameraSpeedSource:
  def __init__(self):
    self.selection = CameraSelection()
    self._camera = None
    self._completed = set()
    self._last_position = None
    self._fix_time = None
    self._behind_fixes = 0
    self._behind_anchor = None
    self._confirmed_approach = False
    self._travel_heading = None
    self._pending = None
    self._previous_projection = None
    self._crossed = False
    self.odometry = CameraOdometry()
    self._odometry_fix_time = None
    self._odometry_position = None
    self._junction_key = None
    self._junction_distance = None
    self._junction_anchored = False

  def update_odometry(self, sample, *, trusted_position, reaction_distance_m=300):
    selection = self.selection
    if self._junction_key is not None:
      if self._camera is None or selection.pass_proven or not self._confirmed_approach:
        return selection
      if not self._junction_anchored:
        if not trusted_position or selection.reason in ('localizer_unreliable', 'position_jump'):
          return selection
        if sample is None or not all(math.isfinite(v) for v in sample) or not 0 <= sample[1] <= MAX_PLAUSIBLE_SPEED_MPS:
          return self._uncertain('odometry_unavailable')
        self.odometry.anchor(selection.camera_id, self._junction_distance, sample)
        self._junction_anchored = True
        remaining = self.odometry.remaining if self.odometry.previous is not None else None
      else:
        remaining = self.odometry.advance(selection.camera_id, sample)
      if remaining is None:
        return self._uncertain('odometry_unavailable')
      if remaining <= 0:
        self._completed.add(selection.camera_id)
        self.selection = CameraSelection('PASSED', selection.camera_id, self._camera, 0., False, True, 'odometry_passed')
        self._junction_key = self._junction_distance = None
        self._junction_anchored = False
      else:
        state = 'ACTIVE' if selection.state == 'ACTIVE' or remaining <= reaction_distance_m else 'APPROACHING'
        localized = trusted_position and selection.reason not in ('localizer_unreliable', 'position_jump')
        reason = 'junction_approach' if localized else 'odometry_tracking'
        self.selection = CameraSelection(state, selection.camera_id, self._camera, remaining, True, False, reason)
      return self.selection
    # A new filter publication can repeat the same position. Keep spending
    # measured CAN distance rather than resetting the anchor to that position.
    same_anchor = self.odometry.identity == selection.camera_id and self._odometry_fix_time is not None and (
      self._odometry_fix_time == self._fix_time or self._odometry_position == self._last_position)
    if trusted_position and selection.valid and selection.reason == 'offline_approach' and not same_anchor:
      self.odometry.anchor(selection.camera_id,selection.distance_m,sample)
      self._odometry_fix_time = self._fix_time
      self._odometry_position = self._last_position
      return selection
    if trusted_position and not same_anchor and selection.reason not in ('approach_corridor_lost','road_not_matched'):
      return selection
    if self._camera is None or selection.pass_proven or not self._confirmed_approach:
      return selection
    remaining = self.odometry.advance(selection.camera_id,sample)
    if remaining is None:
      return selection
    if remaining <= 0:
      self._completed.add(selection.camera_id)
      self.selection = CameraSelection('PASSED',selection.camera_id,self._camera,0.,False,True,'odometry_passed')
    else:
      state = 'ACTIVE' if selection.state == 'ACTIVE' or remaining <= reaction_distance_m else 'APPROACHING'
      self.selection = CameraSelection(state,selection.camera_id,self._camera,remaining,True,False,'odometry_tracking')
    return self.selection

  def _uncertain(self, reason):
    self.selection = CameraSelection(state=self.selection.state, camera_id=self.selection.camera_id,
                                     camera=self._camera, reason=reason)
    self._behind_fixes = 0
    self._behind_anchor = None
    self._pending = None
    self._previous_projection = None
    return self.selection

  def update(self, cameras, latitude, longitude, heading, *, gps_valid,
             fix_time_s=None, now_s=None, reaction_distance_m=300, driver_override_id='', road_graph=None,
             vehicle_tolerance_m=12., vehicle_heading_tolerance_deg=35.):
    """Consume one localized position sample.

    The legacy gps_valid/fix_time_s argument names remain for replay callers;
    mapd supplies native LLK validity and monotonic publication time. A sample
    timestamp identifies a filter state, not a new satellite observation.
    """
    vehicle_tolerance_m = min(30.,max(12.,vehicle_tolerance_m))
    vehicle_heading_tolerance_deg = min(45.,max(35.,vehicle_heading_tolerance_deg))
    road_options = ({'vehicle_tolerance_m':vehicle_tolerance_m,
                     'vehicle_heading_tolerance_deg':vehicle_heading_tolerance_deg}
                    if getattr(road_graph,'supports_vehicle_uncertainty',False) and
                    (vehicle_tolerance_m != 12. or vehicle_heading_tolerance_deg != 35.) else {})
    if reaction_distance_m not in range(50, 1001, 50):
      reaction_distance_m = 300
    source_fresh = getattr(road_graph, 'camera_is_fresh', None)
    if self._camera is not None and callable(source_fresh):
      retained_id = camera_identity(self._camera)
      replacements = (cameras.query(self._camera.latitude, self._camera.longitude, 1.)
                      if isinstance(cameras, CameraSpatialIndex) else cameras)
      for replacement in replacements:
        if camera_identity(replacement) == retained_id and replacement.allowed_auto_control and source_fresh(replacement):
          # Adopt an actual fresh source object without resetting measured
          # approach/pass progress. Re-reading an old object cannot renew TTL.
          self._camera = replacement
          break
    if self._camera is not None and callable(source_fresh) and not source_fresh(self._camera):
      # Expired provider evidence cannot be prolonged by a retained CAN anchor.
      # Expiry is not passage evidence and never marks the report completed.
      self._camera = self._travel_heading = None
      self._pending = self._previous_projection = None
      self._junction_key = self._junction_distance = None
      self._junction_anchored = self._confirmed_approach = False
      self._crossed = False
      self._behind_fixes = 0
      self._behind_anchor = None
      self.odometry = CameraOdometry()
      self._odometry_fix_time = self._odometry_position = None
      self.selection = CameraSelection(reason='camera_source_expired')
    point = (latitude, longitude)
    reliable = (gps_valid and all(isinstance(v, (float, int)) and math.isfinite(v) for v in (*point, heading, fix_time_s, now_s))
                and -90 <= latitude <= 90 and -180 <= longitude <= 180 and 0 <= heading <= 360 and
                0 <= now_s - fix_time_s <= MAX_POSITION_AGE_S)
    if reliable and self._fix_time is not None and fix_time_s < self._fix_time:
      reliable = False
    if not reliable:
      self._last_position = None
      return self._uncertain('localizer_unreliable')
    if self._fix_time == fix_time_s:
      return self.selection
    previous_time, previous_point = self._fix_time, self._last_position
    self._fix_time = fix_time_s
    self._last_position = point
    movement = math.hypot(*xy(point, previous_point)) if previous_point is not None else 0.
    if previous_point is not None and movement > min(100., 60 * (fix_time_s - previous_time) + 5):
      return self._uncertain('position_jump')
    if self.selection.state == 'PASSED':
      self._camera = None
      self._junction_key = self._junction_distance = None
      self._junction_anchored = False
      self._pending = self._previous_projection = None
      self._crossed = False
      self._confirmed_approach = False
      self._behind_fixes = 0
      self._behind_anchor = None
    if self._camera is not None and self._junction_key is not None:
      if not self._junction_anchored:
        # An unpublished event cannot survive a turn or an extended CAN gap
        # and later acquire from an obsolete approach distance.
        junction_approach = getattr(road_graph, 'junction_camera_approach', None)
        approach = (junction_approach(point, heading, (self._camera.latitude, self._camera.longitude),
                                      vehicle_tolerance_m=vehicle_tolerance_m,
                                      vehicle_heading_tolerance_deg=vehicle_heading_tolerance_deg)
                    if callable(junction_approach) else None)
        if approach is None or approach[1] != self._junction_key or not 0 <= approach[0] <= LOOKAHEAD_M:
          self._camera = None
          self._junction_key = self._junction_distance = None
          self._junction_anchored = False
          self._confirmed_approach = False
          self._travel_heading = None
          self.selection = CameraSelection(reason='no_camera')
          return self._uncertain('no_camera')
        self._junction_distance = approach[0]
      # Once anchored, only CAN spends the frozen distance. A connected
      # branch or a new map position is never passage evidence.
      return self._uncertain('odometry_unavailable')
    if self._camera is not None:
      camera = self._camera
      identity = camera_identity(camera)
      if road_graph is None:
        measured = camera_projection(camera, point, heading, self._travel_heading)
      else:
        per_camera = getattr(road_graph, 'camera_distance_for_camera', None)
        distance = (per_camera(camera, point, heading, self._travel_heading, **road_options) if callable(per_camera) else
                    road_graph.camera_distance(point, heading, (camera.latitude, camera.longitude), self._travel_heading,**road_options))
        if distance is None:
          per_camera_passed = getattr(road_graph, 'passed_distance_for_camera', None)
          if callable(per_camera_passed):
            behind = per_camera_passed(camera, point, heading, self._travel_heading, **road_options)
          elif hasattr(road_graph, 'passed_distance'):
            behind = road_graph.passed_distance(point, heading, (camera.latitude, camera.longitude), self._travel_heading,**road_options)
          else:
            behind = road_graph.camera_distance((camera.latitude, camera.longitude), self._travel_heading, point, heading)
          distance = -behind if behind is not None and behind > 0 else None
        measured = None if distance is None else (distance, 0.)
      if measured is None:
        return self._uncertain('approach_corridor_lost')
      distance, lateral = measured
      if self._previous_projection is not None:
        old_distance, old_lateral = self._previous_projection
        if old_distance >= 0 and distance < 0 and movement >= .1:
          # The measured forward segment actually crosses the camera's line.
          fraction = old_distance / (old_distance - distance)
          crossing_lateral = old_lateral + fraction * (lateral - old_lateral)
          self._crossed |= abs(crossing_lateral) <= LATERAL_CORRIDOR_M
      self._previous_projection = measured
      if distance < -PASS_DISTANCE_M:
        # A GPS/map gap can hide the exact zero crossing. After recovery, the
        # retained, previously confirmed approach plus an authoritative unique
        # forward route from camera to vehicle is independent passage evidence.
        # Missing/ambiguous geometry never establishes this evidence. Two fresh
        # behind fixes must also demonstrate actual forward progress; timestamps
        # alone while standing still cannot retire an event.
        route_recovered = road_graph is not None and self._confirmed_approach
        if self._crossed or route_recovered:
          if self._behind_anchor is None:
            self._behind_anchor = (point, distance)
            self._behind_fixes = 1
          else:
            anchor_point, anchor_distance = self._behind_anchor
            if (anchor_distance - distance >= .1 and
                math.hypot(*xy(point, anchor_point)) >= .1):
              self._behind_fixes += 1
              self._behind_anchor = (point, distance)
          if self._behind_fixes >= 2:
            self._completed.add(identity)
            self.selection = CameraSelection('PASSED', identity, camera, distance, False, True, 'gps_passed')
            return self.selection
        else:
          self._behind_fixes = 0
          self._behind_anchor = None
        self.selection = CameraSelection(self.selection.state, identity, camera, reason='awaiting_second_pass_fix')
        return self.selection
      self._behind_fixes = 0
      self._behind_anchor = None
      if distance < 0:
        self.selection = CameraSelection(self.selection.state, identity, camera, reason='awaiting_pass_distance')
        return self.selection
      # The deprecated driver_override_id argument is accepted for old replay
      # callers only. Physical ACC input cannot latch an override on this event.
      state = 'ACTIVE' if distance <= reaction_distance_m or self.selection.state == 'ACTIVE' else 'APPROACHING'
      self.selection = CameraSelection(state, identity, camera, distance, True, False, 'offline_approach')
      return self.selection
    iterable = cameras.query(latitude, longitude, LOOKAHEAD_M) if isinstance(cameras, CameraSpatialIndex) else cameras
    candidates = []
    for camera in iterable:
      if not camera.allowed_auto_control:
        continue
      identity = camera_identity(camera)
      if identity in self._completed:
        continue
      strict_available = False
      for direction in travel_directions(camera, heading, filter_heading=road_graph is None):
        if road_graph is None:
          measured = camera_projection(camera, point, heading, direction)
        else:
          per_camera = getattr(road_graph, 'camera_distance_for_camera', None)
          distance = (per_camera(camera, point, heading, direction, **road_options) if callable(per_camera) else
                      road_graph.camera_distance(point, heading, (camera.latitude, camera.longitude), direction,**road_options))
          measured = None if distance is None else (distance, 0.)
        strict_available |= measured is not None
        if measured is not None and 0 <= measured[0] <= LOOKAHEAD_M:
          candidates.append((measured[0], identity, camera, direction, measured, None))
      junction_approach = getattr(road_graph, 'junction_camera_approach', None)
      if not strict_available and callable(junction_approach):
        approximate = junction_approach(point, heading, (camera.latitude, camera.longitude),
                                        vehicle_tolerance_m=vehicle_tolerance_m,
                                        vehicle_heading_tolerance_deg=vehicle_heading_tolerance_deg)
        if approximate is not None:
          distance, junction_key = approximate
          if 0 <= distance <= LOOKAHEAD_M:
            candidates.append((distance, identity, camera, heading, (distance, 0.), junction_key))
    if not candidates:
      self._pending = None
      associate = getattr(road_graph, 'associate', None)
      association_options = ({'tolerance':vehicle_tolerance_m,'heading_tolerance':vehicle_heading_tolerance_deg}
                             if road_options else {})
      reason = 'road_not_matched' if callable(associate) and associate(point, heading,**association_options) is None else 'no_camera'
      self.selection = CameraSelection(reason=reason)
      return self.selection
    distance, identity, camera, direction, measured, junction_key = min(candidates, key=lambda item: (item[0], item[1]))
    pending = self._pending
    if pending is not None and pending[0] == identity and pending[4] == junction_key and angle_difference(pending[1], direction) <= HEADING_TOLERANCE_DEG:
      _, _, first_point, first_distance, _ = pending
      displacement = xy(point, first_point)
      forward_motion, lateral_motion = projection(displacement, heading)
      if (math.hypot(*displacement) >= APPROACH_MOVEMENT_M and first_distance - distance >= APPROACH_PROGRESS_M and
          forward_motion >= APPROACH_MOVEMENT_M and abs(lateral_motion) <= LATERAL_CORRIDOR_M):
        self._camera, self._travel_heading = camera, direction
        self._pending = None
        self._crossed = False
        self._confirmed_approach = True
        self._behind_anchor = None
        self._previous_projection = measured
        self._junction_key = junction_key
        self._junction_distance = distance if junction_key is not None else None
        self._junction_anchored = False
        self.selection = CameraSelection('ACTIVE' if distance <= reaction_distance_m else 'APPROACHING',
                                         identity, camera, distance, junction_key is None, False,
                                         'offline_approach' if junction_key is None else 'odometry_unavailable')
        return self.selection
    if (pending is None or pending[0] != identity or pending[4] != junction_key or
        angle_difference(pending[1], direction) > HEADING_TOLERANCE_DEG or distance > pending[3] + 3):
      self._pending = (identity, direction, point, distance, junction_key)
    self.selection = CameraSelection(reason='confirming_approach')
    return self.selection
