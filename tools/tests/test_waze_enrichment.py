"""Raw report -> production Practice road client -> policy, without live sockets."""
from copy import deepcopy

import pytest

from test_waze_roads import REAL_SEGMENTS, NOW_MS, body
from openpilot.sunnypilot.mapd.waze.backend import WazeBackend
from openpilot.sunnypilot.mapd.waze_policy import events_to_cameras


def report(**changes):
  lon, lat = REAL_SEGMENTS[0]['geometry']['coordinates'][1]
  return dict(source='waze', source_kind='mobile', source_id='waze:rt:synthetic', raw_type='POLICE',
              raw_subtype='POLICE_WITH_MOBILE_CAMERA', latitude=lat, longitude=lon, heading_raw=270,
              speed_raw=None, speed_limit_kph=None, speed_unit=None, observed_at_ms=NOW_MS,
              expires_at_ms=NOW_MS+180000, **changes)


def test_backend_enriches_synthetic_report_from_real_public_road_fixture_and_reuses_cache(tmp_path):
  calls = []
  def transport(url, **kwargs):
    assert url.startswith('https://www.waze.com/row-Descartes/app/Features?') and 'sandbox=true' in url
    assert kwargs['body'] is None if 'body' in kwargs else True
    calls.append(url)
    return body()
  backend = WazeBackend(tmp_path, transport=transport, clock=lambda: NOW_MS/1000)
  raw = report()
  enriched, = backend.enrich_events([raw])
  assert raw.get('road_segment_id') is None and enriched['road_segment_id'] == REAL_SEGMENTS[0]['id']
  assert enriched['road_limit_variants'][0]['limit_kph'] == 50
  cameras = events_to_cameras([enriched], NOW_MS/1000)
  assert len(cameras) == 2 and sum(c.allowed_auto_control for c in cameras) == 1
  qualified = next(c for c in cameras if c.allowed_auto_control)
  assert qualified.direction_type == 0 and qualified.direction_deg is None
  assert enriched['speed_raw'] is None and enriched['heading_raw'] == 270
  first_calls = len(calls)
  assert backend.enrich_events([raw]) == [enriched] and len(calls) == first_calls
  assert backend.road_error_count == 0


def test_failed_road_download_preserves_raw_report_as_warning_and_discards_old_evidence(tmp_path):
  def transport(*_args, **_kwargs):
    raise OSError('synthetic network failure')
  backend = WazeBackend(tmp_path, transport=transport, clock=lambda: NOW_MS/1000)
  raw = report(road_match_status='verified', road_limit_source='forged_old_evidence', road_limit_kph=60)
  enriched, = backend.enrich_events([raw])
  assert enriched['road_match_status'] == 'unknown' and 'road_limit_source' not in enriched
  assert enriched['source_id'] == raw['source_id'] and enriched['observed_at_ms'] == raw['observed_at_ms']
  assert backend.road_error_count == 1
  camera, = events_to_cameras([enriched], NOW_MS/1000)
  assert not camera.allowed_auto_control and camera.direction_deg is None


def test_enrichment_budget_is_bounded_and_unrelated_events_do_not_fetch_roads(tmp_path):
  calls = []
  def transport(url, **_kwargs):
    calls.append(url)
    return body()
  backend = WazeBackend(tmp_path, transport=transport, clock=lambda: NOW_MS/1000)
  irrelevant = deepcopy(report())
  irrelevant.update(raw_type='HAZARD', raw_subtype='NO_SUBTYPE')
  assert backend.enrich_events([irrelevant]) == [irrelevant] and not calls
  reports = [report() for _ in range(33)]
  enriched = backend.enrich_events(reports)
  assert len(enriched) == 33 and backend.road_error_count == 1
  assert enriched[-1]['road_match_status'] == 'unknown'
  assert len(calls) <= 4


def test_editor_transport_rejects_writes_and_unrelated_paths_without_network():
  from openpilot.sunnypilot.mapd.waze.transport import request
  for url, payload in [('https://www.waze.com/login', None), ('https://www.waze.com/row-Descartes/app/Features', b'write')]:
    with pytest.raises(ValueError, match='read-only'):
      request(url, body=payload)


def test_disabled_generation_stops_new_road_requests_between_reports(tmp_path):
  calls = []
  def transport(url, **_kwargs):
    calls.append(url)
    return body()
  backend = WazeBackend(tmp_path, transport=transport, clock=lambda: NOW_MS/1000)
  checks = iter([True, False])
  enriched = backend.enrich_events([report(), report()], should_continue=lambda: next(checks))
  assert len(enriched) == 1 and enriched[0]['road_match_status'] == 'verified'
  assert len(calls) == 1
