"""Read-only directed Waze roads from the editor's public Practice mode.

The current WME app's normal Practice flow adds sandbox=true to Features reads.
This is an anonymous documented mode, not a saved edit or authenticated session.
Unit evidence: official SDK Segments.updateSegment defines limits in km/h;
current WME v2.378 maps SDK fwdSpeedLimit/revSpeedLimit directly to raw
fwdMaxSpeed/revMaxSpeed in both directions without conversion.
https://www.waze.com/editor/sdk/classes/index.SDK.Segments.html#updateSegment
https://www.waze.com/editor/sdk/interfaces/index.SDK.Segment.html
https://web-assets.waze.com/webapps/wme/v2.378-6-g016d6916f4-20261104114319-production/js/app-2fea95d06440e3ec.js.gz
Independent implementation; no application implementation code copied.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from urllib.parse import urlencode

from .clock import utc_now_s
from .tiles import _atomic_write, region_for, validate_query
from .transport import request

MAX_BYTES = 8 * 1024 * 1024
MAX_SEGMENTS = 20000
MAX_WINDOWS = 64
MAX_CACHE_FILES = 256
MAX_CACHE_BYTES = 32 * 1024 * 1024
CACHE_TTL_MS = 5 * 60 * 1000
CACHE_REFRESH_MARGIN_MS = 2 * 60 * 1000
CELL_DEGREES = .02
ROAD_TYPES = "1,2,3,4,5,6,7,8,9,10,15,16,17,18,19,20,22"
SOURCE = "waze_practice_features"


@dataclass(frozen=True)
class WazeRoadSegment:
  segment_id: int
  from_node_id: int
  to_node_id: int
  geometry: tuple[tuple[float, float], ...]  # GeoJSON lon/lat, from -> to
  forward_allowed: bool
  reverse_allowed: bool
  forward_limit_kph: float | None
  reverse_limit_kph: float | None
  observed_at_ms: int
  expires_at_ms: int
  bbox: tuple[float, float, float, float]
  source_url: str
  usable_for_control: bool
  restriction_reason: str = ""
  source: str = SOURCE
  speed_unit: str = "km/h"
  primary_street_id: int | None = None

  def to_dict(self) -> dict:
    return asdict(self)


@dataclass(frozen=True)
class WazeCameraLink:
  global_id: int
  segment_id: int
  coordinates: tuple[float, float]  # GeoJSON longitude, latitude
  direction: str
  types: tuple[str, ...]
  observed_at_ms: int
  expires_at_ms: int
  bbox: tuple[float, float, float, float]
  source_url: str
  usable_for_control: bool
  restriction_reason: str = ""
  source: str = SOURCE

  def to_dict(self) -> dict:
    return {**asdict(self), "longitude": self.coordinates[0], "latitude": self.coordinates[1]}


@dataclass(frozen=True)
class RoadResult:
  segments: tuple[WazeRoadSegment, ...]
  bbox: tuple[float, float, float, float]
  requested_windows: int
  loaded_windows: int
  observed_at_ms: int
  expires_at_ms: int
  complete: bool = True  # Request-window coverage, never worldwide inventory completeness.
  unknown_count: int = 0
  source: str = SOURCE
  camera_links: tuple[WazeCameraLink, ...] = ()


def _finite(value) -> bool:
  try:
    return type(value) in (int, float) and math.isfinite(value)
  except OverflowError:
    return False


def _id(value) -> int:
  if type(value) is not int or value <= 0:
    raise ValueError("Invalid Waze road identifier")
  return value


def _bounds(bbox):
  if len(bbox) != 4 or not all(_finite(v) for v in bbox):
    raise ValueError("Invalid Waze road bbox")
  west, south, east, north = bbox
  if not -180 <= west < east <= 180 or not -85 <= south < north <= 85:
    raise ValueError("Unsupported Waze road bbox")
  return tuple(float(v) for v in bbox)


def features_url(bbox, region="row") -> str:
  bbox = _bounds(bbox)
  prefix = {"row": "row-", "na": "", "il": "il-"}.get(region)
  if prefix is None:
    raise ValueError("Unknown Waze region")
  return "https://www.waze.com/" + prefix + "Descartes/app/Features?" + urlencode({
    "bbox": ",".join(f"{v:.6f}" for v in bbox), "language": "en", "v": 2,
    "roadTypes": ROAD_TYPES, "sandbox": "true", "cameras": "true", "permanentHazards": "true"})


def _unique_object(pairs):
  result = {}
  for key, value in pairs:
    if key in result:
      raise ValueError("Duplicate Waze road JSON field")
    result[key] = value
  return result


def parse_features(body: bytes, *, bbox, observed_at_ms: int, source_url: str = "") -> tuple[WazeRoadSegment, ...]:
  bbox = _bounds(bbox)
  if type(observed_at_ms) is not int or observed_at_ms <= 0 or not isinstance(body, bytes) or len(body) > MAX_BYTES:
    raise ValueError("Invalid Waze road response bounds")
  payload = json.loads(body, object_pairs_hook=_unique_object,
                       parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("Nonfinite Waze JSON")))
  if not isinstance(payload, dict) or "errorList" in payload or "error" in payload:
    raise ValueError("Waze road endpoint returned an error")
  segment_data = payload.get("segments")
  if not isinstance(segment_data, dict) or not isinstance(segment_data.get("objects"), list):
    raise ValueError("Missing Waze segment array")
  objects = segment_data["objects"]
  if len(objects) > MAX_SEGMENTS:
    raise ValueError("Too many Waze road segments")
  result = {}
  for item in objects:
    if not isinstance(item, dict):
      raise ValueError("Invalid Waze segment")
    identifier = _id(item.get("id"))
    geometry = item.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
      raise ValueError("Invalid Waze road geometry")
    raw_points = geometry.get("coordinates")
    if not isinstance(raw_points, list) or not 2 <= len(raw_points) <= 10000:
      raise ValueError("Invalid Waze road coordinate count")
    points = []
    for point in raw_points:
      if (not isinstance(point, list) or len(point) != 2 or not all(_finite(v) for v in point) or
          not -180 <= point[0] <= 180 or not -90 <= point[1] <= 90):
        raise ValueError("Invalid Waze road coordinate")
      points.append(tuple(float(v) for v in point))
    if not any(point != points[0] for point in points[1:]):
      raise ValueError("Degenerate Waze road geometry")
    if type(item.get("fwdDirection")) is not bool or type(item.get("revDirection")) is not bool:
      raise ValueError("Missing Waze road directions")
    limits = []
    for field in ("fwdMaxSpeed", "revMaxSpeed"):
      value = item.get(field)
      if value is None or _finite(value) and value == 0:
        limits.append(None)  # Missing/unlimited is never a numeric control target.
      elif not _finite(value) or not 0 < value <= 300:
        raise ValueError("Invalid Waze posted limit")
      else:
        limits.append(float(value))
    restricted = bool(item.get("restrictions") or item.get("parkingRestrictions"))
    conditional = any(value and "speed" in key.lower() and any(term in key.lower() for term in ("condition", "schedule", "time"))
                      for key, value in item.items())
    reason = "conditional_or_restricted" if restricted or conditional else ""
    allowed = item["fwdDirection"] or item["revDirection"]
    usable = allowed and not reason and ((item["fwdDirection"] and limits[0] is not None) or
                                        (item["revDirection"] and limits[1] is not None))
    street_id = item.get("primaryStreetID")
    if street_id is not None:
      street_id = _id(street_id)
    segment = WazeRoadSegment(identifier, _id(item.get("fromNodeID")), _id(item.get("toNodeID")), tuple(points),
                              item["fwdDirection"], item["revDirection"], limits[0], limits[1], observed_at_ms,
                              observed_at_ms + CACHE_TTL_MS, bbox, source_url, bool(usable), reason, primary_street_id=street_id)
    if identifier in result and segment != result[identifier]:
      raise ValueError("Conflicting Waze segment identifiers")
    result[identifier] = segment
  return tuple(result.values())


def parse_camera_links(body: bytes, *, bbox, observed_at_ms: int, source_url: str = "") -> tuple[WazeCameraLink, ...]:
  """Read explicit server associations; camera speedLimit is deliberately unused.

  Current public WME returns raw type 10, subTypes SPEED/RED_LIGHT and an actual
  segmentId. Older payloads can omit permanentHazards entirely. Other hazard
  classes never become camera links. Unassociated cameras cannot supply a link.
  """
  bbox = _bounds(bbox)
  if type(observed_at_ms) is not int or observed_at_ms <= 0 or not isinstance(body, bytes) or len(body) > MAX_BYTES:
    raise ValueError("Invalid Waze camera-link response bounds")
  payload = json.loads(body, object_pairs_hook=_unique_object,
                       parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("Nonfinite Waze JSON")))
  if not isinstance(payload, dict) or "errorList" in payload or "error" in payload:
    raise ValueError("Waze camera-link endpoint returned an error")
  if "permanentHazards" not in payload:
    return ()
  hazards = payload["permanentHazards"]
  if not isinstance(hazards, dict) or not isinstance(hazards.get("objects"), list) or len(hazards["objects"]) > MAX_SEGMENTS:
    raise ValueError("Invalid Waze permanent-hazard array")
  links = {}
  for item in hazards["objects"]:
    if not isinstance(item, dict):
      raise ValueError("Invalid Waze permanent hazard")
    if type(item.get("type")) is not int or item["type"] != 10:
      continue
    kinds = item.get("subTypes")
    if not isinstance(kinds, list) or not all(isinstance(kind, str) for kind in kinds):
      raise ValueError("Invalid Waze camera subtypes")
    if not {"SPEED", "RED_LIGHT"}.intersection(kinds) or item.get("segmentId") is None:
      continue
    direction = item.get("direction")
    if direction not in ("FWD", "REV", "BOTH"):
      continue  # No independently established legal approach association.
    geometry = item.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
      raise ValueError("Invalid Waze camera-link geometry")
    point = geometry.get("coordinates")
    if (not isinstance(point, list) or len(point) != 2 or not all(_finite(v) for v in point) or
        not -180 <= point[0] <= 180 or not -90 <= point[1] <= 90):
      raise ValueError("Invalid Waze camera-link coordinates")
    reason = "scheduled_or_excluded" if item.get("scheduleId") or item.get("excludedRoadTypes") else ""
    link = WazeCameraLink(_id(item.get("id")), _id(item.get("segmentId")), tuple(float(v) for v in point),
                          direction, tuple(sorted(set(kinds))), observed_at_ms, observed_at_ms+CACHE_TTL_MS,
                          bbox, source_url, not bool(reason), reason)
    if link.global_id in links and links[link.global_id] != link:
      raise ValueError("Conflicting Waze camera-link identifier")
    links[link.global_id] = link
  return tuple(links.values())


class WazeRoadClient:
  def __init__(self, cache_dir, transport=request, clock=utc_now_s, monotonic=time.monotonic):
    self.cache_dir, self.transport = Path(cache_dir), transport
    self.clock, self.monotonic = clock, monotonic
    self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

  def fetch(self, lat: float, lon: float, radius_m: float = 1000, timeout_s: float = 90) -> RoadResult:
    validate_query(lat, lon, radius_m)
    if abs(lat) >= 85 or not _finite(timeout_s) or timeout_s <= 0:
      raise ValueError("Invalid Waze road acquisition bounds")
    dy = radius_m / 110574.0
    dx = radius_m / (111320.0 * math.cos(math.radians(lat)))
    requested = _bounds((lon-dx, lat-dy, lon+dx, lat+dy))
    west, south, east, north = requested
    xs = range(math.floor(west/CELL_DEGREES), math.floor(east/CELL_DEGREES)+1)
    ys = range(math.floor(south/CELL_DEGREES), math.floor(north/CELL_DEGREES)+1)
    if len(xs)*len(ys) > MAX_WINDOWS:
      raise ValueError("Waze road request exceeds window budget")
    deadline, now_ms = self.monotonic()+timeout_s, round(self.clock()*1000)
    segments, camera_links, observations, loaded = {}, {}, [], 0
    for x in xs:
      for y in ys:
        bbox = _bounds((x*CELL_DEGREES, y*CELL_DEGREES, (x+1)*CELL_DEGREES, (y+1)*CELL_DEGREES))
        url = features_url(bbox, region_for(lat, lon))
        path = self.cache_dir / (hashlib.sha256(url.encode()).hexdigest()+".roads")
        body, observed = None, now_ms
        try:
          stat = path.stat()
          stamp = round(stat.st_mtime*1000)
          if not path.is_symlink() and stat.st_size <= MAX_BYTES and 0 <= now_ms-stamp < CACHE_TTL_MS-CACHE_REFRESH_MARGIN_MS:
            body, observed = path.read_bytes(), stamp
            records = parse_features(body, bbox=bbox, observed_at_ms=observed, source_url=url)
            links = parse_camera_links(body, bbox=bbox, observed_at_ms=observed, source_url=url)
        except (OSError, ValueError, TypeError):
          body = None
        if body is None:
          remaining = deadline-self.monotonic()
          if remaining <= 0:
            raise TimeoutError("Waze road acquisition deadline exceeded")
          body = self.transport(url, headers={"Accept": "application/json"}, timeout_s=remaining, max_bytes=MAX_BYTES)
          observed = round(self.clock()*1000)
          records = parse_features(body, bbox=bbox, observed_at_ms=observed, source_url=url)
          links = parse_camera_links(body, bbox=bbox, observed_at_ms=observed, source_url=url)
          _atomic_write(path, body, observed)
        observations.append(observed)
        loaded += 1
        for segment in records:
          previous = segments.get(segment.segment_id)
          if previous is not None and any(getattr(previous, field) != getattr(segment, field) for field in (
              "from_node_id", "to_node_id", "geometry", "forward_allowed", "reverse_allowed",
              "forward_limit_kph", "reverse_limit_kph", "usable_for_control", "restriction_reason", "primary_street_id")):
            raise ValueError("Conflicting Waze segment across request windows")
          if previous is None or segment.observed_at_ms > previous.observed_at_ms:
            segments[segment.segment_id] = segment
        if len(segments) > MAX_SEGMENTS:
          raise ValueError("Combined Waze road segment budget exceeded")
        for link in links:
          previous = camera_links.get(link.global_id)
          if previous is not None and any(getattr(previous, field) != getattr(link, field) for field in (
              "segment_id", "coordinates", "direction", "types", "usable_for_control", "restriction_reason")):
            raise ValueError("Conflicting Waze camera link across request windows")
          if previous is None or link.observed_at_ms > previous.observed_at_ms:
            camera_links[link.global_id] = link
        if len(camera_links) > MAX_SEGMENTS:
          raise ValueError("Combined Waze camera-link budget exceeded")
    retained_bytes = 0
    for index, path in enumerate(sorted(self.cache_dir.glob("*.roads"), key=lambda p: p.stat().st_mtime, reverse=True)):
      if not path.is_symlink():
        retained_bytes += path.stat().st_size
        if index >= MAX_CACHE_FILES or retained_bytes > MAX_CACHE_BYTES:
          path.unlink(missing_ok=True)
    oldest = min(observations)
    # Only a completely fetched window union can establish the candidate search
    # neighborhood near cell borders. Retain per-record source URL/observation,
    # but expire the union when its oldest constituent window expires.
    complete = loaded == len(xs)*len(ys)
    covered = tuple(replace(segment, bbox=requested, expires_at_ms=min(segment.expires_at_ms, oldest+CACHE_TTL_MS))
                    for segment in segments.values()) if complete else tuple(segments.values())
    covered_links = tuple(replace(link, bbox=requested, expires_at_ms=min(link.expires_at_ms, oldest+CACHE_TTL_MS))
                          for link in camera_links.values()) if complete else ()
    return RoadResult(covered, requested, len(xs)*len(ys), loaded, oldest, oldest+CACHE_TTL_MS,
                      complete=complete, camera_links=covered_links)
