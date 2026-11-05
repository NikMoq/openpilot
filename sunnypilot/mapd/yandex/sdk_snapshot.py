"""Normalize caller-supplied Full MapKit snapshots into warning-only evidence.

Local expiry caps are import policy, not assertions about Yandex event lifetimes.
SDK formatter output is preserved as provenance; raw numeric units stay unknown.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping

SOURCE = "yandex_mapkit_full_sdk"
SDK_BUILD = "4.45.0-full"
MAX_POINTS = 100_000
MAX_EVENTS = 10_000
FIXED_TTL_MS = 300_000
MOBILE_TTL_MS = 180_000


def _number(value):
  try:
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None
  except OverflowError:
    return None


def _point(value):
  if not isinstance(value, (list, tuple)) or len(value) != 2:
    raise ValueError("Invalid SDK coordinate")
  lat, lon = map(_number, value)
  if lat is None or lon is None or not (-85 <= lat <= 85 and -180 <= lon <= 180):
    raise ValueError("Invalid SDK coordinate")
  return lat, lon


def _xy(point, origin):
  return (math.radians(point[1] - origin[1]) * 6371000 * math.cos(math.radians(origin[0])),
          math.radians(point[0] - origin[0]) * 6371000)


def _formatted_limit(raw, descriptor):
  """Accept only explicit, matching official formatter observations in km/h."""
  if _number(raw) is None or not isinstance(descriptor, Mapping):
    return None
  descriptor_raw, value = _number(descriptor.get("raw")), _number(descriptor.get("canonical_value"))
  if (descriptor.get("source") != "official_sdk_i18n_formatter" or descriptor.get("status") != "formatted"
      or descriptor.get("canonical_unit") != "kilometers per hour" or descriptor_raw is None
      or descriptor_raw != _number(raw) or value is None or not 0 < value <= 300):
    return None
  return value


def _format_evidence(descriptor):
  if not isinstance(descriptor, Mapping):
    return None
  for key in ("raw", "canonical_value"):
    if descriptor.get(key) is not None and _number(descriptor[key]) is None:
      raise ValueError("Invalid SDK formatter number")
  for key in ("source", "status", "canonical_unit"):
    value = descriptor.get(key)
    if value is not None and (not isinstance(value, str) or len(value) > 128):
      raise ValueError("Invalid SDK formatter field")
  # Whitelist evidence fields; diagnostic strings/error payloads are not imported.
  return {key: descriptor.get(key) for key in ("raw", "source", "status", "canonical_value", "canonical_unit")}


def normalize_snapshot(snapshot: Mapping, now_ms: int | float) -> list[dict]:
  """Return bounded warning records; malformed or conflicting input fails closed.

  No SpeedCamera/control eligibility is granted. Mobile enforcement warnings
  apply from both approaches; their road-limit evidence covers only the SDK
  route direction. A reverse-direction limit requires a separately proved route.
  """
  now = _number(now_ms)
  if not isinstance(snapshot, Mapping) or now is None or now <= 0:
    raise ValueError("Invalid SDK snapshot")
  if snapshot.get("schema_version") != 1 or type(snapshot.get("schema_version")) is not int:
    raise ValueError("Unsupported SDK snapshot schema")
  if snapshot.get("source") != SOURCE or snapshot.get("sdk_build") != SDK_BUILD:
    raise ValueError("Unverified SDK snapshot source")
  request_id = snapshot.get("request_id")
  if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id):
    raise ValueError("Invalid SDK request identity")
  if (snapshot.get("conditions_outdated") is not False or snapshot.get("conditions_update_seen") is not True
      or snapshot.get("conditions_update_requested") is not True or snapshot.get("conditions_update_status") != "observed"):
    raise ValueError("SDK route conditions not freshly observed")
  observed, completed = (_number(snapshot.get(key)) for key in ("observed_at_ms", "completed_at_ms"))
  if observed is None or completed is None or not 0 < observed <= completed <= now or completed - observed > 60_000:
    raise ValueError("Invalid SDK observation times")
  geometry, events, limits = (snapshot.get(key) for key in ("route_geometry_lat_lon", "events", "segment_speed_limits_raw"))
  if not isinstance(geometry, (list, tuple)) or not 2 <= len(geometry) <= MAX_POINTS:
    raise ValueError("Invalid SDK route geometry")
  if not isinstance(events, (list, tuple)) or len(events) > MAX_EVENTS:
    raise ValueError("Invalid SDK event budget")
  if not isinstance(limits, (list, tuple)) or len(limits) != len(geometry) - 1:
    raise ValueError("SDK segment limits do not match geometry")
  points = [_point(point) for point in geometry]
  for start, end in zip(points, points[1:], strict=False):
    if abs(start[1] - end[1]) > 180 or math.hypot(*_xy(end, start)) > 10_000:
      raise ValueError("Unsupported SDK route segment")
  if any(value is not None and (_number(value) is None or value < 0) for value in limits):
    raise ValueError("Invalid SDK raw segment limit")
  examples = snapshot.get("sdk_speed_format_examples", [])
  if not isinstance(examples, (list, tuple)) or len(examples) > 128:
    raise ValueError("Invalid SDK formatter budget")
  formats = {}
  for descriptor in examples:
    if not isinstance(descriptor, Mapping):
      raise ValueError("Invalid SDK formatter descriptor")
    raw = _number(descriptor.get("raw"))
    if raw is None:
      continue
    evidence = _format_evidence(descriptor)
    if raw in formats and formats[raw] != evidence:
      raise ValueError("Conflicting SDK formatter observations")
    formats[raw] = evidence
  result, identities = [], {}
  for event in events:
    if not isinstance(event, Mapping):
      raise ValueError("Invalid SDK event")
    identifier, tags = event.get("event_id"), event.get("tags")
    if not isinstance(identifier, str) or not 1 <= len(identifier) <= 256 or any(ord(c) < 32 for c in identifier):
      raise ValueError("Invalid SDK event identity")
    if not isinstance(tags, (list, tuple)) or len(tags) > 32:
      raise ValueError("Invalid SDK event tags")
    names = []
    for tag in tags:
      if not isinstance(tag, Mapping) or not isinstance(tag.get("name"), str) or not 1 <= len(tag["name"]) <= 64:
        raise ValueError("Invalid SDK event tag")
      if type(tag.get("sdk_ordinal")) is not int or not 0 <= tag["sdk_ordinal"] <= 255:
        raise ValueError("Invalid SDK tag ordinal")
      names.append(tag["name"])
    location = _point(event.get("location"))
    index, fraction = event.get("polyline_segment_index"), _number(event.get("polyline_segment_position"))
    if type(index) is not int or not 0 <= index < len(points) - 1 or fraction is None or not 0 <= fraction <= 1:
      raise ValueError("Invalid SDK event route position")
    start, end = points[index:index + 2]
    projected = (start[0] + fraction * (end[0] - start[0]), start[1] + fraction * (end[1] - start[1]))
    if math.hypot(*_xy(location, projected)) > 30:
      raise ValueError("SDK event location differs from route position")
    raw = event.get("speed_limit_raw")
    if raw is not None and (_number(raw) is None or raw < 0):
      raise ValueError("Invalid SDK raw event speed")
    if identifier in identities:
      if identities[identifier] != event:
        raise ValueError("Conflicting SDK event identity")
      continue
    identities[identifier] = event
    mobile = "MOBILE_CONTROL" in names or "POLICE_PATROL" in names
    if not mobile and "SPEED_CONTROL" not in names:
      continue  # POLICE is a generic compatibility tag, not proof of a patrol.
    expires = observed + (MOBILE_TTL_MS if mobile else FIXED_TTL_MS)
    if now >= expires:
      continue
    dx, dy = _xy(end, start)
    forward = math.degrees(math.atan2(dx, dy)) % 360 if math.hypot(dx, dy) >= 0.1 else None
    own_format = _format_evidence(event.get("speed_limit_sdk_format"))
    own_limit = _formatted_limit(raw, own_format)
    segment_raw, segment_format = limits[index], formats.get(_number(limits[index]))
    segment_limit = _formatted_limit(segment_raw, segment_format)
    result.append({"source": "yandex", "source_kind": "mobile" if mobile else "fixed",
                   "source_id": "yandex:sdk:" + identifier, "event_id": identifier,
                   "latitude": location[0], "longitude": location[1],
                   "raw_tags": [{"name": tag["name"], "sdk_ordinal": tag["sdk_ordinal"]} for tag in tags],
                   "raw_type": "MOBILE_CONTROL" if "MOBILE_CONTROL" in names else "POLICE_PATROL" if mobile else "SPEED_CONTROL",
                   "speed_raw": raw, "speed_raw_unit": "unknown", "speed_limit_kph": own_limit,
                   "speed_limit_source": "sdk_event_formatter" if own_limit is not None else None,
                   "speed_limit_confidence": "sdk_formatter_observed" if own_limit is not None else "unknown",
                   "event_speed_format_evidence": own_format, "route_segment_limit_raw": segment_raw,
                   "route_segment_limit_kph": segment_limit, "route_segment_format_evidence": segment_format,
                   "speed_applicability": "reported_route_direction_only", "route_heading_deg": forward,
                   "direction_deg": None if mobile else forward, "bidirectional_warning": mobile,
                   "route_segment_index": index, "route_segment_position": fraction,
                   "route_segment_geometry_lat_lon": [list(start), list(end)],
                   "observed_at_ms": observed, "expires_at_ms": expires, "expiry_basis": "local_import_policy",
                   "sdk_build": SDK_BUILD, "request_id": request_id, "conditions_update_seen": True, "conditions_outdated": False,
                   "auto_control_eligible": False})
  return result
