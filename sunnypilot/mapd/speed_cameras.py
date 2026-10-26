"""Local speed-camera database parsing and nearest-camera lookup.

This module deliberately contains no network client and no vehicle-control side
effects.  A producer may fetch a licensed JSON/GeoJSON snapshot and pass it to
``SpeedCameraDatabase``; the resolver then returns only cameras ahead of the
vehicle and within the configured look-ahead distance.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Iterable


EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True)
class SpeedCamera:
  latitude: float
  longitude: float
  speed_limit_kph: float | None = None
  direction_deg: float | None = None
  camera_type: str = "unknown"
  direction_type: int | None = None
  end_latitude: float | None = None
  end_longitude: float | None = None


@dataclass(frozen=True)
class CameraMatch:
  camera: SpeedCamera
  distance_m: float
  bearing_deg: float


def _number(value: Any) -> float | None:
  try:
    return float(value) if value is not None and value != "" else None
  except (TypeError, ValueError):
    return None


def _camera_from_mapping(item: dict[str, Any]) -> SpeedCamera | None:
  coordinates = item.get("coordinates")
  if isinstance(coordinates, (list, tuple)) and len(coordinates) >= 2:
    longitude, latitude = coordinates[:2]
  else:
    latitude = item.get("latitude", item.get("lat"))
    longitude = item.get("longitude", item.get("lon", item.get("lng")))

  latitude = _number(latitude)
  longitude = _number(longitude)
  if latitude is None or longitude is None or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
    return None

  speed_limit = item.get("speed_limit_kph", item.get("speedLimit", item.get("speed")))
  if speed_limit is None:
    controls = item.get("speedControls")
    if isinstance(controls, list):
      current = next((control for control in controls
                      if isinstance(control, dict) and control.get("type") == "current"), None)
      if current is None:
        current = next((control for control in controls if isinstance(control, dict)), None)
      if isinstance(current, dict):
        speed_limit = current.get("speed")
  direction = item.get("direction_deg", item.get("direction", item.get("bearing", item.get("dir"))))
  direction_type = item.get("direction_type", item.get("dirType"))
  return SpeedCamera(
    latitude=latitude,
    longitude=longitude,
    speed_limit_kph=_number(speed_limit),
    direction_deg=_number(direction),
    direction_type=int(direction_type) if str(direction_type).strip().lstrip("-").isdigit() else None,
    camera_type=str(item.get("camera_type", item.get("type", "unknown"))),
    end_latitude=_number(item.get("end_latitude")),
    end_longitude=_number(item.get("end_longitude")),
  )


def parse_camera_snapshot(payload: str | bytes | dict[str, Any] | list[Any]) -> list[SpeedCamera]:
  """Parse a simple list or GeoJSON FeatureCollection into normalized cameras."""
  if isinstance(payload, (str, bytes)):
    payload = json.loads(payload)

  if isinstance(payload, dict) and payload.get("type") == "FeatureCollection":
    items: Iterable[Any] = []
    features = payload.get("features", [])
    normalized = []
    for feature in features:
      if not isinstance(feature, dict):
        continue
      geometry = feature.get("geometry") or {}
      properties = feature.get("properties") or {}
      coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
      item = dict(properties) if isinstance(properties, dict) else {}
      item["coordinates"] = coordinates
      normalized.append(item)
    items = normalized
  elif isinstance(payload, dict):
    items = payload.get("objects", payload.get("cameras", payload.get("items", [])))
  else:
    items = payload

  return [camera for item in items if isinstance(item, dict)
          for camera in [_camera_from_mapping(item)] if camera is not None]


def _distance_and_bearing(latitude: float, longitude: float, camera: SpeedCamera) -> tuple[float, float]:
  lat1, lat2 = math.radians(latitude), math.radians(camera.latitude)
  dlat = lat2 - lat1
  dlon = math.radians(camera.longitude - longitude)
  a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
  distance = 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))
  y = math.sin(dlon) * math.cos(lat2)
  x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
  bearing = (math.degrees(math.atan2(y, x)) + 360) % 360
  return distance, bearing


def find_nearest_camera(cameras: Iterable[SpeedCamera], latitude: float, longitude: float,
                        heading_deg: float | None = None, max_distance_m: float = 5_000,
                        direction_tolerance_deg: float = 60) -> CameraMatch | None:
  """Return the closest camera ahead, or ``None`` when no match is reliable."""
  matches: list[CameraMatch] = []
  for camera in cameras:
    distance, bearing = _distance_and_bearing(latitude, longitude, camera)
    if distance > max_distance_m:
      continue
    if heading_deg is not None:
      delta = abs((bearing - heading_deg + 180) % 360 - 180)
      if delta > 90:
        continue
      if camera.direction_deg is not None and camera.direction_type in (1, 2):
        camera_delta = abs((heading_deg - camera.direction_deg + 180) % 360 - 180)
        if camera.direction_type == 1 and camera_delta > direction_tolerance_deg:
          continue
        if camera.direction_type == 2 and min(camera_delta, abs(180 - camera_delta)) > direction_tolerance_deg:
          continue
    matches.append(CameraMatch(camera, distance, bearing))
  return min(matches, key=lambda match: match.distance_m, default=None)
