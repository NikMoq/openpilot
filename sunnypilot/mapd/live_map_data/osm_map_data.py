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
import xml.etree.ElementTree as ET

from cereal import log
from openpilot.common.params import Params
from openpilot.system.hardware.hw import Paths
from openpilot.sunnypilot.mapd.camera_database import load_database
from openpilot.sunnypilot.mapd.camera_road_graph import RoadGraph
from openpilot.sunnypilot.mapd.camera_speed_source import CameraSpeedSource, camera_gps_reliable
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
    self.camera_road_graph = None
    self.camera_road_graph_stamp = None
    self.camera_enabled = True
    self.camera_warning_enabled = True
    self.camera_alert_distance_m = 1_000.0

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
      distance = float(self.camera_params.get("SpeedCameraAlertDistanceMeters") or 1_000)
      self.camera_alert_distance_m = min(max(distance, 100.0), 5_000.0) if math.isfinite(distance) else 1_000.0
    except (TypeError, ValueError):
      self.camera_alert_distance_m = 1_000.0

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
    if self.camera_database is not None and time.time() - self.camera_database.fetched_at > 7 * 24 * 3600:  # noqa: TID251 - persisted Unix time
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
        # Warning distance is a display preference, not the control lookahead.
        # A short warning preference must not postpone the ICBM speed target.
        max_distance_m=max(self.camera_alert_distance_m,
                           5_000.0 if self.camera_params.get("SpeedCameraControlEnabled") == "1" else 0.0),
      ) if self.camera_warning_enabled else None
      if self.camera_params.get("SpeedCameraControlEnabled") == "1":
        self._update_camera_source()
    if self.camera_params.get("SpeedCameraControlEnabled") != "1":
      self.camera_speed_source = CameraSpeedSource()
      self.camera_selection = self.camera_speed_source.selection
    elif not self.localizer_valid or self.camera_database is None or self.last_position is None:
      self.camera_selection = self.camera_speed_source.update((), None, 0., 0., 0., gps_valid=False)
    return self.camera_match

  def _update_camera_source(self):
    path = self.camera_params.get("SpeedCameraRoadGraphPath") or os.path.join(Paths.mapd_root(), "camera_roads.osm")
    status = 'ready'
    try:
      stamp = (path, os.path.getmtime(path), os.path.getsize(path), os.path.getmtime(path + '.manifest.json'))
      if stamp != self.camera_road_graph_stamp:
        self.camera_road_graph = RoadGraph.load(path)
        self.camera_road_graph_stamp = stamp
      if not 0 <= time.time() - self.camera_road_graph.acquired_at <= 30 * 86400:
        raise ValueError('road_graph_stale')
    except (OSError, ValueError, KeyError, ET.ParseError) as error:
      self.camera_road_graph = None
      self.camera_road_graph_stamp = None
      status = 'missing' if isinstance(error, FileNotFoundError) else str(error)
    try:
      reaction = int(self.camera_params.get('SpeedCameraReactionDistance') or 300)
    except (TypeError, ValueError):
      reaction = 300
    gps = self.sm[self.camera_gps_service]
    now = time.time()
    gps_valid = bool(self.localizer_valid and self.sm['liveLocationKalman'].gpsOK and
                     self.sm.valid[self.camera_gps_service] and self.sm.alive[self.camera_gps_service] and
                     camera_gps_reliable(gps, now))
    # Camera topology uses one consistent raw GPS position/course/time tuple.
    # Existing LLK coordinates and map/navigation behavior remain unchanged.
    self.camera_selection = self.camera_speed_source.update(
      self.camera_index, self.camera_road_graph, gps.latitude, gps.longitude, gps.bearingDeg,
      gps_valid=gps_valid, fix_time_s=gps.unixTimestampMillis * .001, now_s=now,
      reaction_distance_m=reaction)
    self.camera_params.put('SpeedCameraRoadGraphStatus', status if status != 'ready' else self.camera_selection.reason)
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
