"""Synthetic independent OSM topology: no vehicle I/O or provider database."""
import math
import hashlib
import json
import time
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
for name, relative in [('openpilot', '.'), ('openpilot.sunnypilot', 'sunnypilot'), ('openpilot.sunnypilot.mapd', 'sunnypilot/mapd')]:
  if name not in sys.modules:
    module = ModuleType(name)
    module.__path__ = [str(ROOT / relative)]
    sys.modules[name] = module

from openpilot.sunnypilot.mapd.camera_road_graph import RoadGraph
from openpilot.sunnypilot.mapd.camera_speed_source import CameraSpeedSource, camera_identity, camera_gps_reliable
from openpilot.sunnypilot.mapd.speed_cameras import SpeedCamera


def point(north, east=0):
  return 55 + math.degrees(north / 6371000), 37 + math.degrees(east / (6371000 * math.cos(math.radians(55))))


def graph(tmp_path, ways, nodes=None):
  nodes = nodes or {'1': point(0), '2': point(1000)}
  xml = '<osm version="0.6"><bounds minlat="54" minlon="36" maxlat="56" maxlon="38"/>'
  xml += ''.join(f'<node id="{i}" lat="{p[0]}" lon="{p[1]}"/>' for i, p in nodes.items())
  for i, refs in enumerate(ways):
    xml += f'<way id="{i}">' + ''.join(f'<nd ref="{ref}"/>' for ref in refs)
    xml += '<tag k="highway" v="primary"/><tag k="oneway" v="yes"/></way>'
  path = tmp_path / 'roads.osm'
  path.write_text(xml + '</osm>')
  provenance(path)
  return RoadGraph.load(path)


def provenance(path, acquired=None):
  manifest = {'schema_version': 1, 'retrieved_at': time.time() if acquired is None else acquired,
              'source_url': 'https://api.openstreetmap.org/api/0.6/map?bbox=36,54,38,56',
              'complete_region_export': True, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
  Path(str(path) + '.manifest.json').write_text(json.dumps(manifest))


def camera(meters, identity='a'):
  return SpeedCamera(*point(meters), speed_limit_kph=60, direction_deg=0, direction_type=1,
                     camera_type='speedcamonline:1', source_id=identity)


def update(source, roads, cameras, north, **kwargs):
  source.test_time = getattr(source, 'test_time', 1000.) + 1
  return source.update(cameras, roads, *point(north), 0., gps_valid=True,
                       fix_time_s=source.test_time, now_s=source.test_time, **kwargs)


def test_reaction_pass_two_equal_limits_and_gps_loss(tmp_path):
  roads = graph(tmp_path, [('1', '2')])
  cameras = [camera(400), camera(800, 'b')]
  source = CameraSpeedSource()
  first = update(source, roads, cameras, 50)
  assert first.state == 'APPROACHING' and first.distance_m == pytest.approx(350)
  assert update(source, roads, cameras, 100).state == 'ACTIVE'
  lost = source.update(cameras, roads, 0., 0., 0., gps_valid=False)
  assert lost.camera_id == first.camera_id and not lost.valid and not lost.pass_proven
  # Reacquired fix alone on a parallel route cannot prove passage (other test).
  pending = update(source, roads, cameras, 430)
  assert not pending.pass_proven
  passed = update(source, roads, cameras, 440)
  assert passed.state == 'PASSED' and passed.pass_proven and passed.camera_id == first.camera_id
  second = update(source, roads, cameras, 450)
  assert second.camera_id != first.camera_id and second.camera_id == camera_identity(cameras[1])


def test_parallel_and_stacked_roads_reject(tmp_path):
  for offset in (0, 6):
    roads = graph(tmp_path, [('1', '2'), ('3', '4')],
                  {'1': point(0), '2': point(1000), '3': point(0, offset), '4': point(1000, offset)})
    selected = update(CameraSpeedSource(), roads, [camera(400)], 100)
    assert not selected.valid and selected.reason == 'no_unambiguous_camera'


def test_nonadjacent_parallel_segments_of_same_way_are_ambiguous(tmp_path):
  roads = graph(tmp_path, [('1', '2', '3', '4', '5', '6')], {
    '1': point(0), '2': point(1000), '3': point(1000, 100),
    '4': point(0, 100), '5': point(0, 6), '6': point(1000, 6)})
  assert not update(CameraSpeedSource(), roads, [camera(400)], 100).valid


def test_interchange_branch_does_not_guess_route(tmp_path):
  roads = graph(tmp_path, [('1', '2'), ('2', '3'), ('2', '4')],
                {'1': point(0), '2': point(200), '3': point(1000), '4': point(500, 200)})
  assert not update(CameraSpeedSource(), roads, [camera(400)], 100).valid


def test_connected_curve_uses_route_distance(tmp_path):
  roads = graph(tmp_path, [('1', '2', '3')], {'1': point(0), '2': point(200), '3': point(400, 50)})
  cam = SpeedCamera(*point(350, 37.5), speed_limit_kph=60, direction_deg=14, direction_type=1, camera_type='speedcamonline:1')
  selection = update(CameraSpeedSource(), roads, [cam], 100)
  assert selection.valid and selection.distance_m > 250


def test_missing_graph_and_invalid_reaction_fail_safe(tmp_path):
  source = CameraSpeedSource()
  assert not update(source, None, [camera(400)], 100).valid
  roads = graph(tmp_path, [('1', '2')])
  assert update(source, roads, [camera(400)], 50, reaction_distance_m=49).state == 'APPROACHING'
  with pytest.raises(ValueError, match='stale'):
    path = tmp_path / 'roads.osm'
    RoadGraph.load(path, now=path.stat().st_mtime + 31 * 86400)


def test_unknown_limit_and_opposing_camera_never_acquired(tmp_path):
  roads = graph(tmp_path, [('1', '2')])
  invalid = SpeedCamera(*point(400), direction_deg=0, direction_type=1, camera_type='speedcamonline:1')
  opposing = SpeedCamera(*point(400), speed_limit_kph=60, direction_deg=180, direction_type=1, camera_type='speedcamonline:1')
  assert not update(CameraSpeedSource(), roads, [invalid, opposing], 100).valid


def test_active_latches_across_threshold_jitter(tmp_path):
  roads = graph(tmp_path, [('1', '2')])
  source = CameraSpeedSource()
  assert update(source, roads, [camera(400)], 110).state == 'ACTIVE'
  assert update(source, roads, [camera(400)], 90).state == 'ACTIVE'


def test_duplicate_stale_future_backward_and_jump_fixes(tmp_path):
  roads = graph(tmp_path, [('1', '2')])
  source = CameraSpeedSource()
  cameras = [camera(400)]
  update(source, roads, cameras, 350)
  first = source.update(cameras, roads, *point(430), 0., gps_valid=True, fix_time_s=1003., now_s=1003.)
  assert not first.pass_proven
  repeated = source.update(cameras, roads, *point(440), 0., gps_valid=True, fix_time_s=1003., now_s=1003.)
  assert not repeated.pass_proven
  for fix, now in [(1002., 1003.), (1004., 1003.), (1004., 1007.)]:
    result = source.update(cameras, roads, *point(450), 0., gps_valid=True, fix_time_s=fix, now_s=now)
    assert not result.valid and not result.pass_proven
  source = CameraSpeedSource()
  update(source, roads, cameras, 100)
  jump = update(source, roads, cameras, 430)
  assert jump.reason == 'position_jump' and not jump.pass_proven


@pytest.mark.parametrize('direction_type,second', [(2, None), (1, 180)])
def test_supported_reverse_direction_retained(tmp_path, direction_type, second):
  # Use a two-way OSM road, not a manually permitted reverse ID.
  roads = graph(tmp_path, [('1', '2')])
  path = tmp_path / 'roads.osm'
  path.write_text(path.read_text().replace('k="oneway" v="yes"', 'k="oneway" v="no"'))
  provenance(path)
  roads = RoadGraph.load(path)
  cam = SpeedCamera(*point(400), speed_limit_kph=60, direction_deg=0, direction_type=direction_type,
                    second_direction_deg=second, camera_type='speedcamonline:1')
  source = CameraSpeedSource()
  selected = source.update([cam], roads, *point(600), 180., gps_valid=True, fix_time_s=1000., now_s=1000.)
  assert selected.valid and selected.distance_m == pytest.approx(200)
  source.update([cam], roads, *point(370), 180., gps_valid=True, fix_time_s=1004., now_s=1004.)
  # The >100m jump is rejected. Two subsequent ordered behind observations prove pass.
  first = source.update([cam], roads, *point(360), 180., gps_valid=True, fix_time_s=1005., now_s=1005.)
  assert not first.pass_proven
  second = source.update([cam], roads, *point(350), 180., gps_valid=True, fix_time_s=1006., now_s=1006.)
  assert second.pass_proven


def test_provenance_old_copy_and_removed_competing_way_rejected(tmp_path):
  graph(tmp_path, [('1', '2')])
  path = tmp_path / 'roads.osm'
  provenance(path, time.time() - 31 * 86400)
  path.touch()  # Copying or touching data cannot rejuvenate recorded acquisition.
  with pytest.raises(ValueError, match='stale'):
    RoadGraph.load(path)
  provenance(path)
  path.write_text(path.read_text().replace('primary', 'residential'))
  with pytest.raises(ValueError, match='provenance'):
    RoadGraph.load(path)
  provenance(path)
  manifest_path = Path(str(path) + '.manifest.json')
  manifest = json.loads(manifest_path.read_text())
  manifest['complete_region_export'] = False
  manifest_path.write_text(json.dumps(manifest))
  with pytest.raises(ValueError, match='provenance'):
    RoadGraph.load(path)


@pytest.mark.parametrize('changes', [dict(horizontalAccuracy=13), dict(horizontalAccuracy=0),
  dict(bearingAccuracyDeg=16), dict(speed=.5), dict(unixTimestampMillis=997000),
  dict(unixTimestampMillis=1001000), dict(latitude=91), dict(longitude=float('nan')), dict(hasFix=False)])
def test_raw_gps_quality_is_camera_specific(changes):
  gps = dict(latitude=55., longitude=37., bearingDeg=0., horizontalAccuracy=5.,
             bearingAccuracyDeg=5., speed=10., unixTimestampMillis=1000000, hasFix=True)
  assert camera_gps_reliable(SimpleNamespace(**gps), 1000.)
  gps.update(changes)
  assert not camera_gps_reliable(SimpleNamespace(**gps), 1000.)
