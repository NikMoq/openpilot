"""Local speed-camera database parsing and nearest-camera lookup.

This module deliberately contains no network client and no vehicle-control side
effects.  A producer may fetch a licensed JSON/GeoJSON snapshot and pass it to
``SpeedCameraDatabase``; the resolver then returns only cameras ahead of the
vehicle and within the configured look-ahead distance.
"""

from __future__ import annotations

import csv
import io
import json
import math
from dataclasses import dataclass, field
from collections.abc import Iterable
from typing import Any


EARTH_RADIUS_M = 6_371_000.0
IGO_HEADER = ("IDX", "X", "Y", "TYPE", "SPEED", "DIRTYPE", "DIRECTION")
IGO_AUTO_CONTROL_TYPES = frozenset({"1", "2", "5"})
CITYGUIDE_POINT_TYPES = frozenset({"18059", "18950", "18958"})


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
  second_direction_deg: float | None = None
  source_id: str | None = None
  source_params: dict[str, str] = field(default_factory=dict)

  @property
  def allowed_auto_control(self) -> bool:
    """Import eligibility under the selected provider policy, not road validation.

    CG point bearings use the HIGH CONFIDENCE inference documented in
    cityguide.py. Require the raw metadata as well as the normalized fields,
    so an old cache or a type label alone cannot enable automatic control.
    """
    speed = _number(self.speed_limit_kph)
    if speed is None or not 0 < speed <= 300:
      return False
    if self.camera_type.startswith("speedcamonline:"):
      return self.camera_type.removeprefix("speedcamonline:") in IGO_AUTO_CONTROL_TYPES
    if not self.camera_type.startswith("speedcamonline-cg77:"):
      return False
    kind = self.camera_type.removeprefix("speedcamonline-cg77:")
    directions = cityguide_point_directions(kind, self.source_params)
    params = _cityguide_parameters(self.source_params)
    return (directions[0] is not None and self.direction_type == 1 and
            self.direction_deg == directions[0] and self.second_direction_deg == directions[1] and
            params is not None and _number(params.get(1709)) == speed and
            self.end_latitude is None and self.end_longitude is None)


@dataclass(frozen=True)
class CameraMatch:
  camera: SpeedCamera
  distance_m: float
  bearing_deg: float


class CameraSpatialIndex:
  """Conservative unit-sphere grid; exact lookup policy remains in the resolver.

  A spherical cap of radius r fits inside the Cartesian box with half-width
  2*sin(r/(2*R)) around its centre. This avoids longitude seams and polar
  singularities. Buckets only remove impossible candidates; original indices
  restore iteration order (including duplicates and equal-distance ties).
  """

  _CELL_SIZE = 0.001  # About 6.4 km on the unit sphere.
  _PADDING = 1e-12  # Conservative allowance for floating-point trig/rounding.

  def __init__(self, cameras: Iterable[SpeedCamera]):
    self._cameras = tuple(cameras)
    self._cells: dict[tuple[int, int, int], list[int]] = {}
    self._unindexed: list[int] = []
    for index, camera in enumerate(self._cameras):
      if not self._valid_position(camera.latitude, camera.longitude):
        # Preserve legacy handling of malformed caller-supplied records.
        self._unindexed.append(index)
        continue
      cell = tuple(math.floor(value / self._CELL_SIZE) for value in self._point(camera.latitude, camera.longitude))
      self._cells.setdefault(cell, []).append(index)

  def __iter__(self):
    return iter(self._cameras)

  @staticmethod
  def _valid_position(latitude: float, longitude: float) -> bool:
    try:
      return math.isfinite(latitude) and math.isfinite(longitude) and -90 <= latitude <= 90 and -180 <= longitude <= 180
    except TypeError:
      return False

  @staticmethod
  def _point(latitude: float, longitude: float) -> tuple[float, float, float]:
    lat, lon = math.radians(latitude), math.radians(longitude)
    cos_lat = math.cos(lat)
    return cos_lat * math.cos(lon), cos_lat * math.sin(lon), math.sin(lat)

  def query(self, latitude: float, longitude: float, radius_m: float) -> Iterable[SpeedCamera]:
    # Fall back to the original iterable for invalid/unbounded queries rather
    # than assigning new semantics to NaN, negative radii or invalid positions.
    try:
      bounded = math.isfinite(radius_m) and 0 <= radius_m < math.pi * EARTH_RADIUS_M
    except TypeError:
      bounded = False
    if not bounded or not self._valid_position(latitude, longitude):
      return iter(self._cameras)
    half_width = 2 * math.sin(radius_m / (2 * EARTH_RADIUS_M)) + self._PADDING
    bounds = [(math.floor((value - half_width) / self._CELL_SIZE),
               math.floor((value + half_width) / self._CELL_SIZE)) for value in self._point(latitude, longitude)]
    if math.prod(high - low + 1 for low, high in bounds) > 4096:
      return iter(self._cameras)  # Bound work for unusually large query radii.
    indices = list(self._unindexed)
    for x in range(bounds[0][0], bounds[0][1] + 1):
      for y in range(bounds[1][0], bounds[1][1] + 1):
        for z in range(bounds[2][0], bounds[2][1] + 1):
          indices.extend(self._cells.get((x, y, z), ()))
    return (self._cameras[index] for index in sorted(indices))


def _number(value: Any) -> float | None:
  try:
    number = float(value) if value is not None and value != "" else None
    return number if number is not None and math.isfinite(number) else None
  except (TypeError, ValueError):
    return None


def _cityguide_parameters(raw: dict[str, str]) -> dict[int, str] | None:
  """Validate preserved key/value metadata without modifying original tokens."""
  if not isinstance(raw, dict):
    return None
  parameters = {}
  for key, value in raw.items():
    if not isinstance(key, str) or not isinstance(value, str):
      return None
    token = key.strip()
    if not token or not token.isascii() or not token.isdigit():
      return None
    try:
      number = int(token)
    except ValueError:
      return None
    if number <= 0 or number in parameters:
      return None
    parameters[number] = value.strip()
  return parameters


def cityguide_point_directions(kind: str, raw: dict[str, str]) -> tuple[float | None, float | None]:
  """HIGH CONFIDENCE point-only azimuth+180 inference; beam is not DIRTYPE.

  The second explicit azimuth follows the documented first/second symmetry;
  no two-azimuth records occurred in the 290 matched export observations.
  Ranges are validated/preserved, not interpreted as route geometry.
  """
  parameters = _cityguide_parameters(raw)
  if (kind not in CITYGUIDE_POINT_TYPES or parameters is None or
      parameters.get(1713) not in ("0", "1", "2")):
    return None, None
  first = _number(parameters.get(1705))
  if first is None or not 0 <= first <= 360:
    return None, None
  second = None
  if 1707 in parameters:
    azimuth = _number(parameters[1707])
    if azimuth is None or not 0 <= azimuth <= 360:
      return None, None
    second = (azimuth + 180) % 360
  for key in (1706, 1708):
    if key in parameters:
      distance = _number(parameters[key])
      if distance is None or distance < 0:
        return None, None
  return (first + 180) % 360, second


def _decode_text(payload: str | bytes) -> str:
  if isinstance(payload, str):
    return payload.lstrip("\ufeff")
  if payload.startswith(b"\xef\xbb\xbf"):
    return payload.decode("utf-8-sig")
  try:
    return payload.decode("utf-8")
  except UnicodeDecodeError:
    return payload.decode("cp1251")


def _is_igo_header(line: str) -> bool:
  return tuple(part.strip().upper() for part in line.split(",")) == IGO_HEADER


def is_igo_snapshot(payload: str | bytes) -> bool:
  text = _decode_text(payload)
  first_line = next((line for line in text.splitlines() if line.strip()), "")
  return _is_igo_header(first_line)


def _stable_type(value: Any) -> str:
  number = _number(value)
  if number is not None and number.is_integer():
    return str(int(number))
  value = str(value).strip()
  return value or "unknown"


def parse_igo_snapshot(payload: str | bytes, strict: bool = False) -> list[SpeedCamera]:
  """Parse SpeedCamOnline iGo CSV: X=longitude, Y=latitude."""
  text = _decode_text(payload)
  if strict and not text.endswith(("\n", "\r")):
    raise ValueError("iGo snapshot is truncated: missing line terminator")
  lines = [line for line in text.splitlines() if line.strip()]
  if not lines or not _is_igo_header(lines[0]):
    raise ValueError("invalid iGo speed-camera header")
  reader = csv.reader(io.StringIO(text, newline=""), strict=True)
  try:
    header = next(reader)
  except (StopIteration, csv.Error):
    raise ValueError("invalid iGo speed-camera schema") from None
  if tuple((name or "").strip().upper() for name in header) != IGO_HEADER:
    raise ValueError("invalid iGo speed-camera schema")

  cameras: list[SpeedCamera] = []
  try:
    rows = list(reader)
  except csv.Error:
    if strict:
      raise ValueError("invalid iGo CSV quoting") from None
    return cameras
  for row_number, values in enumerate(rows, start=2):
    try:
      if len(values) != len(IGO_HEADER):
        raise ValueError("invalid column count")
      row = dict(zip(IGO_HEADER, values, strict=True))
      longitude = _number(row.get("X"))
      latitude = _number(row.get("Y"))
      direction = _number(row.get("DIRECTION"))
      direction_type = int(row["DIRTYPE"].strip())
      speed = _number(row.get("SPEED"))
      if (longitude is None or latitude is None or not -180 <= longitude <= 180 or
          not -90 <= latitude <= 90 or direction is None or not 0 <= direction <= 360 or
          direction_type not in (0, 1, 2) or speed is None or not 0 <= speed <= 300):
        raise ValueError("invalid coordinate/direction")
      cameras.append(SpeedCamera(
        latitude=latitude,
        longitude=longitude,
        speed_limit_kph=None if speed is None or speed <= 0 else speed,
        direction_deg=direction % 360,
        direction_type=direction_type,
        camera_type=f"speedcamonline:{_stable_type(row.get('TYPE'))}",
      ))
    except (AttributeError, KeyError, TypeError, ValueError):
      if strict:
        raise ValueError(f"invalid iGo row {row_number}") from None
  return cameras


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
  raw_params = item.get("source_params", {})
  source_params = dict(raw_params) if isinstance(raw_params, dict) else {}
  return SpeedCamera(
    latitude=latitude,
    longitude=longitude,
    speed_limit_kph=_number(speed_limit),
    direction_deg=_number(direction),
    direction_type=int(direction_type) if str(direction_type).strip().lstrip("-").isdigit() else None,
    camera_type=str(item.get("camera_type", item.get("type", "unknown"))),
    end_latitude=_number(item.get("end_latitude")),
    end_longitude=_number(item.get("end_longitude")),
    second_direction_deg=_number(item.get("second_direction_deg")),
    source_id=item.get("source_id") if isinstance(item.get("source_id"), str) else None,
    source_params=source_params,
  )


def parse_camera_snapshot(payload: str | bytes | dict[str, Any] | list[Any]) -> list[SpeedCamera]:
  """Parse a simple list or GeoJSON FeatureCollection into normalized cameras."""
  if isinstance(payload, (str, bytes)):
    if _decode_text(payload).lstrip().startswith("2|Radars|"):
      # Imported lazily: the strict CityGuide reader shares SpeedCamera.
      from openpilot.sunnypilot.mapd.cityguide import parse_cityguide_snapshot
      return parse_cityguide_snapshot(payload)
    if is_igo_snapshot(payload):
      return parse_igo_snapshot(payload)
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
  if isinstance(cameras, CameraSpatialIndex):
    cameras = cameras.query(latitude, longitude, max_distance_m)
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
        directions = [camera.direction_deg]
        if camera.second_direction_deg is not None:
          directions.append(camera.second_direction_deg)
        if camera.direction_type == 2:
          directions += [(direction + 180) % 360 for direction in tuple(directions)]
        camera_delta = min(abs((heading_deg - direction + 180) % 360 - 180) for direction in directions)
        if camera_delta > direction_tolerance_deg:
          continue
    matches.append(CameraMatch(camera, distance, bearing))
  return min(matches, key=lambda match: match.distance_m, default=None)


def find_nearest_control_camera(cameras: Iterable[SpeedCamera], latitude: float, longitude: float,
                                heading_deg: float | None, minimum_speed_kph: float,
                                max_distance_m: float = 5_000) -> CameraMatch | None:
  """Select independently of warnings, using raw eligibility before any offset."""
  if heading_deg is None or not math.isfinite(heading_deg) or not math.isfinite(minimum_speed_kph) or minimum_speed_kph <= 0:
    return None
  if isinstance(cameras, CameraSpatialIndex):
    cameras = cameras.query(latitude, longitude, max_distance_m)
  candidates = (camera for camera in cameras if camera.allowed_auto_control and camera.speed_limit_kph >= minimum_speed_kph)
  return find_nearest_camera(candidates, latitude, longitude, heading_deg, max_distance_m)
