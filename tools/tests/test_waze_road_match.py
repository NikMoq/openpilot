"""Directed Waze road-limit association; raw report headings never filter approaches."""

import math
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
for name, relative in (("openpilot", "."), ("openpilot.sunnypilot", "sunnypilot"), ("openpilot.sunnypilot.mapd", "sunnypilot/mapd")):
  if name not in sys.modules:
    namespace = ModuleType(name)
    namespace.__path__ = [str(ROOT / relative)]
    sys.modules[name] = namespace

from openpilot.sunnypilot.mapd.waze import road_match
from openpilot.sunnypilot.mapd.waze.road_match import annotate_road_limit

NOW = 1_793_550_000_000


def corridor_roads(**opposite_changes):
  # Synthetic divided street, 25m lateral separation, independent posted limits.
  delta = math.degrees(25 / (6371000 * math.cos(math.radians(55))))
  primary = road(primary_street_id=900, reverse_allowed=False)
  opposite = road(segment_id=124, from_node_id=2001, to_node_id=2002, primary_street_id=900,
                  geometry=[(37 + delta, 54.999), (37 + delta, 55.001)], forward_allowed=False, reverse_limit_kph=70,
                  **opposite_changes)
  return [primary, opposite]


def test_mobile_corridor_uses_independent_limits_and_preserves_raw_report():
  raw = event(heading_raw=90)
  results = road_match.annotate_mobile_corridor(raw, corridor_roads(), NOW)
  assert len(results) == 2
  assert [item['road_limit_kph'] for item in results] == [50, 70]
  assert [item['road_direction_coverage'] for item in results] == ['forward', 'reverse']
  for item in results:
    assert all(item[key] == raw[key] for key in raw)
    assert item['road_primary_street_id'] == 900
    assert item['control_latitude'] == pytest.approx(55)
  assert results[1]['road_report_distance_m'] == pytest.approx(25, abs=0.01)
  assert results[1]['control_longitude'] > raw['longitude']


@pytest.mark.parametrize('change', [
  {'primary_street_id': None}, {'primary_street_id': 901}, {'expires_at_ms': NOW},
  {'usable_for_control': False}, {'speed_unit': None}, {'reverse_limit_kph': None},
  {'forward_allowed': True}, {'bbox': (36.9999, 54.9999, 37.001, 55.001)},
])
def test_mobile_corridor_unproven_opposite_retains_normal_match(change):
  roads = corridor_roads()
  roads[1].update(change)
  results = road_match.annotate_mobile_corridor(event(), roads, NOW)
  assert len(results) == 1
  assert results[0]['road_segment_id'] == 123
  assert 'control_latitude' not in results[0]


def test_mobile_corridor_rejects_intersection_third_road_and_end_projection():
  roads = corridor_roads()
  crossing = road(segment_id=125, geometry=[(36.999, 55), (37.001, 55)])
  assert len(road_match.annotate_mobile_corridor(event(), [*roads, crossing], NOW)) == 1
  end_report = event(latitude=54.99901)
  result = road_match.annotate_mobile_corridor(end_report, roads, NOW)
  assert len(result) == 1
  assert 'control_latitude' not in result[0]


def test_mobile_corridor_pair_inside_twelve_metres_can_resolve_by_corridor_proof():
  roads = corridor_roads()
  roads[1]['geometry'] = [(37.00012, 54.999), (37.00012, 55.001)]
  assert annotate_road_limit(event(), roads, NOW)['road_match_status'] == 'ambiguous'
  assert len(road_match.annotate_mobile_corridor(event(), roads, NOW)) == 2


def test_mobile_corridor_fixed_event_never_uses_parallel_association():
  result = road_match.annotate_mobile_corridor(fixed_event(), corridor_roads(), NOW)
  assert len(result) == 1
  assert 'control_latitude' not in result[0]


def test_mobile_corridor_fallback_clears_all_preexisting_projection_evidence():
  raw = event(control_latitude=10, control_longitude=20, road_report_distance_m=1, road_primary_street_id=900)
  roads = corridor_roads()
  roads[0]['primary_street_id'] = None
  result = road_match.annotate_mobile_corridor(raw, roads, NOW)[0]
  empty = annotate_road_limit(raw, [], NOW)
  for matched in (result, empty):
    assert not {'control_latitude', 'control_longitude', 'road_report_distance_m', 'road_primary_street_id'} & matched.keys()

def event(**changes):
  result = {
    "source": "waze",
    "source_kind": "mobile",
    "source_id": "waze:rt:synthetic",
    "raw_type": "POLICE",
    "raw_subtype": "POLICE_WITH_MOBILE_CAMERA",
    "latitude": 55.0,
    "longitude": 37.0,
    "heading_raw": 270,
    "speed_raw": None,
    "speed_unit": None,
    "speed_limit_kph": None,
    "observed_at_ms": NOW - 1000,
    "expires_at_ms": NOW + 180000,
  }
  return {**result, **changes}


def road(**changes):
  result = {
    "segment_id": 123,
    "from_node_id": 1001,
    "to_node_id": 1002,
    "geometry": [(37.0, 54.999), (37.0, 55.001)],
    "forward_allowed": True,
    "reverse_allowed": True,
    "forward_limit_kph": 50.0,
    "reverse_limit_kph": 50.0,
    "source": "waze_practice_features",
    "speed_unit": "km/h",
    "observed_at_ms": NOW - 60000,
    "expires_at_ms": NOW + 1200000,
    "bbox": (36.99, 54.99, 37.01, 55.01),
    "usable_for_control": True,
    "restriction_reason": "",
  }
  return {**result, **changes}


def camera_link(**changes):
  result = {
    "global_id": 356390,
    "segment_id": 123,
    "coordinates": (37.0, 55.0),
    "direction": "REV",
    "types": ("SPEED", "RED_LIGHT"),
    "source": "waze_practice_features",
    "usable_for_control": True,
    "observed_at_ms": NOW - 1000,
    "expires_at_ms": NOW + 60000,
    "bbox": (36.99, 54.99, 37.01, 55.01),
  }
  return {**result, **changes}


def fixed_event(**changes):
  return event(source_kind="fixed", raw_type="CAMERA", raw_subtype="SPEED_CAMERA", **changes)


def test_equal_two_way_limit_applies_both_directions_without_camera_heading_claim():
  raw = event()
  matched = annotate_road_limit(raw, [road()], NOW)
  assert matched["road_match_status"] == "verified"
  assert matched["road_limit_kph"] == 50
  assert matched["road_limit_source"] == "waze_practice_features"
  assert matched["road_direction_coverage"] == "both"
  assert matched["road_limit_variants"] == [
    {"travel_direction": "both", "limit_kph": 50.0, "heading_deg": None, "direction_basis": "equal_limits_both_directions"}
  ]
  assert matched["road_limit_expires_at_ms"] == raw["expires_at_ms"]
  for key, value in raw.items():
    assert matched[key] == value
  assert matched["speed_limit_kph"] is None and "direction_status" not in matched
  assert "road_match_status" not in raw


@pytest.mark.parametrize("heading", [None, 0, 90, 180, 270, 359])
def test_unequal_two_way_limits_generate_both_variants_regardless_report_heading(heading):
  matched = annotate_road_limit(event(heading_raw=heading), [road(forward_limit_kph=30, reverse_limit_kph=70)], NOW)
  assert matched["road_match_status"] == "verified" and matched["road_limit_kph"] is None
  variants = matched["road_limit_variants"]
  assert [(v["travel_direction"], v["limit_kph"]) for v in variants] == [("forward", 30), ("reverse", 70)]
  assert variants[0]["heading_deg"] == pytest.approx(0)
  assert variants[1]["heading_deg"] == pytest.approx(180)
  assert matched["heading_raw"] == heading


@pytest.mark.parametrize("forward,reverse,direction,heading", [(True, False, "forward", 0), (False, True, "reverse", 180)])
def test_one_way_direction_comes_from_road_not_report(forward, reverse, direction, heading):
  matched = annotate_road_limit(event(heading_raw=90), [road(forward_allowed=forward, reverse_allowed=reverse)], NOW)
  variant = matched["road_limit_variants"][0]
  assert variant["travel_direction"] == direction
  assert variant["heading_deg"] == pytest.approx(heading)
  assert variant["direction_basis"] == "only_legal_direction"
  assert matched["heading_raw"] == 90


def test_parallel_roads_are_ambiguous_even_when_one_is_closer():
  dx = math.degrees(6 / (6371000 * math.cos(math.radians(55))))
  other = road(segment_id=124, geometry=[(37 + dx, 54.999), (37 + dx, 55.001)], forward_limit_kph=30, reverse_limit_kph=30)
  matched = annotate_road_limit(event(), [road(), other], NOW)
  assert matched["road_match_status"] == "ambiguous"
  assert matched["road_match_reason"] == "multiple_nearby_roads"
  assert matched["road_limit_variants"] == [] and matched["road_limit_kph"] is None


def test_intersection_and_adjacent_segment_endpoint_fail_closed():
  cross = road(segment_id=124, geometry=[(36.999, 55), (37.001, 55)])
  assert annotate_road_limit(event(), [road(), cross], NOW)["road_match_status"] == "ambiguous"
  joining = road(segment_id=125, geometry=[(37, 55.00004), (37, 55.002)])
  assert annotate_road_limit(event(), [road(), joining], NOW)["road_match_status"] == "ambiguous"


def test_duplicate_same_road_records_are_not_false_ambiguity_but_conflicts_are():
  assert annotate_road_limit(event(), [road(), road(observed_at_ms=NOW - 1000)], NOW)["road_match_status"] == "verified"
  conflict = annotate_road_limit(event(), [road(), road(forward_limit_kph=30)], NOW)
  assert conflict["road_match_status"] == "ambiguous"
  assert conflict["road_match_reason"] == "conflicting_road_records"


@pytest.mark.parametrize(
  "changes,reason",
  [
    ({"reverse_limit_kph": None}, "missing_directional_posted_limit"),
    ({"forward_limit_kph": 0}, "missing_directional_posted_limit"),
    ({"forward_allowed": False, "reverse_allowed": False}, "no_verified_legal_direction"),
    ({"usable_for_control": False}, "road_restriction_unresolved"),
    ({"speed_unit": "mph"}, "unverified_road_source_or_unit"),
    ({"source": "osm"}, "unverified_road_source_or_unit"),
    ({"expires_at_ms": NOW}, "road_snapshot_not_fresh"),
    ({"observed_at_ms": NOW + 1}, "road_snapshot_not_fresh"),
    ({"bbox": (37, 54.99, 37.01, 55.01)}, "incomplete_search_window"),
  ],
)
def test_unknown_or_restricted_road_evidence_is_not_accepted(changes, reason):
  matched = annotate_road_limit(event(), [road(**changes)], NOW)
  assert matched["road_match_status"] == "unknown"
  assert matched["road_match_reason"] == reason
  assert matched["road_limit_variants"] == []


def test_reprocessing_clears_previous_road_evidence_without_touching_raw_camera_speed():
  fixed = event(source_kind="fixed", raw_type="CAMERA", raw_subtype="SPEED_CAMERA", speed_raw=50, heading_raw=303)
  first = annotate_road_limit(fixed, [road()], NOW)
  unknown = annotate_road_limit(first, [], NOW)
  assert unknown["road_match_status"] == "unknown"
  assert "road_segment_id" not in unknown and unknown["road_limit_variants"] == []
  assert unknown["speed_raw"] == 50 and unknown["heading_raw"] == 303
  assert unknown["speed_unit"] is None and unknown["speed_limit_kph"] is None


def test_dataclass_adapter_and_geometry_budget(monkeypatch):
  normalized = SimpleNamespace(to_dict=lambda: road())
  assert annotate_road_limit(event(), [normalized], NOW)["road_match_status"] == "verified"
  monkeypatch.setattr(road_match, "MAX_SEGMENTS", 1)
  assert annotate_road_limit(event(), [road(), road()], NOW)["road_match_reason"] == "road_match_budget"


def test_expired_event_and_invalid_coordinates_cannot_acquire_road_limit():
  assert annotate_road_limit(event(expires_at_ms=NOW), [road()], NOW)["road_match_reason"] == "event_not_fresh"
  assert annotate_road_limit(event(latitude=float("nan")), [road()], NOW)["road_match_reason"] == "invalid_event_coordinates"


def test_explicit_fixed_link_selects_server_segment_despite_parallel_road():
  dx = math.degrees(6 / (6371000 * math.cos(math.radians(55))))
  parallel = road(segment_id=124, geometry=[(37 + dx, 54.999), (37 + dx, 55.001)])
  raw = fixed_event(speed_raw=50, heading_raw=303)
  matched = annotate_road_limit(raw, [road(forward_limit_kph=30, reverse_limit_kph=70), parallel], NOW, camera_links=[camera_link()])
  assert matched["road_match_status"] == "verified"
  assert matched["road_match_basis"] == "explicit_camera_segment"
  assert matched["road_camera_global_id"] == 356390 and matched["road_camera_direction"] == "REV"
  assert matched["road_segment_id"] == 123 and matched["road_direction_coverage"] == "reverse"
  assert matched["road_limit_variants"] == [
    {"travel_direction": "reverse", "limit_kph": 70.0, "heading_deg": 180.0, "direction_basis": "explicit_camera_direction"}
  ]
  assert matched["road_limit_expires_at_ms"] == NOW + 60000
  for key, value in raw.items():
    assert matched[key] == value


def test_explicit_fixed_link_at_intersection_restricts_two_way_road_to_enforced_forward_direction():
  crossing = road(segment_id=124, geometry=[(36.999, 55), (37.001, 55)])
  matched = annotate_road_limit(fixed_event(), [road(), crossing], NOW, camera_links=[camera_link(direction="FWD")])
  assert matched["road_match_status"] == "verified" and matched["road_direction_coverage"] == "forward"
  assert len(matched["road_limit_variants"]) == 1
  assert matched["road_limit_variants"][0]["travel_direction"] == "forward"
  assert matched["road_limit_variants"][0]["heading_deg"] == pytest.approx(0)


def test_mobile_events_never_read_or_use_fixed_camera_links():
  class ForbiddenLinks:
    def __iter__(self):
      pytest.fail("Mobile matching must ignore fixed-camera links completely")

  matched = annotate_road_limit(event(), [road(forward_limit_kph=30, reverse_limit_kph=70)], NOW, camera_links=ForbiddenLinks())
  assert [variant["travel_direction"] for variant in matched["road_limit_variants"]] == ["forward", "reverse"]
  assert matched["road_match_basis"] == "unique_geometry" and "road_camera_global_id" not in matched


def test_far_link_is_not_guessed_as_nearest_and_missing_link_segment_stays_unknown():
  far_lat = 55 + math.degrees(3 / 6371000)
  physical = annotate_road_limit(fixed_event(), [road()], NOW, camera_links=[camera_link(coordinates=(37.0, far_lat))])
  assert physical["road_match_basis"] == "unique_geometry" and "road_camera_global_id" not in physical
  missing = annotate_road_limit(fixed_event(), [road()], NOW, camera_links=[camera_link(segment_id=999)])
  assert missing["road_match_status"] == "unknown" and missing["road_match_reason"] == "camera_link_segment_geometry_mismatch"


@pytest.mark.parametrize(
  "changes,reason",
  [
    ({"direction": "UNKNOWN"}, "unverified_camera_direction"),
    ({"usable_for_control": False}, "unverified_fixed_camera_link"),
    ({"expires_at_ms": NOW}, "unverified_fixed_camera_link"),
    ({"types": ("RED_LIGHT",)}, "unverified_fixed_camera_link"),
    ({"source": "osm"}, "unverified_fixed_camera_link"),
  ],
)
def test_unverified_fixed_camera_link_never_overrides_geometry(changes, reason):
  matched = annotate_road_limit(fixed_event(), [road()], NOW, camera_links=[camera_link(**changes)])
  assert matched["road_match_status"] == "unknown" and matched["road_match_reason"] == reason
  assert matched["road_limit_variants"] == []


def test_ambiguous_fixed_links_fail_but_duplicate_identical_global_id_is_allowed():
  duplicate = annotate_road_limit(fixed_event(), [road()], NOW, camera_links=[camera_link(), camera_link()])
  assert duplicate["road_camera_global_id"] == 356390
  ambiguous = annotate_road_limit(fixed_event(), [road()], NOW, camera_links=[camera_link(), camera_link(global_id=356391)])
  assert ambiguous["road_match_status"] == "ambiguous" and ambiguous["road_match_reason"] == "ambiguous_fixed_camera_links"


def test_fixed_camera_direction_cannot_select_illegal_travel_direction():
  matched = annotate_road_limit(fixed_event(), [road(forward_allowed=True, reverse_allowed=False)], NOW, camera_links=[camera_link(direction="REV")])
  assert matched["road_match_status"] == "unknown" and matched["road_match_reason"] == "camera_direction_not_legal"
