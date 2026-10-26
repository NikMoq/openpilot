"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from abc import abstractmethod, ABC

import cereal.messaging as messaging
from openpilot.common.params import Params
from openpilot.common.constants import CV
from openpilot.selfdrive.car.cruise import V_CRUISE_UNSET
from openpilot.sunnypilot.navd.helpers import coordinate_from_param

MAX_SPEED_LIMIT = V_CRUISE_UNSET * CV.KPH_TO_MS


class BaseMapData(ABC):
  def __init__(self):
    self.params = Params()

    self.sm = messaging.SubMaster(['liveLocationKalman'])
    self.pm = messaging.PubMaster(['liveMapDataSP'])

    self.localizer_valid = False
    self.last_bearing = None
    self.last_position = coordinate_from_param("LastGPSPositionLLK", self.params)

  @abstractmethod
  def update_location(self) -> None:
    pass

  @abstractmethod
  def get_current_speed_limit(self) -> float:
    pass

  @abstractmethod
  def get_next_speed_limit_and_distance(self) -> tuple[float, float]:
    pass

  @abstractmethod
  def get_current_road_name(self) -> str:
    pass

  def get_speed_camera(self):
    return None

  def get_speed_camera_control(self):
    return None

  def publish(self) -> None:
    speed_limit = self.get_current_speed_limit()
    camera = self.get_speed_camera()
    control_camera = self.get_speed_camera_control()
    next_speed_limit, next_speed_limit_distance = self.get_next_speed_limit_and_distance()

    mapd_sp_send = messaging.new_message('liveMapDataSP')
    mapd_sp_send.valid = bool(self.localizer_valid and self.sm['liveLocationKalman'].gpsOK)
    live_map_data = mapd_sp_send.liveMapDataSP
    live_map_data.locationValid = mapd_sp_send.valid

    live_map_data.speedLimitValid = bool(MAX_SPEED_LIMIT > speed_limit > 0)
    live_map_data.speedLimit = speed_limit
    live_map_data.speedLimitAheadValid = bool(MAX_SPEED_LIMIT > next_speed_limit > 0)
    live_map_data.speedLimitAhead = next_speed_limit
    live_map_data.speedLimitAheadDistance = next_speed_limit_distance
    live_map_data.roadName = self.get_current_road_name()
    # Always mark the new contract, including an explicitly empty selection.
    live_map_data.speedCameraControlVersion = 1

    if camera is not None:
      live_map_data.speedCameraAheadValid = True
      live_map_data.speedCameraAheadDistance = camera.distance_m
      live_map_data.speedCameraSpeedLimit = (camera.camera.speed_limit_kph or 0.) / 3.6
      live_map_data.speedCameraType = camera.camera.camera_type
      live_map_data.speedCameraLatitude = camera.camera.latitude
      live_map_data.speedCameraLongitude = camera.camera.longitude
      live_map_data.speedCameraAutoControlAllowed = camera.camera.allowed_auto_control

    if control_camera is not None:
      live_map_data.speedCameraControlValid = True
      live_map_data.speedCameraControlDistance = control_camera.distance_m
      live_map_data.speedCameraControlSpeedLimit = control_camera.camera.speed_limit_kph / 3.6
      live_map_data.speedCameraControlType = control_camera.camera.camera_type
      live_map_data.speedCameraControlLatitude = control_camera.camera.latitude
      live_map_data.speedCameraControlLongitude = control_camera.camera.longitude
      live_map_data.speedCameraControlAutoControlAllowed = control_camera.camera.allowed_auto_control

    self.pm.send('liveMapDataSP', mapd_sp_send)

  def tick(self) -> None:
    self.sm.update(0)
    self.update_location()
    self.publish()
