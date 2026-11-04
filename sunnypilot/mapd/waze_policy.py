"""Waze event import policy; observation freshness and control evidence stay separate.

RT alerts supply no posted speed limit. A separately matched Waze road segment
can supply that limit independently of the report's facing direction. Tile speed
bytes remain raw until their own semantics are independently established.
"""
from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping

from openpilot.sunnypilot.mapd.speed_cameras import CameraSpatialIndex, SpeedCamera, _distance_and_bearing

MOBILE_TTL_S = 180.0
FIXED_TTL_S = 12 * 3600.0
_FIXED_TYPES = {"SPEED_CAMERA": "speed_camera", "DUMMY_CAMERA": "dummy_camera", "RED_LIGHT_CAMERA": "red_light_camera"}
ROAD_LIMIT_TTL_MS = 300_000


def _number(value):
  try:
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None
  except OverflowError:
    return None


def _fresh(event: Mapping, now: float) -> bool:
  if _number(now) is None or event.get("source") != "waze" or event.get("source_kind") not in ("fixed", "mobile"):
    return False
  observed, expires = (_number(event.get(key)) for key in ("observed_at_ms", "expires_at_ms"))
  if observed is None or expires is None:
    return False
  ttl = MOBILE_TTL_S if event["source_kind"] == "mobile" else FIXED_TTL_S
  if not 0 < observed <= now * 1000 < min(expires, observed + ttl * 1000):
    return False
  published = event.get("published_at_ms")
  # Old reports can still be active in the current delta stream. Never replace
  # source time with observation time, or discard solely because publication is old.
  return published is None or (_number(published) is not None and 0 <= published <= now * 1000)


def _event(camera: SpeedCamera) -> dict | None:
  if not isinstance(camera.source_params, Mapping):
    return None
  try:
    event = json.loads(camera.source_params.get("waze_event", "null"))
  except (TypeError, ValueError):
    return None
  return event if isinstance(event, dict) else None


def _type(event: Mapping) -> str:
  if event.get("source_kind") == "fixed":
    return "waze:fixed:" + _FIXED_TYPES.get(event.get("raw_subtype"), "unknown")
  return "waze:mobile:" + str(event.get("raw_subtype") or event.get("raw_type") or "UNKNOWN")


def _control_location(event):
  latitude, longitude = (_number(event.get(key)) for key in ("latitude", "longitude"))
  if latitude is None or longitude is None or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
    return None
  basis = event.get("road_match_basis")
  if basis not in ("mobile_parallel_carriageway", "mobile_parallel_carriageway_primary"):
    if "control_latitude" in event or "control_longitude" in event:
      return None
    return latitude, longitude
  if event.get("source_kind") != "mobile" or type(event.get("road_primary_street_id")) is not int or event["road_primary_street_id"] <= 0:
    return None
  target_lat, target_lon = (_number(event.get(key)) for key in ("control_latitude", "control_longitude"))
  if target_lat is None or target_lon is None or not -90 <= target_lat <= 90 or not -180 <= target_lon <= 180:
    return None
  actual = _distance_and_bearing(latitude, longitude, SpeedCamera(target_lat, target_lon))[0]
  reported = _number(event.get("road_report_distance_m"))
  if reported is None or actual > (12 if basis.endswith("_primary") else 40) or abs(actual - reported) > 0.2:
    return None
  return target_lat, target_lon


def _road_variants(event, now=None):
  if event.get("road_match_status") != "verified" or event.get("road_limit_source") != "waze_practice_features":
    return []
  if event.get("source_kind") == "fixed" and event.get("raw_subtype") not in ("SPEED_CAMERA", "RED_LIGHT_CAMERA"):
    return []
  if event.get("source_kind") == "mobile" and event.get("raw_type") not in ("POLICE", "CAMERA"):
    return []
  if any(type(event.get(key)) is not int or event[key] <= 0 for key in ("road_segment_id", "road_from_node_id", "road_to_node_id")):
    return []
  observed, expires = (_number(event.get(key)) for key in ("road_observed_at_ms", "road_limit_expires_at_ms"))
  if observed is None or expires is None or not 0 < observed < expires <= observed + ROAD_LIMIT_TTL_MS:
    return []
  if now is not None and not observed <= now * 1000 < expires:
    return []
  corridor = event.get("road_match_basis") in ("mobile_parallel_carriageway", "mobile_parallel_carriageway_primary")
  distance = _number(event.get("road_match_distance_m"))
  if distance is None or not 0 <= distance <= (40 if corridor else 12):
    return []
  geometry = event.get("road_geometry")
  if not isinstance(geometry, (list, tuple)) or not 2 <= len(geometry) <= 10000:
    return []
  for point in geometry:
    if (not isinstance(point, (list, tuple)) or len(point) != 2 or any(_number(v) is None for v in point) or
        not -180 <= point[0] <= 180 or not -90 <= point[1] <= 90):
      return []
  target = _control_location(event)
  if target is None:
    return []
  if corridor:
    from openpilot.sunnypilot.mapd.waze.road_match import _projections
    projections = _projections(geometry, target[1], target[0])
    if not projections or projections[0][0] > 0.5:
      return []
  explicit_camera = event.get("road_match_basis") == "explicit_camera_segment"
  if explicit_camera:
    if event.get("source_kind") != "fixed" or type(event.get("road_camera_global_id")) is not int or event["road_camera_global_id"] <= 0:
      return []
    if event.get("road_camera_segment_id") != event["road_segment_id"] or event.get("road_camera_direction") not in ("FWD", "REV", "BOTH"):
      return []
    kinds, point = event.get("road_camera_types"), event.get("road_camera_coordinates")
    expected_kind = "SPEED" if event.get("raw_subtype") == "SPEED_CAMERA" else "RED_LIGHT"
    if not isinstance(kinds, (list, tuple)) or expected_kind not in kinds:
      return []
    if (not isinstance(point, (list, tuple)) or len(point) != 2 or any(_number(v) is None for v in point) or
        not -180 <= point[0] <= 180 or not -90 <= point[1] <= 90):
      return []
    lat, lon = _number(event.get("latitude")), _number(event.get("longitude"))
    if lat is None or lon is None or _distance_and_bearing(lat, lon, SpeedCamera(point[1], point[0]))[0] > 2:
      return []
    link_observed, link_expires = (_number(event.get(key)) for key in ("road_camera_observed_at_ms", "road_camera_expires_at_ms"))
    if link_observed is None or link_expires is None or not 0 < link_observed < link_expires <= link_observed + ROAD_LIMIT_TTL_MS:
      return []
    if expires > link_expires or now is not None and not link_observed <= now * 1000 < link_expires:
      return []
  variants = event.get("road_limit_variants")
  if not isinstance(variants, list) or not 1 <= len(variants) <= 2:
    return []
  directions, result = set(), []
  for variant in variants:
    if not isinstance(variant, dict):
      return []
    direction, speed, heading = variant.get("travel_direction"), _number(variant.get("limit_kph")), _number(variant.get("heading_deg"))
    if direction not in ("both", "forward", "reverse") or direction in directions or speed is None or not 0 < speed <= 300:
      return []
    if direction != "both" and (heading is None or not 0 <= heading < 360):
      return []
    if direction == "both" and (len(variants) != 1 or variant.get("heading_deg") is not None):
      return []
    if variant.get("direction_basis") not in (
        "only_legal_direction", "equal_limits_both_directions", "per_direction_road_limit", "explicit_camera_direction"):
      return []
    if variant.get("direction_basis") == "explicit_camera_direction" and not explicit_camera:
      return []
    directions.add(direction)
    result.append(variant)
  coverage = event.get("road_direction_coverage")
  if (coverage == "both" and directions not in ({"both"}, {"forward", "reverse"})) or (
      coverage != "both" and directions != {coverage}):
    return []
  if corridor:
    if len(result) != 1 or "both" in directions:
      return []
    expected_heading = (projections[0][2] + (180 if result[0]["travel_direction"] == "reverse" else 0)) % 360
    if abs((result[0]["heading_deg"] - expected_heading + 180) % 360 - 180) > 1:
      return []
  if explicit_camera and event["road_camera_direction"] != "BOTH" and directions != {
      "forward" if event["road_camera_direction"] == "FWD" else "reverse"}:
    return []
  return result


def events_to_cameras(events: Iterable[dict], now: float) -> list[SpeedCamera]:
  cameras = {}
  for event in events:
    if not isinstance(event, dict) or not _fresh(event, now):
      continue
    # Other road events remain in the raw snapshot. Do not present closures,
    # accidents or congestion as speed cameras in the existing camera UI.
    if event["source_kind"] == "mobile" and event.get("raw_type") not in ("POLICE", "CAMERA"):
      continue
    latitude, longitude = (_number(event.get(key)) for key in ("latitude", "longitude"))
    identity = event.get("source_id")
    if latitude is None or longitude is None or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
      continue
    if not isinstance(identity, str) or not identity.startswith("waze:") or len(identity) > 512:
      continue
    try:
      raw = json.dumps(event, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
      continue
    params = {key: value if isinstance(value, str) else json.dumps(value, allow_nan=False, separators=(",", ":"))
              for key, value in event.items()}
    params["waze_event"] = raw
    variants = _road_variants(event, now)
    if variants:
      if event["source_kind"] == "mobile":
        # Keep the physical report visible from both approaches even when the
        # applicable road limits require distinct directional control entries.
        warning = SpeedCamera(latitude, longitude, None, None, _type(event), None, source_id=identity, source_params=params)
        previous = cameras.get(identity)
        if previous is None or event["observed_at_ms"] > _event(previous)["observed_at_ms"]:
          cameras[identity] = warning
      for variant in variants:
        suffix = f':road:{event["road_segment_id"]}:{variant["travel_direction"]}'
        variant_params = dict(params, waze_road_variant=json.dumps(variant, sort_keys=True, separators=(",", ":")))
        both = variant["travel_direction"] == "both"
        control_lat, control_lon = _control_location(event)
        camera = SpeedCamera(control_lat, control_lon, float(variant["limit_kph"]), None if both else float(variant["heading_deg"]),
                             _type(event), 0 if both else 1, source_id=identity + suffix, source_params=variant_params)
        previous = cameras.get(camera.source_id)
        if previous is None or event["observed_at_ms"] > _event(previous)["observed_at_ms"]:
          cameras[camera.source_id] = camera
      continue
    heading = _number(event.get("heading_raw"))
    approach = heading if (event["source_kind"] == "fixed" and event.get("direction_status") == "verified_approach" and
                           heading is not None and 0 <= heading < 360) else None
    speed = _number(event.get("speed_limit_kph"))
    if (event.get("source_kind") != "fixed" or event.get("speed_unit") != "km/h" or
        event.get("limit_status") != "verified" or event.get("limit_source") != "RoadMapAlert.speed"):
      speed = None
    camera = SpeedCamera(latitude, longitude, speed if speed is not None and 0 < speed <= 300 else None,
                         approach, _type(event), 1 if approach is not None else None,
                         source_id=identity, source_params=params)
    previous = cameras.get(identity)
    if previous is None or event["observed_at_ms"] > _event(previous)["observed_at_ms"]:
      cameras[identity] = camera
  return list(cameras.values())


def waze_control_evidence(camera: SpeedCamera) -> bool:
  """Structural evidence gate, deliberately independent of wall-clock functions.

  This is not itself a verification service: real backend events do not set the
  verification fields. Freshness must be rechecked at import/merge on every tick.
  Mobile RT needs separate matched road-limit evidence; its raw report cannot
  supply a control limit or restrict enforcement to the report's facing.
  """
  event = _event(camera)
  if event is None or event.get("source") != "waze":
    return False
  if "waze_road_variant" in camera.source_params:
    try:
      variant = json.loads(camera.source_params["waze_road_variant"])
    except (TypeError, ValueError):
      return False
    if variant not in _road_variants(event):
      return False
    expected_id = f'{event.get("source_id")}:road:{event["road_segment_id"]}:{variant["travel_direction"]}'
    both = variant["travel_direction"] == "both"
    return bool(camera.source_id == expected_id and camera.camera_type == _type(event) and
                _number(camera.speed_limit_kph) == _number(variant["limit_kph"]) and
                type(camera.direction_type) is int and camera.direction_type == (0 if both else 1) and
                camera.direction_deg == (None if both else _number(variant["heading_deg"])) and
                _control_location(event) == (camera.latitude, camera.longitude) and
                camera.end_latitude is None and camera.end_longitude is None and camera.second_direction_deg is None)
  if event.get("source_kind") != "fixed":
    return False
  if event.get("raw_type") != "CAMERA" or event.get("raw_subtype") not in ("SPEED_CAMERA", "RED_LIGHT_CAMERA"):
    return False
  expected_category = 2 if event["raw_subtype"] == "SPEED_CAMERA" else 4
  if type(event.get("category_raw")) is not int or event["category_raw"] != expected_category:
    return False
  if camera.camera_type != _type(event) or camera.source_id != event.get("source_id"):
    return False
  if not isinstance(camera.source_id, str) or not camera.source_id.startswith("waze:"):
    return False
  speed = _number(event.get("speed_limit_kph"))
  heading = _number(event.get("heading_raw"))
  if (event.get("speed_unit") != "km/h" or event.get("limit_status") != "verified" or
      event.get("limit_source") != "RoadMapAlert.speed" or event.get("direction_status") != "verified_approach"):
    return False
  return bool(speed is not None and 0 < speed <= 300 and speed == _number(camera.speed_limit_kph) == _number(event.get("speed_raw")) and
              heading is not None and 0 <= heading < 360 and type(camera.direction_type) is int and camera.direction_type == 1 and
              _number(camera.direction_deg) == heading and
              _number(event.get("latitude")) == camera.latitude and _number(event.get("longitude")) == camera.longitude and
              camera.end_latitude is None and camera.end_longitude is None and camera.second_direction_deg is None)


def filter_fresh_waze(cameras: Iterable[SpeedCamera], now: float) -> list[SpeedCamera]:
  return [camera for camera in cameras if camera.camera_type.startswith("waze:") and
          (event := _event(camera)) is not None and _fresh(event, now) and
          ("waze_road_variant" not in camera.source_params or _road_variants(event, now))]


def merge_camera_sources(russian: Iterable[SpeedCamera], waze: Iterable[SpeedCamera], now: float,
                         *, russian_index: CameraSpatialIndex | None = None) -> list[SpeedCamera]:
  """Keep the Russian authority; do not coalesce opposite verified approaches.

  Nearby unknown-limit Waze warnings must not replace a qualified Russian camera.
  Separate Waze IDs remain separate, including mobile reports and opposite lanes.
  """
  primary = list(russian)
  additions = filter_fresh_waze(waze, now)
  if not additions:
    return primary
  index = russian_index if russian_index is not None else CameraSpatialIndex(primary)
  identities = {camera.source_id for camera in primary if camera.source_id}
  for camera in additions:
    if camera.source_id in identities:
      continue
    hidden_by_primary = False
    if not waze_control_evidence(camera):
      for original in index.query(camera.latitude, camera.longitude, 50):
        if not original.allowed_auto_control or _distance_and_bearing(camera.latitude, camera.longitude, original)[0] > 50:
          continue
        opposite = (camera.direction_deg is not None and original.direction_deg is not None and
                    abs((camera.direction_deg - original.direction_deg + 180) % 360 - 180) > 90)
        if not opposite:
          hidden_by_primary = True
          break
    if not hidden_by_primary:
      primary.append(camera)
      identities.add(camera.source_id)
  return primary
