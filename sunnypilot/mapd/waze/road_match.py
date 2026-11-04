"""Associate a public Waze event with one unambiguous Waze road segment.

Road limits are separate evidence: they never rewrite camera speed bytes or
claim that a camera faces a particular direction. Enforcement reports may apply
to both approaches; legal-direction variants come solely from the matched road.
The caller still owns vehicle-to-road validation and all control policy.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

MAX_SEGMENTS = 5000
MAX_POINTS_PER_SEGMENT = 4096
MAX_TOTAL_EDGES = 100_000
MAX_MATCH_DISTANCE_M = 12.0
SOURCE = "waze_practice_features"
_EARTH_RADIUS_M = 6371000.0
_EVIDENCE_KEYS = frozenset(
  {
    "road_match_status",
    "road_match_reason",
    "road_limit_kph",
    "road_limit_source",
    "road_segment_id",
    "road_from_node_id",
    "road_to_node_id",
    "road_geometry",
    "road_travel_direction",
    "road_direction_coverage",
    "road_direction_basis",
    "road_heading_deg",
    "road_match_distance_m",
    "road_observed_at_ms",
    "road_limit_expires_at_ms",
    "road_limit_variants",
    "road_match_basis",
    "road_camera_global_id",
    "road_camera_segment_id",
    "road_camera_direction",
    "road_camera_types",
    "road_camera_coordinates",
    "road_camera_observed_at_ms",
    "road_camera_expires_at_ms",
    "control_latitude",
    "control_longitude",
    "road_report_distance_m",
    "road_primary_street_id",
  }
)


def _number(value):
  try:
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None
  except OverflowError:
    return None


def _point(value):
  if not isinstance(value, (list, tuple)) or len(value) != 2:
    return None
  lon, lat = (_number(coordinate) for coordinate in value)
  if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
    return None
  return lon, lat


def _xy(lon, lat, origin_lon, origin_lat):
  longitude_delta = (lon - origin_lon + 180) % 360 - 180
  return (math.radians(longitude_delta) * _EARTH_RADIUS_M * math.cos(math.radians(origin_lat)), math.radians(lat - origin_lat) * _EARTH_RADIUS_M)


def _projections(geometry, lon, lat):
  points = [_xy(*point, lon, lat) for point in geometry]
  projections, total = [], 0.0
  for start, end in zip(points, points[1:], strict=False):
    vx, vy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(vx, vy)
    if length < 0.01:
      continue
    fraction = max(0.0, min(1.0, -(start[0] * vx + start[1] * vy) / (length * length)))
    distance = math.hypot(start[0] + fraction * vx, start[1] + fraction * vy)
    heading = math.degrees(math.atan2(vx, vy)) % 360
    projections.append((distance, total + fraction * length, heading))
    total += length
  return sorted(projections)


def _angle_difference(a, b):
  return abs((a - b + 180) % 360 - 180)


def _interior_projection(geometry, lon, lat):
  """Nearest point, excluding endpoints/junctions and ambiguous local legs."""
  projections = _projections(geometry, lon, lat)
  if not projections:
    return None
  nearest = projections[0]
  lengths = [math.hypot(*_xy(*b, *a)) for a, b in zip(geometry, geometry[1:], strict=False)]
  if nearest[1] < 12 or sum(lengths) - nearest[1] < 12:
    return None
  if any(d <= nearest[0] + 0.5 and (abs(s - nearest[1]) > 12 or _angle_difference(h, nearest[2]) > 20) for d, s, h in projections):
    return None
  along = nearest[1]
  for (a, b), length in zip(zip(geometry, geometry[1:], strict=False), lengths, strict=True):
    if length > 0 and along <= length:
      fraction = along / length
      return nearest, (a[0] + fraction * (b[0] - a[0]), a[1] + fraction * (b[1] - a[1]))
    along -= length
  return None


def annotate_mobile_corridor(event: Mapping, segments: Iterable, now_ms: int | float) -> list[dict]:
  """Associate a mobile report with a proven pair of opposite carriageways.

  Same positive Waze street identity is necessary but insufficient: both roads
  must have exactly one legal direction, opposite local travel bearings, fresh
  usable evidence, interior projections, and no additional nearby road. Report
  coordinates remain immutable; control coordinates identify each road point.
  Unproven cases retain the ordinary physical-uniqueness matcher behavior.
  """
  records = []
  try:
    for index, item in enumerate(segments):
      if index >= MAX_SEGMENTS:
        return [annotate_road_limit(event, records + [None], now_ms)]
      records.append(item if isinstance(item, Mapping) else item.to_dict() if callable(getattr(item, "to_dict", None)) else None)
  except TypeError:
    return [annotate_road_limit(event, (), now_ms)]

  def fallback():
    return [annotate_road_limit(event, records, now_ms)]

  if not isinstance(event, Mapping) or event.get("source_kind") != "mobile" or event.get("raw_type") not in ("POLICE", "CAMERA"):
    return fallback()
  lat, lon = _number(event.get("latitude")), _number(event.get("longitude"))
  if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
    return fallback()
  nearby, seen, edges = [], set(), 0
  for record in records:
    if not isinstance(record, Mapping):
      return fallback()
    raw = record.get("geometry")
    if not isinstance(raw, (list, tuple)) or not 2 <= len(raw) <= MAX_POINTS_PER_SEGMENT:
      return fallback()
    geometry = [_point(p) for p in raw]
    edges += len(geometry) - 1
    if any(p is None for p in geometry) or edges > MAX_TOTAL_EDGES:
      return fallback()
    projections = _projections(geometry, lon, lat)
    if not projections or projections[0][0] > 40:
      continue
    identifier = record.get("segment_id")
    if type(identifier) is not int or identifier <= 0 or identifier in seen:
      return fallback()
    seen.add(identifier)
    nearby.append((record, geometry, projections[0][0]))
  if len(nearby) != 2:
    return fallback()
  nearby.sort(key=lambda item: item[2])
  if nearby[0][2] > 12 or abs(nearby[0][2] - nearby[1][2]) < 0.5:
    return fallback()
  street = nearby[0][0].get("primary_street_id")
  if type(street) is not int or street <= 0 or nearby[1][0].get("primary_street_id") != street:
    return fallback()
  results = []
  for record, geometry, distance in nearby:
    if record.get("forward_allowed") is record.get("reverse_allowed") or not _covered_search_window(record.get("bbox"), lat, lon, 40):
      return fallback()
    projection = _interior_projection(geometry, lon, lat)
    if projection is None:
      return fallback()
    _, point = projection
    projected = dict(event, longitude=point[0], latitude=point[1])
    matched = annotate_road_limit(projected, [record], now_ms)
    if matched.get("road_match_status") != "verified" or len(matched["road_limit_variants"]) != 1:
      return fallback()
    matched.update(latitude=event["latitude"], longitude=event["longitude"], control_latitude=point[1], control_longitude=point[0],
                   road_report_distance_m=distance, road_match_distance_m=distance, road_primary_street_id=street,
                   road_match_basis="mobile_parallel_carriageway_primary" if not results else "mobile_parallel_carriageway")
    results.append(matched)
  headings = [item["road_limit_variants"][0]["heading_deg"] for item in results]
  if any(heading is None for heading in headings) or _angle_difference(headings[0], headings[1]) < 160:
    return fallback()
  return results


def _covered_search_window(bbox, lat, lon, tolerance):
  if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
    return False
  values = [_number(value) for value in bbox]
  if any(value is None for value in values):
    return False
  west, south, east, north = values
  if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
    return False
  dlat = math.degrees(tolerance / _EARTH_RADIUS_M)
  dlon = dlat / max(0.01, math.cos(math.radians(lat)))
  return west <= lon - dlon and lon + dlon <= east and south <= lat - dlat and lat + dlat <= north


def _camera_link(event, links, lat, lon, now):
  """Fixed-only modern server association; no nearest-object guessing."""
  if event.get("source_kind") != "fixed" or event.get("raw_type") != "CAMERA" or event.get("raw_subtype") not in ("SPEED_CAMERA", "RED_LIGHT_CAMERA"):
    return None, None
  expected_type = "SPEED" if event["raw_subtype"] == "SPEED_CAMERA" else "RED_LIGHT"
  found = {}
  try:
    iterator = iter(links)
  except TypeError:
    return None, "invalid_camera_links"
  for index, item in enumerate(iterator):
    if index >= MAX_SEGMENTS:
      return None, "camera_link_budget"
    link = item if isinstance(item, Mapping) else item.to_dict() if callable(getattr(item, "to_dict", None)) else None
    if not isinstance(link, Mapping):
      return None, "invalid_camera_links"
    point = _point(link.get("coordinates"))
    if point is None:
      continue
    if math.hypot(*_xy(*point, lon, lat)) > 2.0:
      continue
    observed, expires = (_number(link.get(key)) for key in ("observed_at_ms", "expires_at_ms"))
    types = link.get("types")
    if (
      link.get("source") != SOURCE
      or link.get("usable_for_control") is not True
      or not isinstance(types, (list, tuple))
      or expected_type not in types
      or any(type(link.get(key)) is not int or link[key] <= 0 for key in ("global_id", "segment_id"))
      or observed is None
      or expires is None
      or not 0 < observed <= now < expires
      or not _covered_search_window(link.get("bbox"), lat, lon, 2.0)
    ):
      return None, "unverified_fixed_camera_link"
    if link.get("direction") not in ("FWD", "REV", "BOTH"):
      return None, "unverified_camera_direction"
    identifier = link["global_id"]
    if identifier in found:
      previous = found[identifier]
      signature = (link["segment_id"], point, link["direction"], tuple(types))
      old_signature = (previous["segment_id"], _point(previous["coordinates"]), previous["direction"], tuple(previous["types"]))
      if signature != old_signature:
        return None, "ambiguous_fixed_camera_links"
      if observed <= (_number(previous["observed_at_ms"]) or 0):
        continue
    found[identifier] = link
  if len(found) > 1:
    return None, "ambiguous_fixed_camera_links"
  return next(iter(found.values()), None), None


def annotate_road_limit(
  event: Mapping, segments: Iterable, now_ms: int | float, *, tolerance_m: float = MAX_MATCH_DISTANCE_M, camera_links: Iterable = ()
) -> dict:
  """Return a raw-event copy plus independently sourced road-limit evidence.

  ``segments`` accepts roads.WazeRoadSegment objects or their ``to_dict`` form.
  Physical uniqueness is checked without alert heading. One-way roads yield one
  variant; two-way equal limits yield a both-direction variant; unequal limits
  yield two variants. Missing limits, parallel candidates, intersections, stale
  evidence and incomplete query windows remain unknown. No raw field is changed.
  """
  result = {key: value for key, value in event.items() if key not in _EVIDENCE_KEYS} if isinstance(event, Mapping) else {}
  result.update(road_match_status="unknown", road_match_reason="no_road_match", road_limit_kph=None, road_limit_variants=[])

  def refuse(reason, status="unknown"):
    result.update(road_match_status=status, road_match_reason=reason)
    return result

  if not isinstance(event, Mapping) or event.get("source") != "waze" or event.get("source_kind") not in ("fixed", "mobile"):
    return refuse("invalid_event_source")
  lat, lon, now, tolerance = (_number(value) for value in (event.get("latitude"), event.get("longitude"), now_ms, tolerance_m))
  if lat is None or lon is None or now is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
    return refuse("invalid_event_coordinates")
  if tolerance is None or not 0 < tolerance <= MAX_MATCH_DISTANCE_M:
    return refuse("invalid_match_tolerance")
  observed, expires = (_number(event.get(key)) for key in ("observed_at_ms", "expires_at_ms"))
  if observed is None or expires is None or not 0 < observed <= now < expires:
    return refuse("event_not_fresh")
  camera_link, link_error = _camera_link(event, camera_links, lat, lon, now)
  if link_error:
    return refuse(link_error, "ambiguous" if link_error == "ambiguous_fixed_camera_links" else "unknown")

  candidates, records, edges = {}, 0, 0
  try:
    iterator = iter(segments)
  except TypeError:
    return refuse("invalid_road_snapshot")
  for item in iterator:
    records += 1
    if records > MAX_SEGMENTS:
      return refuse("road_match_budget")
    road = item if isinstance(item, Mapping) else item.to_dict() if callable(getattr(item, "to_dict", None)) else None
    if not isinstance(road, Mapping):
      return refuse("invalid_road_snapshot")
    raw_geometry = road.get("geometry")
    if not isinstance(raw_geometry, (list, tuple)) or not 2 <= len(raw_geometry) <= MAX_POINTS_PER_SEGMENT:
      return refuse("invalid_road_geometry")
    geometry = [_point(value) for value in raw_geometry]
    if any(value is None for value in geometry):
      return refuse("invalid_road_geometry")
    edges += len(geometry) - 1
    if edges > MAX_TOTAL_EDGES:
      return refuse("road_match_budget")
    projections = _projections(geometry, lon, lat)
    if not projections or projections[0][0] > tolerance:
      continue
    identifier = road.get("segment_id")
    if type(identifier) is not int or identifier <= 0:
      return refuse("missing_road_identity")
    signature = (
      geometry,
      road.get("from_node_id"),
      road.get("to_node_id"),
      road.get("forward_allowed"),
      road.get("reverse_allowed"),
      road.get("forward_limit_kph"),
      road.get("reverse_limit_kph"),
    )
    if identifier in candidates:
      previous = candidates[identifier]
      if previous[0] != signature:
        return refuse("conflicting_road_records", "ambiguous")
      if (_number(road.get("observed_at_ms")) or 0) <= (_number(previous[1].get("observed_at_ms")) or 0):
        continue
    candidates[identifier] = (signature, road, geometry, projections)
  if not candidates:
    return result
  if camera_link is not None:
    if camera_link["segment_id"] not in candidates:
      return refuse("camera_link_segment_geometry_mismatch")
    selected = candidates[camera_link["segment_id"]]
  elif len(candidates) != 1:
    return refuse("multiple_nearby_roads", "ambiguous")
  else:
    selected = next(iter(candidates.values()))
  _, road, geometry, projections = selected
  nearest = projections[0]
  if any(distance <= tolerance and abs(along - nearest[1]) > 2 * tolerance for distance, along, _ in projections):
    return refuse("multiple_nearby_road_legs", "ambiguous")
  if road.get("source") != SOURCE or road.get("speed_unit") != "km/h":
    return refuse("unverified_road_source_or_unit")
  road_observed, road_expires = (_number(road.get(key)) for key in ("observed_at_ms", "expires_at_ms"))
  if road_observed is None or road_expires is None or not 0 < road_observed <= now < road_expires:
    return refuse("road_snapshot_not_fresh")
  if road.get("usable_for_control") is not True:
    return refuse("road_restriction_unresolved")
  if not _covered_search_window(road.get("bbox"), lat, lon, tolerance):
    return refuse("incomplete_search_window")
  if any(type(road.get(key)) is not int or road[key] <= 0 for key in ("from_node_id", "to_node_id")):
    return refuse("missing_road_nodes")
  forward, reverse = road.get("forward_allowed"), road.get("reverse_allowed")
  if type(forward) is not bool or type(reverse) is not bool or not (forward or reverse):
    return refuse("no_verified_legal_direction")
  if camera_link is not None:
    forward = forward and camera_link["direction"] in ("FWD", "BOTH")
    reverse = reverse and camera_link["direction"] in ("REV", "BOTH")
    if not (forward or reverse):
      return refuse("camera_direction_not_legal")
  fwd_limit, rev_limit = (_number(road.get(key)) for key in ("forward_limit_kph", "reverse_limit_kph"))
  if (forward and (fwd_limit is None or not 0 < fwd_limit <= 300)) or (reverse and (rev_limit is None or not 0 < rev_limit <= 300)):
    return refuse("missing_directional_posted_limit")
  if forward and reverse and math.isclose(fwd_limit, rev_limit, rel_tol=0, abs_tol=1e-6):
    variants = [{"travel_direction": "both", "limit_kph": fwd_limit, "heading_deg": None, "direction_basis": "equal_limits_both_directions"}]
  else:
    if any(distance <= nearest[0] + 0.5 and _angle_difference(heading, nearest[2]) > 30 for distance, _, heading in projections):
      return refuse("ambiguous_road_tangent", "ambiguous")
    basis = "explicit_camera_direction" if camera_link is not None else "per_direction_road_limit" if forward and reverse else "only_legal_direction"
    variants = []
    if forward:
      variants.append({"travel_direction": "forward", "limit_kph": fwd_limit, "heading_deg": nearest[2], "direction_basis": basis})
    if reverse:
      variants.append({"travel_direction": "reverse", "limit_kph": rev_limit, "heading_deg": (nearest[2] + 180) % 360, "direction_basis": basis})
  direction = "both" if forward and reverse else "forward" if forward else "reverse"
  result.update(
    road_match_status="verified",
    road_match_reason="unique_directed_road",
    road_limit_source=SOURCE,
    road_limit_kph=variants[0]["limit_kph"] if len(variants) == 1 else None,
    road_limit_variants=variants,
    road_segment_id=road["segment_id"],
    road_from_node_id=road["from_node_id"],
    road_to_node_id=road["to_node_id"],
    road_geometry=geometry,
    road_travel_direction=direction,
    road_direction_coverage=direction,
    road_direction_basis=variants[0]["direction_basis"],
    road_heading_deg=variants[0]["heading_deg"] if len(variants) == 1 else None,
    road_match_distance_m=nearest[0],
    road_observed_at_ms=road_observed,
    road_limit_expires_at_ms=min(road_expires, expires),
  )
  result["road_match_basis"] = "explicit_camera_segment" if camera_link is not None else "unique_geometry"
  if camera_link is not None:
    result.update(
      road_camera_global_id=camera_link["global_id"],
      road_camera_segment_id=camera_link["segment_id"],
      road_camera_direction=camera_link["direction"],
      road_camera_types=list(camera_link["types"]),
      road_camera_coordinates=_point(camera_link["coordinates"]),
      road_camera_observed_at_ms=camera_link["observed_at_ms"],
      road_camera_expires_at_ms=camera_link["expires_at_ms"],
      road_limit_expires_at_ms=min(result["road_limit_expires_at_ms"], camera_link["expires_at_ms"]),
    )
  return result
