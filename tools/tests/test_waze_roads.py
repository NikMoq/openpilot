"""Anonymous Practice-mode roads: actual sanitized records and offline bounds/cache tests."""
import copy
import json
import os
import socket
import sys
from pathlib import Path
from types import ModuleType
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parents[2]
for name, relative in [('openpilot', '.'), ('openpilot.sunnypilot', 'sunnypilot'), ('openpilot.sunnypilot.mapd', 'sunnypilot/mapd')]:
  if name not in sys.modules:
    module = ModuleType(name)
    module.__path__ = [str(ROOT / relative)]
    sys.modules[name] = module

from openpilot.sunnypilot.mapd.waze.roads import CACHE_TTL_MS, WazeRoadClient, features_url, parse_camera_links, parse_features

BBOX = (13.4, 52.515, 13.41, 52.525)
NOW_MS = 1793793600000
# Sanitized public-map facts only; user/editor metadata intentionally excluded.
# Retrieved 2026-11-04 via anonymous Features sandbox roadTypes/v=2 request for BBOX, standard production urllib transport,
# honest default SunnyPilot UA, no cookies or authentication. Full response 332819
# bytes SHA256 06e34b3d47681da094080131fff79818006494867d995c3a993a160ba46cd93d.
REAL_SEGMENTS = [
  {"id": 483663062, "fromNodeID": 84433516, "toNodeID": 362902323,
   "fwdDirection": True, "revDirection": True, "fwdMaxSpeed": 50, "revMaxSpeed": 50, "restrictions": [],
   "geometry": {"type": "LineString", "coordinates": [[13.410561161995,52.516118195692],
       [13.41015272988114,52.516163203847256],[13.40985226479664,52.51620239871635]]}},
  {"id": 483663063, "fromNodeID": 362902323, "toNodeID": 337293590,
   "fwdDirection": True, "revDirection": True, "fwdMaxSpeed": 50, "revMaxSpeed": 50, "restrictions": [],
   "geometry": {"type": "LineString", "coordinates": [[13.40985226479664,52.51620239871635],
       [13.40926026564106,52.51627962332532],[13.408681699393506,52.51636586171447]]}},
]
# Actual Berlin camera from the public practice response bbox13.415,52.52,13.425,52.53;
# source SHA256 f791aa98b9dfe9eba03790d7206c92f55498dbcd3404983cd05b83b0734761d4.
# Camera speedLimit=null is intentionally preserved: its road's limit is separate.
REAL_CAMERA = {"id":356390,"type":10,"subTypes":["RED_LIGHT","SPEED"],"segmentId":272499027,
               "direction":"REV","speedLimit":None,"scheduleId":None,"excludedRoadTypes":[],
               "geometry":{"type":"Point","coordinates":[13.420411505726902,52.525151635587285]}}


def body(segments=None):
  return json.dumps({'segments': {'objects': copy.deepcopy(REAL_SEGMENTS) if segments is None else segments}}).encode()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
  def forbidden(*_args, **_kwargs):
    pytest.fail('Waze road tests must not open sockets')
  monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
  monkeypatch.setattr(socket, 'create_connection', forbidden)


def test_real_records_have_directed_limits_geometry_and_source_provenance():
  records = parse_features(body(), bbox=BBOX, observed_at_ms=NOW_MS, source_url=features_url(BBOX))
  assert len(records) == 2
  first, second = records
  assert first.segment_id == 483663062 and first.to_node_id == second.from_node_id
  assert first.geometry[-1] == second.geometry[0]
  assert first.forward_limit_kph == first.reverse_limit_kph == 50
  assert first.forward_allowed and first.reverse_allowed and first.usable_for_control
  assert first.source == 'waze_practice_features' and first.speed_unit == 'km/h'
  assert first.bbox == BBOX and first.observed_at_ms == NOW_MS
  assert first.expires_at_ms == NOW_MS+CACHE_TTL_MS
  assert first.to_dict()['geometry'][0][0] == 13.410561161995


def test_url_is_normal_read_only_practice_request_without_credentials():
  parsed = urlparse(features_url(BBOX))
  assert parsed.scheme == 'https' and parsed.netloc == 'www.waze.com'
  assert parsed.path == '/row-Descartes/app/Features'
  assert parse_qs(parsed.query)['sandbox'] == ['true']
  assert parse_qs(parsed.query)['v'] == ['2']
  assert parse_qs(parsed.query)['cameras'] == ['true']
  assert parse_qs(parsed.query)['permanentHazards'] == ['true']
  assert not any(key in parsed.query for key in ('cookie=', 'token=', 'secret='))


@pytest.mark.parametrize('field,value', [('fwdDirection',1),('revDirection','true'),('id',True),('fromNodeID',None),
                                       ('fwdMaxSpeed',True),('revMaxSpeed',-1),('fwdMaxSpeed',301)])
def test_malformed_direction_id_or_speed_is_rejected(field, value):
  row = copy.deepcopy(REAL_SEGMENTS[0])
  row[field] = value
  with pytest.raises(ValueError):
    parse_features(body([row]), bbox=BBOX, observed_at_ms=NOW_MS)


@pytest.mark.parametrize('value', [None,0,0.0])
def test_unknown_or_unlimited_is_never_fabricated_as_limit(value):
  row = copy.deepcopy(REAL_SEGMENTS[0])
  row['fwdMaxSpeed'] = row['revMaxSpeed'] = value
  record = parse_features(body([row]), bbox=BBOX, observed_at_ms=NOW_MS)[0]
  assert record.forward_limit_kph is None and record.reverse_limit_kph is None and not record.usable_for_control


@pytest.mark.parametrize('field,value', [('restrictions',[{'timeFrame':{'from':8,'to':18}}]),
                                       ('fwdSpeedCondition','school hours'),('revMaxSpeedSchedule',{'weekdays':True})])
def test_uninterpreted_conditional_or_restricted_segments_do_not_authorize(field,value):
  row = copy.deepcopy(REAL_SEGMENTS[0])
  row[field] = value
  record = parse_features(body([row]), bbox=BBOX, observed_at_ms=NOW_MS)[0]
  assert not record.usable_for_control and record.restriction_reason == 'conditional_or_restricted'


@pytest.mark.parametrize('payload', [b'{}', b'[]', b'null', b'{"errorList":[]}', b'{"segments":{"objects":[null]}}',
                                     b'{"segments":{"objects":[]},"segments":{"objects":[]}}'])
def test_missing_error_and_duplicate_json_schema_rejected(payload):
  with pytest.raises(ValueError):
    parse_features(payload,bbox=BBOX,observed_at_ms=NOW_MS)


def test_geometry_and_conflicting_duplicate_ids_are_rejected():
  row = copy.deepcopy(REAL_SEGMENTS[0])
  row['geometry']['coordinates'][0] = [181,52.5]
  with pytest.raises(ValueError):
    parse_features(body([row]),bbox=BBOX,observed_at_ms=NOW_MS)
  row = copy.deepcopy(REAL_SEGMENTS[0])
  row['fwdMaxSpeed'] = 30
  with pytest.raises(ValueError):
    parse_features(body([REAL_SEGMENTS[0],row]),bbox=BBOX,observed_at_ms=NOW_MS)


def test_cell_batch_cache_keeps_original_age_and_refetches_on_expiry(tmp_path):
  now = [NOW_MS/1000]
  calls = []
  def transport(url,**options):
    calls.append((url,options))
    return body([])
  client = WazeRoadClient(tmp_path,transport,clock=lambda:now[0],monotonic=lambda:0.)
  first = client.fetch(52.525,13.405,50)
  assert first.complete and first.loaded_windows == first.requested_windows == 1
  assert len(calls) == 1 and calls[0][1]['max_bytes'] == 8*1024*1024
  assert first.observed_at_ms == NOW_MS
  now[0] += 10
  assert client.fetch(52.525,13.405,50).observed_at_ms == NOW_MS
  assert len(calls) == 1
  now[0] = (NOW_MS+CACHE_TTL_MS)/1000
  assert client.fetch(52.525,13.405,50).observed_at_ms == NOW_MS+CACHE_TTL_MS
  assert len(calls) == 2


def test_corrupt_or_future_cache_is_not_trusted(tmp_path):
  calls=[]
  def transport(*args,**kwargs):
    calls.append(args)
    return body([])
  client=WazeRoadClient(tmp_path,transport,clock=lambda:NOW_MS/1000,monotonic=lambda:0.)
  client.fetch(52.525,13.405,50)
  file=next(tmp_path.glob('*.roads'))
  file.write_bytes(b'{broken')
  client.fetch(52.525,13.405,50)
  os.utime(file,((NOW_MS+1000)/1000,)*2)
  client.fetch(52.525,13.405,50)
  assert len(calls)==3


def test_one_total_deadline_and_oversized_window_request(tmp_path):
  clock=[0.]
  def transport(*args,**kwargs):
    clock[0]+=10
    return body([])
  client=WazeRoadClient(tmp_path,transport,clock=lambda:NOW_MS/1000,monotonic=lambda:clock[0])
  with pytest.raises(TimeoutError):
    client.fetch(52.52,13.4,1000,timeout_s=1)
  with pytest.raises(ValueError):
    client.fetch(52.52,13.4,100000)


def test_complete_window_union_is_attached_without_losing_original_source_url(tmp_path):
  calls = []
  def transport(url, **kwargs):
    calls.append(url)
    return body()
  client = WazeRoadClient(tmp_path, transport, clock=lambda: NOW_MS/1000, monotonic=lambda: 0.)
  result = client.fetch(52.52, 13.4, 500)
  assert result.complete and result.loaded_windows == result.requested_windows > 1
  assert len(result.segments) == 2
  for segment in result.segments:
    assert segment.bbox == result.bbox
    assert segment.source_url in calls
    assert segment.expires_at_ms <= result.expires_at_ms


@pytest.mark.parametrize('change', ['speed', 'geometry', 'direction', 'node', 'street'])
def test_conflicting_repeated_segment_from_adjacent_window_fails_closed(tmp_path, change):
  calls = []
  def transport(url, **kwargs):
    rows = copy.deepcopy(REAL_SEGMENTS)
    if calls:
      if change == 'speed':
        rows[0]['fwdMaxSpeed'] = 30
      elif change == 'geometry':
        rows[0]['geometry']['coordinates'][0][0] += .00001
      elif change == 'direction':
        rows[0]['fwdDirection'] = False
      elif change == 'node':
        rows[0]['fromNodeID'] += 1
      elif change == 'street':
        rows[0]['primaryStreetID'] = 123
    calls.append(url)
    return body(rows)
  client = WazeRoadClient(tmp_path, transport, clock=lambda: NOW_MS/1000, monotonic=lambda: 0.)
  with pytest.raises(ValueError, match='across request windows'):
    client.fetch(52.52, 13.4, 500)


def link_body(cameras=None):
  payload = json.loads(body())
  payload['permanentHazards'] = {'objects': [copy.deepcopy(REAL_CAMERA)] if cameras is None else cameras}
  return json.dumps(payload).encode()


def test_real_camera_link_contains_explicit_server_segment_and_no_invented_camera_limit():
  link = parse_camera_links(link_body(),bbox=BBOX,observed_at_ms=NOW_MS,source_url=features_url(BBOX))[0]
  assert link.global_id == 356390 and link.segment_id == 272499027 and link.direction == 'REV'
  assert link.types == ('RED_LIGHT','SPEED') and link.usable_for_control
  assert link.coordinates == (13.420411505726902,52.525151635587285)
  assert link.to_dict()['latitude'] == 52.525151635587285
  assert link.source == 'waze_practice_features' and link.expires_at_ms == NOW_MS+CACHE_TTL_MS
  assert not any(key in link.to_dict() for key in ('speedLimit','speed_limit_kph','speed_raw'))


def test_older_missing_hazard_payload_and_non_camera_hazards_do_not_create_links():
  assert parse_camera_links(body(),bbox=BBOX,observed_at_ms=NOW_MS) == ()
  other = copy.deepcopy(REAL_CAMERA)
  other['type'] = 12
  assert parse_camera_links(link_body([other]),bbox=BBOX,observed_at_ms=NOW_MS) == ()
  other['type'] = 10
  other['subTypes'] = ['DUMMY']
  assert parse_camera_links(link_body([other]),bbox=BBOX,observed_at_ms=NOW_MS) == ()
  other['subTypes'] = ['SPEED']
  other['segmentId'] = None
  assert parse_camera_links(link_body([other]),bbox=BBOX,observed_at_ms=NOW_MS) == ()


@pytest.mark.parametrize('field,value',[('id',True),('segmentId',0),('geometry',{'type':'LineString','coordinates':[]}),
                                       ('subTypes','SPEED'),('geometry',{'type':'Point','coordinates':[181,52.5]})])
def test_malformed_explicit_camera_link_is_rejected(field,value):
  camera = copy.deepcopy(REAL_CAMERA)
  camera[field] = value
  with pytest.raises(ValueError):
    parse_camera_links(link_body([camera]),bbox=BBOX,observed_at_ms=NOW_MS)


@pytest.mark.parametrize('field,value',[('scheduleId',5),('excludedRoadTypes',[1])])
def test_scheduled_or_excluded_camera_is_retained_but_not_control_usable(field,value):
  camera = copy.deepcopy(REAL_CAMERA)
  camera[field] = value
  link = parse_camera_links(link_body([camera]),bbox=BBOX,observed_at_ms=NOW_MS)[0]
  assert not link.usable_for_control and link.restriction_reason == 'scheduled_or_excluded'


def test_camera_links_return_only_after_complete_window_coverage_and_conflicts_fail_closed(tmp_path):
  calls = []
  def transport(url, **kwargs):
    calls.append(url)
    return link_body()
  client = WazeRoadClient(tmp_path,transport,clock=lambda:NOW_MS/1000,monotonic=lambda:0.)
  result = client.fetch(52.52,13.4,500)
  assert result.complete and len(result.camera_links) == 1
  assert result.camera_links[0].bbox == result.bbox
  assert result.camera_links[0].source_url in calls
  # A new independent cache cannot combine contradictory global camera links.
  calls.clear()
  def conflicting(url, **kwargs):
    camera = copy.deepcopy(REAL_CAMERA)
    if calls:
      camera['segmentId'] += 1
    calls.append(url)
    return link_body([camera])
  client = WazeRoadClient(tmp_path/'other',conflicting,clock=lambda:NOW_MS/1000,monotonic=lambda:0.)
  with pytest.raises(ValueError,match='camera link across request windows'):
    client.fetch(52.52,13.4,500)


def test_cache_refresh_margin_preserves_continuous_legitimate_freshness(tmp_path):
  now = [NOW_MS/1000]
  calls = []
  def transport(url, **kwargs):
    calls.append(url)
    return body([])
  client = WazeRoadClient(tmp_path,transport,clock=lambda:now[0],monotonic=lambda:0.)
  assert client.fetch(52.525,13.405,50).observed_at_ms == NOW_MS
  now[0] += 120
  assert client.fetch(52.525,13.405,50).observed_at_ms == NOW_MS
  assert len(calls) == 1
  now[0] += 120
  refreshed = client.fetch(52.525,13.405,50)
  assert len(calls) == 2 and refreshed.observed_at_ms == NOW_MS+240000
  assert refreshed.expires_at_ms == NOW_MS+540000


@pytest.mark.parametrize('value', [None, 123])
def test_optional_primary_street_id_preserved(value):
  rows = copy.deepcopy(REAL_SEGMENTS)
  rows[0]['primaryStreetID'] = value
  records = parse_features(body(rows), bbox=BBOX, observed_at_ms=NOW_MS)
  assert records[0].primary_street_id == value
  assert records[0].to_dict()['primary_street_id'] == value
  assert records[1].primary_street_id is None


@pytest.mark.parametrize('value', [True, False, 0, -1, 1.0, '123', [], {}])
def test_malformed_primary_street_id_rejected(value):
  rows = copy.deepcopy(REAL_SEGMENTS)
  rows[0]['primaryStreetID'] = value
  with pytest.raises(ValueError, match='identifier'):
    parse_features(body(rows), bbox=BBOX, observed_at_ms=NOW_MS)
