"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import json
import math
import os
import platform
import time
from dataclasses import replace

from cereal import log
from openpilot.common.params import Params
from openpilot.system.hardware.hw import Paths
from openpilot.sunnypilot.mapd.camera_database import load_database
from openpilot.sunnypilot.mapd.camera_osm_bridge import OfflineOsmBridge
from openpilot.sunnypilot.mapd.camera_speed_source import CameraSpeedSource, camera_gps_reliable, camera_identity
from openpilot.sunnypilot.mapd.camera_odometry import CameraOdometry, car_speed_sample
from openpilot.sunnypilot.mapd.camera_gps_quality import MAX_HORIZONTAL_ACCURACY_M, MAX_FIX_AGE_S, MIN_MOVING_SPEED_MPS
from openpilot.sunnypilot.mapd.camera_updater import CameraParams
from openpilot.sunnypilot.mapd.live_map_data.base_map_data import BaseMapData
from openpilot.sunnypilot.mapd.speed_cameras import CameraSpatialIndex, find_nearest_camera
from openpilot.sunnypilot.navd.helpers import Coordinate


class OsmMapData(BaseMapData):
  def __init__(self):
    super().__init__()
    self.mem_params = Params("/dev/shm/params") if platform.system() != "Darwin" else self.params
    self.camera_params = CameraParams()
    configured_path = self.camera_params.get("SpeedCameraDatabasePath")
    self.camera_database_path = configured_path or os.path.join(Paths.mapd_root(), "speed_cameras.json")
    self.camera_database = None
    self.camera_database_mtime = None
    self.camera_index = None
    self.camera_match = None
    self.camera_control_match = None
    self.camera_speed_source = CameraSpeedSource()
    self.camera_selection = self.camera_speed_source.selection
    self.camera_graph_status = None
    self.camera_osm_bridge = OfflineOsmBridge(Paths.mapd_root())
    self.camera_enabled = True
    self.camera_warning_enabled = True
    self.camera_alert_distance_m = 300

  def _refresh_camera_config(self) -> None:
    configured_path = self.camera_params.get("SpeedCameraDatabasePath")
    configured_path = configured_path or os.path.join(Paths.mapd_root(), "speed_cameras.json")
    if configured_path != self.camera_database_path:
      self.camera_database_path = configured_path
      self.camera_database = None
      self.camera_database_mtime = None
      self.camera_index = None
    enabled = str(self.camera_params.get("SpeedCameraEnabled") or "1").strip().lower()
    self.camera_warning_enabled = enabled not in ("0", "false", "no", "off")
    self.camera_enabled = self.camera_warning_enabled or self.camera_params.get("SpeedCameraControlEnabled") == "1"
    try:
      distance = int(self.camera_params.get("SpeedCameraReactionDistance") or 300)
    except (TypeError, ValueError):
      distance = 300
    self.camera_alert_distance_m = distance if distance in range(50,1001,50) else 300

  def _load_camera_database(self) -> None:
    self._refresh_camera_config()
    try:
      mtime = os.path.getmtime(self.camera_database_path)
    except OSError:
      self.camera_database = None
      self.camera_database_mtime = None
      self.camera_index = None
      self.camera_match = None
      return
    if mtime != self.camera_database_mtime:
      self.camera_database = load_database(self.camera_database_path, max_age_s=7 * 24 * 3600)
      self.camera_database_mtime = mtime
      self.camera_index = None
    if self.camera_database is not None and self.camera_index is None:
      self.camera_index = CameraSpatialIndex(self.camera_database.cameras)

  def get_speed_camera(self):
    self._load_camera_database()
    self.camera_control_match = None
    expired = self.camera_database is not None and time.time() - self.camera_database.fetched_at > 7 * 24 * 3600
    if expired:
      self.camera_database = None
      self.camera_index = None
    if not self.localizer_valid or not self.camera_enabled or self.camera_database is None or self.last_position is None:
      self.camera_match = None
    else:
      self.camera_match = find_nearest_camera(
        self.camera_index,
        self.last_position.latitude,
        self.last_position.longitude,
        heading_deg=self.last_bearing,
        # Warning visibility shares the configured reaction zone; the separate
        # control source retains its independent5km lookahead.
        max_distance_m=self.camera_alert_distance_m,
      ) if self.camera_warning_enabled else None
    if self.camera_params.get("SpeedCameraControlEnabled") == "1":
      # Selection consumes only the local database and measured GPS trajectory.
      self._update_camera_source(database_reason='camera_database_stale' if expired else 'camera_database_missing')
    else:
      self.camera_speed_source = CameraSpeedSource()
      self.camera_selection = self.camera_speed_source.selection
      self._set_camera_graph_status('disabled')
    self._update_warning_odometry()
    return self.camera_match

  def _update_warning_odometry(self):
    self.camera_warning_tracking = False
    self.camera_warning_passed = False
    if not hasattr(self,'camera_warning_odometry'):
      self.camera_warning_odometry = CameraOdometry()
      self.camera_warning_retained = None
      self.camera_warning_completed = []
      self.camera_warning_fix_time = None
    if not self.camera_warning_enabled:
      self.camera_warning_retained = None
      self.camera_warning_odometry = CameraOdometry()
      self.camera_warning_fix_time = None
      return
    now = time.time()
    gps = self.sm[self.camera_gps_service]
    trusted = bool(self.localizer_valid and self.sm['liveLocationKalman'].gpsOK and
                   self.sm.valid[self.camera_gps_service] and self.sm.alive[self.camera_gps_service] and
                   camera_gps_reliable(gps,now))
    sample = car_speed_sample(self.sm,time.monotonic())
    same_fix = trusted and self.camera_warning_fix_time == gps.unixTimestampMillis
    if trusted and not same_fix and self.camera_match is not None:
      identity = camera_identity(self.camera_match.camera)
      if identity in self.camera_warning_completed:
        self.camera_match = None
      else:
        self.camera_warning_retained = self.camera_match
        self.camera_warning_odometry.anchor(identity,self.camera_match.distance_m,sample)
        self.camera_warning_fix_time = gps.unixTimestampMillis
    elif not trusted or same_fix:
      retained = self.camera_warning_retained
      # A still-qualified duplicate fix may retain its existing warning while
      # CAN is absent; it must never reset the distance integration anchor.
      if not trusted:
        self.camera_match = None
      if retained is not None:
        identity = camera_identity(retained.camera)
        remaining = self.camera_warning_odometry.advance(identity,sample)
        if remaining is not None:
          if remaining <= 0:
            self.camera_warning_completed = (self.camera_warning_completed+[identity])[-128:]
            self.camera_warning_retained = None
            self.camera_warning_passed = True
          else:
            self.camera_match = replace(retained,distance_m=remaining)
            self.camera_warning_tracking = True
    selection = self.camera_selection
    if selection.pass_proven and self.camera_match is not None and camera_identity(self.camera_match.camera) == selection.camera_id:
      self.camera_match = None
      self.camera_warning_retained = None

  def _set_camera_graph_status(self, status):
    # Params.put is an atomic file replacement; do not fsync unchanged status
    # every mapd tick, including while an offline worker is backing off.
    if status != getattr(self, 'camera_graph_status', None):
      self.camera_params.put('SpeedCameraRoadGraphStatus', status)
      self.camera_graph_status = status

  def _update_camera_source(self, database_reason='camera_database_missing'):
    reaction = self.camera_alert_distance_m
    gps = self.sm[self.camera_gps_service]
    now = time.time()
    raw_stream_valid = bool(self.sm.valid[self.camera_gps_service] and self.sm.alive[self.camera_gps_service])
    raw_gps_valid = raw_stream_valid and camera_gps_reliable(gps, now)
    # A strong stationary position is not a weak-GPS signal, but motion is
    # needed to confirm a camera approach and passage from measured trajectory.
    position_values = (gps.latitude, gps.longitude, gps.horizontalAccuracy, gps.unixTimestampMillis)
    position_valid = bool(raw_stream_valid and gps.hasFix and all(math.isfinite(value) for value in position_values) and
                          -90 <= gps.latitude <= 90 and -180 <= gps.longitude <= 180 and
                          0 < gps.horizontalAccuracy <= MAX_HORIZONTAL_ACCURACY_M and 0 <= now - gps.unixTimestampMillis * .001 <= MAX_FIX_AGE_S)
    gps_valid = bool(self.localizer_valid and self.sm['liveLocationKalman'].gpsOK and raw_gps_valid)
    if getattr(self, 'camera_osm_bridge', None) is None:
      self.camera_osm_bridge = OfflineOsmBridge(Paths.mapd_root())
    if position_valid:
      self.camera_osm_bridge.tick(gps.latitude, gps.longitude)
    roads = self.camera_osm_bridge.snapshot
    self.camera_selection = self.camera_speed_source.update(
      self.camera_index or (), gps.latitude, gps.longitude, gps.bearingDeg,
      gps_valid=gps_valid and self.camera_database is not None and roads is not None,
      fix_time_s=gps.unixTimestampMillis * .001, now_s=now,
      reaction_distance_m=reaction, road_graph=roads,
      vehicle_tolerance_m=gps.horizontalAccuracy,vehicle_heading_tolerance_deg=gps.bearingAccuracyDeg)
    self.camera_selection = self.camera_speed_source.update_odometry(
      car_speed_sample(self.sm,time.monotonic()),trusted_position=gps_valid,reaction_distance_m=reaction)
    if not self.camera_selection.valid and not self.camera_selection.pass_proven:
      if not gps_valid:
        # Stationary course is undefined, not a weak-GPS signal.
        localizer_ok = bool(self.localizer_valid and self.sm['liveLocationKalman'].gpsOK)
        stationary_position = bool(position_valid and math.isfinite(gps.speed) and 0 <= gps.speed < MIN_MOVING_SPEED_MPS)
        if not localizer_ok and (raw_gps_valid or stationary_position):
          reason = 'localizer_unreliable'
        else:
          reason = 'waiting_for_motion' if stationary_position and localizer_ok else 'gps_unreliable'
      elif self.camera_database is None:
        reason = database_reason
      elif roads is None:
        reason = self.camera_osm_bridge.status
        if reason == 'ready':
          reason = 'offline_maps_loading'  # Worker completed after this tick captured snapshot=None.
      else:
        reason = self.camera_selection.reason
      self.camera_selection = replace(self.camera_selection, reason=reason)
    # Read existing downloaded mapd tiles only. The bridge does not download
    # data or use posted road limits; the camera database supplies the limit.
    self._set_camera_graph_status('offline_osm:' + self.camera_osm_bridge.status + ':' + self.camera_selection.reason)
    if self.camera_selection.valid:
      from openpilot.sunnypilot.mapd.speed_cameras import CameraMatch
      self.camera_control_match = CameraMatch(self.camera_selection.camera, self.camera_selection.distance_m, 0.)

  def get_speed_camera_control(self):
    # BaseMapData.publish refreshes both selections once through get_speed_camera.
    return self.camera_control_match

  def update_location(self) -> None:
    location = self.sm['liveLocationKalman']
    self.localizer_valid = ((location.status == log.LiveLocationKalman.Status.valid) and location.positionGeodetic.valid
                            and location.calibratedOrientationNED.valid)
    self.localizer_valid = self.localizer_valid and self.sm.valid['liveLocationKalman'] and self.sm.alive['liveLocationKalman']

    if self.localizer_valid:
      self.last_bearing = math.degrees(location.calibratedOrientationNED.value[2])
      self.last_position = Coordinate(location.positionGeodetic.value[0], location.positionGeodetic.value[1])

    if self.last_position is None:
      return

    params = {
      "latitude": self.last_position.latitude,
      "longitude": self.last_position.longitude,
    }

    if self.last_bearing is not None:
      params['bearing'] = self.last_bearing

    self.mem_params.put("LastGPSPosition", json.dumps(params))

  def get_current_speed_limit(self) -> float:
    return float(self.mem_params.get("MapSpeedLimit") or 0.0)

  def get_current_road_name(self) -> str:
    return str(self.mem_params.get("RoadName") or "")

  def get_next_speed_limit_and_distance(self) -> tuple[float, float]:
    # Cameras are temporary targets on their own message fields. Feeding them
    # into road limits makes SLA retain the camera limit after passing it.
    next_speed_limit_section_str = self.mem_params.get("NextMapSpeedLimit")
    try:
      next_speed_limit_section = json.loads(next_speed_limit_section_str) if next_speed_limit_section_str else {}
    except (TypeError, ValueError, json.JSONDecodeError):
      next_speed_limit_section = {}
    if not isinstance(next_speed_limit_section, dict):
      next_speed_limit_section = {}
    next_speed_limit = next_speed_limit_section.get('speedlimit', 0.0)
    next_speed_limit_latitude = next_speed_limit_section.get('latitude')
    next_speed_limit_longitude = next_speed_limit_section.get('longitude')
    next_speed_limit_distance = 0.0

    if next_speed_limit_latitude and next_speed_limit_longitude:
      next_speed_limit_coordinates = Coordinate(next_speed_limit_latitude, next_speed_limit_longitude)
      next_speed_limit_distance = (self.last_position or Coordinate(0, 0)).distance_to(next_speed_limit_coordinates)

    return next_speed_limit, next_speed_limit_distance
