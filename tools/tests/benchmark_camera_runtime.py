"""Synthetic host workload for the real camera spatial index/offline source.

No hardware, network, controls or provider database. Results are host evidence,
not measurements of comma hardware. Run with Python 3.12 from the repo root.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import time
import tracemalloc
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
for name, relative in [('openpilot', '.'), ('openpilot.sunnypilot', 'sunnypilot'),
                       ('openpilot.sunnypilot.mapd', 'sunnypilot/mapd')]:
  if name not in sys.modules:
    module = ModuleType(name)
    module.__path__ = [str(ROOT / relative)]
    sys.modules[name] = module

from openpilot.sunnypilot.mapd.camera_speed_source import CameraSpeedSource
from openpilot.sunnypilot.mapd.camera_osm_bridge import OfflineRoadSnapshot, RoadEdge
from openpilot.sunnypilot.mapd.speed_cameras import CameraSpatialIndex, SpeedCamera


def point(north):
  return 55 + math.degrees(north / 6_371_000), 37.


def summary(samples):
  samples = sorted(samples)
  return {'median_ms': round(statistics.median(samples) * 1000, 4),
          'p95_ms': round(samples[math.ceil(.95 * len(samples)) - 1] * 1000, 4),
          'maximum_ms': round(samples[-1] * 1000, 4)}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--iterations', type=int, default=100)
  parser.add_argument('--output', type=Path)
  args = parser.parse_args()
  if args.iterations < 10 or args.iterations > 1000:
    parser.error('iterations must be between 10 and 1000')
  tracemalloc.start()
  start = time.perf_counter()
  # The local set deliberately includes 24 eligible cameras on the corridor;
  # the remaining records are spread geographically, rather than 74k duplicates.
  cameras = [SpeedCamera(*point(900 + 150 * i), speed_limit_kph=60, direction_deg=0,
                         camera_type='speedcamonline:1', direction_type=1, source_id=f'local-{i}')
             for i in range(24)]
  for i in range(74_023 - len(cameras)):
    lat = 45 + ((i * 7919) % 200_003) / 10_000
    lon = 30 + ((i * 1543) % 500_009) / 10_000
    cameras.append(SpeedCamera(lat, lon, 60, 0, 'speedcamonline:1', 1, source_id=f'far-{i}'))
  index = CameraSpatialIndex(cameras)
  camera_index_s = time.perf_counter() - start
  start = time.perf_counter()
  edges = [RoadEdge(f'10:{i}', '10', point(-1500 + i * 50), point(-1500 + (i + 1) * 50), 50.)
           for i in range(150)]
  for i in range(10_000 - len(edges)):
    lat, lon = 55.1 + (i // 100) * .005, 37.1 + (i % 100) * .005
    edges.append(RoadEdge(f'{i+100}:0', str(i+100), (lat, lon), (lat + .0003, lon), 33.3585))
  roads = OfflineRoadSnapshot(edges, [(54., 36., 56., 38.)], point(0))
  road_index_s = time.perf_counter() - start
  _, build_peak = tracemalloc.get_traced_memory()
  tracemalloc.stop()
  queries, acquisition, retained = [], [], []
  query_candidates = len(tuple(index.query(*point(0), 5000)))
  for i in range(args.iterations):
    position = point(i * .1)
    start = time.perf_counter()
    tuple(index.query(*position, 5000))
    queries.append(time.perf_counter() - start)
    source = CameraSpeedSource()
    start = time.perf_counter()
    first = source.update(index, *position, 0., gps_valid=True,
                          fix_time_s=1_800_000_000. + i * 3, now_s=1_800_000_000. + i * 3,
                          reaction_distance_m=1000, road_graph=roads)
    assert not first.valid
    selection = source.update(index, *point(i * .1 + 3), 0., gps_valid=True,
                              fix_time_s=1_800_000_001. + i * 3, now_s=1_800_000_001. + i * 3,
                              reaction_distance_m=1000, road_graph=roads)
    acquisition.append(time.perf_counter() - start)
    assert selection.valid and selection.camera.source_id == 'local-0'
    start = time.perf_counter()
    following = source.update(index, *point(i * .1 + 4), 0., gps_valid=True,
                              fix_time_s=1_800_000_002. + i * 3, now_s=1_800_000_002. + i * 3,
                              reaction_distance_m=1000, road_graph=roads)
    retained.append(time.perf_counter() - start)
    assert following.valid and following.distance_m < selection.distance_m
  report = {'scope': 'synthetic host Python benchmark; not comma device timing',
            'python': platform.python_version(), 'machine': platform.machine(),
            'camera_records': len(cameras), 'directed_edges': len(roads.edges),
            'eligible_local_cameras': 24, 'index_query_candidates': query_candidates,
            'iterations': args.iterations,
            'startup_under_tracemalloc': {'camera_records_and_index_ms': round(camera_index_s * 1000, 2),
                                         'road_index_ms': round(road_index_s * 1000, 2), 'python_build_peak_bytes': build_peak},
            'steady_state_without_tracemalloc': {'index_query': summary(queries),
                                                'fresh_camera_acquisition': summary(acquisition),
                                                'retained_event_update': summary(retained)}}
  encoded = json.dumps(report, indent=2) + '\n'
  if args.output:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded, encoding='utf-8')
  print(encoded, end='')


if __name__ == '__main__':
  main()
