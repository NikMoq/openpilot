"""Automatic complete geometry acquisition; synthetic HTTP, real graph/source."""
import hashlib
import json
import math
import os
import sys
import threading
from pathlib import Path
from types import ModuleType
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parents[2]
for name, relative in [('openpilot', '.'), ('openpilot.sunnypilot', 'sunnypilot'), ('openpilot.sunnypilot.mapd', 'sunnypilot/mapd')]:
  if name not in sys.modules:
    module = ModuleType(name)
    module.__path__ = [str(ROOT / relative)]
    sys.modules[name] = module

from openpilot.sunnypilot.mapd.camera_road_graph import Edge, RoadGraph
from openpilot.sunnypilot.mapd.camera_road_graph_updater import RoadGraphUpdater, region_bounds
from openpilot.sunnypilot.mapd.camera_speed_source import CameraSpeedSource
from openpilot.sunnypilot.mapd.speed_cameras import SpeedCamera

NOW = 1_800_000_000.


def export(url, path):
  b, a, d, c = map(float, parse_qs(urlparse(url).query)['bbox'][0].split(','))
  south = 55 - math.degrees(2000 / 6371000)
  north = 55 + math.degrees(2000 / 6371000)
  raw = (f'<osm><bounds minlat="{a}" minlon="{b}" maxlat="{c}" maxlon="{d}"/>'
         f'<node id="1" lat="{south}" lon="37"/><node id="2" lat="{north}" lon="37"/>'
         '<way id="10"><nd ref="1"/><nd ref="2"/>'
         '<tag k="highway" v="primary"/><tag k="oneway" v="yes"/></way></osm>').encode()
  Path(path).write_bytes(raw)
  Path(str(path) + '.manifest.json').write_text(json.dumps({
    'schema_version': 1, 'source_url': url, 'retrieved_at': NOW, 'complete_region_export': True,
    'sha256': hashlib.sha256(raw).hexdigest()}))


def finish(updater):
  updater._worker.join(timeout=5)
  assert not updater._worker.is_alive()


def test_missing_graph_downloads_and_selects_actual_camera_offline_restart(tmp_path):
  downloads = []
  def download(url, path):
    downloads.append(url)
    export(url, path)
  updater = RoadGraphUpdater(tmp_path, acquire=download, clock=lambda: NOW)
  assert updater.tick(55., 37., 0., 900)
  finish(updater)
  assert updater.status == 'ready' and len(downloads) == 1
  assert updater.graph.inside((55., 37.), margin=1100)
  camera = SpeedCamera(55 + math.degrees(900 / 6371000), 37, speed_limit_kph=60,
                       direction_deg=0, direction_type=1, camera_type='speedcamonline:1', source_id='real-parser-shape')
  source = CameraSpeedSource()
  selected = source.update([camera], updater.graph, 55., 37., 0., gps_valid=True,
                           fix_time_s=NOW, now_s=NOW, reaction_distance_m=900)
  assert selected.valid and selected.distance_m == pytest.approx(900)
  assert selected.camera.speed_limit_kph == 60
  def no_network(*args):
    raise AssertionError('verified offline cache must avoid download')
  restart = RoadGraphUpdater(tmp_path, acquire=no_network, clock=lambda: NOW + 30)
  assert restart.tick(55., 37.)
  finish(restart)
  assert restart.graph is not None and restart.status == 'ready'
  assert restart.graph.camera_distance((55., 37.), 0., (camera.latitude, camera.longitude), 0.) == pytest.approx(900)


def test_worker_never_blocks_mapd_tick_and_single_worker(tmp_path):
  entered, release = threading.Event(), threading.Event()
  def download(url, path):
    entered.set()
    assert release.wait(5)
    export(url, path)
  updater = RoadGraphUpdater(tmp_path, acquire=download, clock=lambda: NOW)
  try:
    assert updater.tick(55., 37.)
    assert entered.wait(2)
    assert not updater.tick(55., 37.) and updater.graph is None
  finally:
    release.set()
    finish(updater)
  assert updater.graph is not None


@pytest.mark.parametrize('limit', ['nodes', 'bytes'])
def test_node_limit_splits_complete_region_and_keeps_each_provenance(tmp_path, limit):
  requests = []
  def download(url, path):
    requests.append(url)
    if len(requests) == 1:
      if limit == 'bytes':
        raise ValueError('map export exceeds size limit')
      raise HTTPError(url, 400, 'too many nodes', {}, None)
    export(url, path)
  updater = RoadGraphUpdater(tmp_path, acquire=download, clock=lambda: NOW)
  updater.tick(55., 37.)
  finish(updater)
  assert updater.status == 'ready' and len(requests) == 5
  assert len(list(tmp_path.glob('region-*/*.osm'))) == 4
  index = json.loads(next(tmp_path.glob('region-*/region.json')).read_text())
  assert len(index['parts']) == 4
  assert updater.graph.bounds == region_bounds(55., 37.)
  assert len(updater.graph.edges) == 1  # Shared full ways are deduplicated.


def test_partial_region_never_published_and_retry_backoff(tmp_path):
  calls = []
  def download(url, path):
    calls.append(url)
    if len(calls) == 1:
      raise HTTPError(url, 400, 'too many nodes', {}, None)
    if len(calls) == 3:
      raise OSError('offline')
    export(url, path)
  updater = RoadGraphUpdater(tmp_path, acquire=download, clock=lambda: NOW, monotonic=lambda: 100.)
  updater.tick(55., 37.)
  finish(updater)
  assert updater.graph is None and updater.status == 'download_error'
  assert not list(tmp_path.glob('region-*/region.json'))
  assert not updater.tick(55., 37.)


def test_stale_cached_region_and_corrupt_source_fail_closed(tmp_path):
  updater = RoadGraphUpdater(tmp_path, acquire=export, clock=lambda: NOW)
  updater.tick(55., 37.)
  finish(updater)
  updater.clock = lambda: NOW + 31 * 86400
  assert updater.graph is None
  next(tmp_path.glob('region-*/*.osm')).write_text('<osm/>')
  restart = RoadGraphUpdater(tmp_path, acquire=lambda *a: (_ for _ in ()).throw(OSError('offline')), clock=lambda: NOW)
  restart.tick(55., 37.)
  finish(restart)
  assert restart.graph is None and restart.status == 'download_error'


def test_complete_union_rejects_gap_and_geometry_conflict():
  edge = Edge('1:0:a>b', 'a', 'b', (0., 0.), (1., 0.), '0')
  left = RoadGraph([edge], (0., 0., 1., .4), NOW)
  right = RoadGraph([edge], (0., .6, 1., 1.), NOW)
  with pytest.raises(ValueError, match='incomplete_region'):
    RoadGraph.combine_complete([left, right], (0., 0., 1., 1.))
  right = RoadGraph([Edge(edge.key, 'a', 'b', (0., 0.), (.9, 0.), '0')], (0., .4, 1., 1.), NOW)
  with pytest.raises(ValueError, match='conflicting_exports'):
    RoadGraph.combine_complete([left, right], (0., 0., 1., 1.))


def test_moving_outside_cached_coverage_offline_does_not_invent_region(tmp_path):
  updater = RoadGraphUpdater(tmp_path, acquire=export, clock=lambda: NOW)
  updater.tick(55., 37.)
  finish(updater)
  updater.acquire = lambda *a: (_ for _ in ()).throw(OSError('offline'))
  updater._next_attempt = 0
  updater.tick(55.1, 37.)
  finish(updater)
  assert updater.status == 'download_error'
  assert updater.graph is not None and not updater.graph.inside((55.1, 37.))


def test_restart_offline_near_cached_edge_retains_usable_geometry(tmp_path):
  first = RoadGraphUpdater(tmp_path, acquire=export, clock=lambda: NOW)
  first.tick(55., 37.)
  finish(first)
  point = (55 + math.degrees(1800 / 6371000), 37.)
  assert first.graph.inside(point) and not first.graph.inside(point, margin=1100)
  restart = RoadGraphUpdater(tmp_path, acquire=lambda *a: (_ for _ in ()).throw(OSError('offline')), clock=lambda: NOW)
  restart.tick(*point)
  finish(restart)
  assert restart.graph is not None and restart.graph.inside(point)
  assert restart.status == 'offline_cache'


def test_unrelated_cache_indexes_do_not_parse_xml(tmp_path, monkeypatch):
  first = RoadGraphUpdater(tmp_path, acquire=export, clock=lambda: NOW)
  first.tick(55., 37.)
  finish(first)
  # An unrelated region must be skipped from metadata, even if its XML is bad.
  unrelated = tmp_path / 'region-unrelated'
  unrelated.mkdir()
  (unrelated / 'region.json').write_text(json.dumps({'bounds': [30, 30, 31, 31], 'parts': ['bad.osm']}))
  restart = RoadGraphUpdater(tmp_path, acquire=export, clock=lambda: NOW)
  original = restart._load_region
  loaded = []
  def load(directory):
    loaded.append(directory)
    return original(directory)
  monkeypatch.setattr(restart, '_load_region', load)
  restart.tick(55., 37.)
  finish(restart)
  assert len(loaded) == 1 and unrelated not in loaded


@pytest.mark.skipif(not hasattr(os, 'sched_setscheduler'), reason='Linux thread scheduling contract')
def test_worker_demotes_only_its_own_thread_before_acquisition(tmp_path, monkeypatch):
  parent_tid = threading.get_native_id()
  observed = []
  def demote(tid, policy, param):
    observed.append(('scheduler', tid, policy, param.sched_priority))
  def nice(which, tid, value):
    observed.append(('nice', tid, value))
  def download(url, path):
    assert observed[0] == ('scheduler', threading.get_native_id(), os.SCHED_OTHER, 0)
    assert observed[1] == ('nice', threading.get_native_id(), 10)
    export(url, path)
  monkeypatch.setattr(os, 'sched_setscheduler', demote)
  monkeypatch.setattr(os, 'setpriority', nice)
  updater = RoadGraphUpdater(tmp_path, acquire=download, clock=lambda: NOW)
  updater.tick(55., 37.)
  finish(updater)
  assert updater.status == 'ready' and updater.graph is not None
  assert all(call[1] != parent_tid for call in observed)


@pytest.mark.skipif(not hasattr(os, 'sched_setscheduler'), reason='Linux thread scheduling contract')
def test_worker_refuses_heavy_work_if_inherited_realtime_policy_cannot_be_removed(tmp_path, monkeypatch):
  def denied(*args):
    raise PermissionError('scheduler blocked')
  def no_network(*args):
    raise AssertionError('realtime worker must not perform acquisition')
  monkeypatch.setattr(os, 'sched_setscheduler', denied)
  monkeypatch.setattr(os, 'sched_getscheduler', lambda tid: os.SCHED_FIFO)
  updater = RoadGraphUpdater(tmp_path, acquire=no_network, clock=lambda: NOW)
  updater.tick(55., 37.)
  finish(updater)
  assert updater.status == 'worker_priority_error' and updater.graph is None
  assert not tmp_path.exists() or not list(tmp_path.iterdir())


@pytest.mark.parametrize('lat,lon', [(float('nan'), 37), (55, float('inf')), (90, 37), (55, 180)])
def test_invalid_positions_do_not_create_worker(tmp_path, lat, lon):
  updater = RoadGraphUpdater(tmp_path, acquire=export)
  assert not updater.tick(lat, lon)
  assert updater._worker is None
