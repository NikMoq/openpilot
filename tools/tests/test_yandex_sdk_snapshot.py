"""Synthetic SDK import evidence only; no real payloads, keys or network."""
import copy
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
for name, relative in (("openpilot", "."), ("openpilot.sunnypilot", "sunnypilot"), ("openpilot.sunnypilot.mapd", "sunnypilot/mapd")):
  if name not in sys.modules:
    module = ModuleType(name)
    module.__path__ = [str(ROOT / relative)]
    sys.modules[name] = module

from openpilot.sunnypilot.mapd.yandex.sdk_snapshot import normalize_snapshot

NOW = 1_800_000_000_000


def formatter(raw=16.666666, value=60, unit="kilometers per hour"):
  return dict(raw=raw, canonical_value=value, canonical_unit=unit, source="official_sdk_i18n_formatter", status="formatted")


def snapshot(tags=("SPEED_CONTROL", "POLICE")):
  return dict(schema_version=1, source="yandex_mapkit_full_sdk", sdk_build="4.45.0-full", request_id="synthetic-only",
              observed_at_ms=NOW, completed_at_ms=NOW + 1, conditions_outdated=False,
              conditions_update_seen=True, conditions_update_requested=True, conditions_update_status="observed",
              route_geometry_lat_lon=[[55, 37], [55.001, 37]], segment_speed_limits_raw=[16.666666],
              sdk_speed_format_examples=[formatter()], events=[dict(event_id="synthetic-event", location=[55.0005, 37],
              tags=[dict(name=name, sdk_ordinal=index) for index, name in enumerate(tags)], speed_limit_raw=16.666666,
              speed_limit_sdk_format=formatter(), polyline_segment_index=0, polyline_segment_position=0.5)])


def test_fixed_warning_preserves_independent_speed_provenance_without_granting_control():
  raw = snapshot()
  previous = copy.deepcopy(raw)
  event, = normalize_snapshot(raw, NOW + 2)
  assert event["source_id"] == "yandex:sdk:synthetic-event"
  assert event["speed_limit_kph"] == 60 and event["speed_raw"] == 16.666666
  assert event["route_segment_limit_kph"] == 60
  assert event["speed_raw_unit"] == "unknown" and event["direction_deg"] == pytest.approx(0)
  assert event["expires_at_ms"] == NOW + 300000 and event["expiry_basis"] == "local_import_policy"
  assert event["auto_control_eligible"] is False and raw == previous


@pytest.mark.parametrize("tag", ["MOBILE_CONTROL", "POLICE_PATROL"])
def test_mobile_warns_both_approaches_but_limit_evidence_covers_only_reported_route(tag):
  event, = normalize_snapshot(snapshot((tag, "POLICE")), NOW + 2)
  assert event["source_kind"] == "mobile" and event["bidirectional_warning"]
  assert event["direction_deg"] is None and event["route_heading_deg"] == pytest.approx(0)
  assert event["speed_applicability"] == "reported_route_direction_only"
  assert event["expires_at_ms"] == NOW + 180000 and not event["auto_control_eligible"]
  assert normalize_snapshot(snapshot((tag,)), NOW + 180000) == []


@pytest.mark.parametrize("tags", [("POLICE",), ("ACCIDENT",), ("NO_STOPPING_CONTROL", "POLICE")])
def test_generic_police_and_other_controls_do_not_invent_supported_camera_type(tags):
  assert normalize_snapshot(snapshot(tags), NOW + 2) == []


@pytest.mark.parametrize("change", [dict(source="waze"), dict(sdk_build="unknown"), dict(schema_version=True),
    dict(conditions_outdated=True), dict(conditions_update_seen=False), dict(conditions_update_requested=False),
    dict(conditions_update_status="timed_out"), dict(observed_at_ms=NOW + 10), dict(completed_at_ms=NOW - 1),
    dict(request_id="bad\nidentity"), dict(segment_speed_limits_raw=[]), dict(route_geometry_lat_lon=[[55, 37]]),
    dict(segment_speed_limits_raw=[float("nan")]), dict(observed_at_ms=10**1000)])
def test_invalid_provenance_conditions_geometry_and_times_fail_closed(change):
  raw = snapshot()
  raw.update(change)
  with pytest.raises(ValueError):
    normalize_snapshot(raw, NOW + 2)


@pytest.mark.parametrize("change", [dict(polyline_segment_index=-1), dict(polyline_segment_index=1),
    dict(polyline_segment_index=True), dict(polyline_segment_position=1.1), dict(location=[56, 37]),
    dict(speed_limit_raw=float("inf")), dict(event_id=""), dict(tags=[dict(name="SPEED_CONTROL", sdk_ordinal=True)])])
def test_event_position_and_raw_values_are_strict(change):
  raw = snapshot()
  raw["events"][0].update(change)
  with pytest.raises(ValueError):
    normalize_snapshot(raw, NOW + 2)


@pytest.mark.parametrize("descriptor", [None, formatter(unit="miles per hour"), formatter(raw=20),
    dict(formatter(), source="unverified"), dict(formatter(), status="formatter_unavailable")])
def test_event_limit_requires_matching_explicit_kmh_sdk_observation(descriptor):
  raw = snapshot()
  raw["events"][0]["speed_limit_sdk_format"] = descriptor
  event, = normalize_snapshot(raw, NOW + 2)
  assert event["speed_limit_kph"] is None
  assert event["route_segment_limit_kph"] == 60  # Separate provenance, never substituted into camera speed.


def test_duplicate_identity_collapses_but_conflicts_fail():
  raw = snapshot()
  raw["events"].append(copy.deepcopy(raw["events"][0]))
  assert len(normalize_snapshot(raw, NOW + 2)) == 1
  raw["events"][1]["speed_limit_raw"] = 10
  with pytest.raises(ValueError, match="Conflicting SDK event"):
    normalize_snapshot(raw, NOW + 2)


def test_formatter_conflicts_and_oversized_geometry_fail():
  raw = snapshot()
  raw["sdk_speed_format_examples"].append(formatter(value=90))
  with pytest.raises(ValueError, match="Conflicting SDK formatter"):
    normalize_snapshot(raw, NOW + 2)
  raw = snapshot()
  raw["route_geometry_lat_lon"] = [[55, 37]] * 100001
  with pytest.raises(ValueError, match="geometry"):
    normalize_snapshot(raw, NOW + 2)


def test_fixed_import_cap_expires_without_fabricating_source_lifetime():
  assert normalize_snapshot(snapshot(), NOW + 300000) == []


def test_formatter_metadata_cannot_carry_unbounded_or_nonnumeric_values():
  raw = snapshot()
  raw["events"][0]["speed_limit_sdk_format"]["canonical_unit"] = "x" * 129
  with pytest.raises(ValueError, match="formatter field"):
    normalize_snapshot(raw, NOW + 2)
  raw = snapshot()
  raw["events"][0]["speed_limit_sdk_format"]["canonical_value"] = {"unexpected": 1}
  with pytest.raises(ValueError, match="formatter number"):
    normalize_snapshot(raw, NOW + 2)
