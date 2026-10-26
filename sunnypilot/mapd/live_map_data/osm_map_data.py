"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import json
import math
import os
import platform

from cereal import log
from openpilot.common.params import Params
from openpilot.system.hardware.hw import Paths
from openpilot.sunnypilot.mapd.camera_database import load_database
from openpilot.sunnypilot.mapd.live_map_data.base_map_data import BaseMapData
from openpilot.sunnypilot.mapd.speed_cameras import find_nearest_camera
from openpilot.sunnypilot.navd.helpers import Coordinate


class OsmMapData(BaseMapData):
  def __init__(self):
    super().__init__()
    self.mem_params = Params("/dev/shm/params") if platform.system() != "Darwin" else self.params
    configured_path = self.params.get("SpeedCameraDatabasePath", return_default=True)
    self.camera_database_path = configured_path or os.path.join(Paths.mapd_root(), "speed_cameras.json")
    self.camera_database = None
    self.camera_database_mtime = None

  def _load_camera_database(self) -> None:
    try:
      mtime = os.path.getmtime(self.camera_database_path)
    except OSError:
      self.camera_database = None
      self.camera_database_mtime = None
      return
    if mtime != self.camera_database_mtime:
      self.camera_database = load_database(self.camera_database_path, max_age_s=7 * 24 * 3600)
      self.camera_database_mtime = mtime

  def update_location(self) -> None:
    location = self.sm['liveLocationKalman']
    self.localizer_valid = (location.status == log.LiveLocationKalman.Status.valid) and location.positionGeodetic.valid

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
    self._load_camera_database()
    if self.camera_database is not None and self.last_position is not None:
      camera_match = find_nearest_camera(
        self.camera_database.cameras,
        self.last_position.latitude,
        self.last_position.longitude,
        heading_deg=self.last_bearing,
        max_distance_m=5_000,
      )
      if camera_match is not None and camera_match.camera.speed_limit_kph is not None:
        return camera_match.camera.speed_limit_kph / 3.6, camera_match.distance_m

    next_speed_limit_section_str = self.mem_params.get("NextMapSpeedLimit")
    next_speed_limit_section = next_speed_limit_section_str if next_speed_limit_section_str else {}
    next_speed_limit = next_speed_limit_section.get('speedlimit', 0.0)
    next_speed_limit_latitude = next_speed_limit_section.get('latitude')
    next_speed_limit_longitude = next_speed_limit_section.get('longitude')
    next_speed_limit_distance = 0.0

    if next_speed_limit_latitude and next_speed_limit_longitude:
      next_speed_limit_coordinates = Coordinate(next_speed_limit_latitude, next_speed_limit_longitude)
      next_speed_limit_distance = (self.last_position or Coordinate(0, 0)).distance_to(next_speed_limit_coordinates)

    return next_speed_limit, next_speed_limit_distance
