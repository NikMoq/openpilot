#!/usr/bin/env python3
"""Read only GPS/LLK/clock fields from a user-provided raw rlog for offline QA.

Uses current camera modules, current provider bytes and a freshly acquired full
OSM region. Coordinates/raw records are never printed or included in the report.
No vehicle I/O. This is source/association replay, not full vehicle/MPC replay.
"""
import argparse
import bisect
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time
from types import SimpleNamespace
from urllib.parse import urlencode

from cereal import log
from openpilot.sunnypilot.mapd.camera_database import fetch_snapshot
from openpilot.sunnypilot.mapd.camera_road_graph import RoadGraph, acquire_map_export
from openpilot.sunnypilot.mapd.camera_source import MIRROR_MANIFEST_URL, MIRROR_RELEASE_BASE, _parse_manifest
from openpilot.sunnypilot.mapd.camera_speed_source import CameraSpeedSource, camera_gps_reliable
from openpilot.sunnypilot.mapd.cityguide import parse_cityguide_snapshot
from openpilot.sunnypilot.mapd.speed_cameras import CameraSpatialIndex, find_nearest_camera


def utc(value):
  return datetime.fromtimestamp(value, timezone.utc).isoformat()


def read_inputs(path):
  gps_records, llk_records, clocks = [], [], []
  fields = ('latitude', 'longitude', 'bearingDeg', 'horizontalAccuracy', 'bearingAccuracyDeg',
            'speed', 'unixTimestampMillis', 'hasFix')
  with path.open('rb') as stream:
    for event in log.Event.read_multiple(stream):
      kind = event.which()
      mono = event.logMonoTime * 1e-9
      if kind in ('gpsLocation', 'gpsLocationExternal'):
        gps = getattr(event, kind)
        gps_records.append((mono, bool(event.valid), SimpleNamespace(**{field: getattr(gps, field) for field in fields})))
      elif kind == 'liveLocationKalman':
        llk = event.liveLocationKalman
        reliable = bool(event.valid and str(llk.status) == 'valid' and llk.gpsOK and
                        llk.positionGeodetic.valid and llk.calibratedOrientationNED.valid)
        llk_records.append((mono, reliable))
      elif kind == 'clocks' and event.clocks.wallTimeNanos:
        clocks.append((mono, event.clocks.wallTimeNanos * 1e-9 - mono))
  return gps_records, llk_records, clocks


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--rlog', type=Path, required=True)
  parser.add_argument('--graph', type=Path, required=True)
  parser.add_argument('--acquire-graph', action='store_true')
  parser.add_argument('--graph-margin-m', type=float, default=5500,
                      help='default covers5km lookahead; smaller exports must not be called complete route validation')
  parser.add_argument('--report', type=Path, required=True, help='aggregate JSON; never raw GPS')
  parser.add_argument('--reaction-distance', type=int, default=300)
  args = parser.parse_args()
  if not math.isfinite(args.graph_margin_m) or not 100 <= args.graph_margin_m <= 6000:
    parser.error('graph margin must be finite and between100 and6000m')
  if args.reaction_distance not in range(50, 1001, 50):
    parser.error('reaction distance must be50..1000m in50m increments')
  gps_records, llk_records, clocks = read_inputs(args.rlog)
  usable = [(mono, gps) for mono, valid, gps in gps_records if valid and camera_gps_reliable(gps, gps.unixTimestampMillis * .001)]
  if not usable or not clocks:
    raise ValueError('recording needs reliable moving raw GPS and recorded Unix/monotonic clock anchors')
  if args.acquire_graph:
    latitudes = [gps.latitude for _, gps in usable]
    longitudes = [gps.longitude for _, gps in usable]
    margin_lat = math.degrees(args.graph_margin_m / 6371000)
    margin_lon = margin_lat / math.cos(math.radians(sum(latitudes) / len(latitudes)))
    bbox = (min(longitudes) - margin_lon, min(latitudes) - margin_lat,
            max(longitudes) + margin_lon, max(latitudes) + margin_lat)
    # Actual unfiltered map API export; no manually trusted road identifiers.
    url = 'https://api.openstreetmap.org/api/0.6/map?' + urlencode({'bbox': ','.join(map(str, bbox))})
    acquire_map_export(url, args.graph)
  graph = RoadGraph.load(args.graph)
  manifest = _parse_manifest(fetch_snapshot(MIRROR_MANIFEST_URL, max_bytes=65536), 64 * 1024 * 1024)
  raw = fetch_snapshot(MIRROR_RELEASE_BASE + manifest.asset_name)
  if len(raw) != manifest.size_bytes or hashlib.sha256(raw).hexdigest() != manifest.sha256:
    raise ValueError('provider bytes fail current mirror integrity contract')
  cameras = tuple(parse_cityguide_snapshot(raw))
  if len(cameras) != manifest.record_count:
    raise ValueError('provider record count mismatch')
  index = CameraSpatialIndex(cameras)
  source = CameraSpeedSource()
  clock_times = [entry[0] for entry in clocks]
  llk_times = [entry[0] for entry in llk_records]
  states, reasons, unique_events = Counter(), Counter(), set()
  ages, selected_distances = [], []
  association = Counter()
  # Preserve every recorded relative delta/duplicate/order, while replaying
  # freshness against an explicitly rebased virtual Unix clock. Acquisition
  # metadata of current map/camera data is never rewritten to the old date.
  rebase_ms = int(time.time() * 1000) - gps_records[0][2].unixTimestampMillis
  next_tick = gps_records[0][0]
  reliable_ticks = passed_events = 0
  for mono, message_valid, gps in gps_records:
    if mono < next_tick:
      continue
    while next_tick <= mono:
      next_tick += 1.0  # Fixed1Hz schedule; never count a gap as repeated fresh fixes.
    anchor = max(0, bisect.bisect_right(clock_times, mono) - 1)
    now = mono + clocks[anchor][1]
    ages.append(now - gps.unixTimestampMillis * .001)
    llk_index = bisect.bisect_right(llk_times, mono) - 1
    llk_ok = llk_index >= 0 and mono - llk_records[llk_index][0] <= .5 and llk_records[llk_index][1]
    reliable = bool(message_valid and llk_ok and camera_gps_reliable(gps, now))
    reliable_ticks += int(reliable)
    if reliable:
      association['vehicle_associated_ticks' if graph.associate((gps.latitude, gps.longitude), gps.bearingDeg) else
                  'vehicle_ambiguous_or_unmapped_ticks'] += 1
      eligible = [camera for camera in index.query(gps.latitude, gps.longitude, 5000) if camera.allowed_auto_control]
      nearest = find_nearest_camera(eligible, gps.latitude, gps.longitude, gps.bearingDeg)
      association['directional_candidate_ticks' if nearest is not None else 'no_directional_candidate_ticks'] += 1
      if nearest is not None and not graph.inside((nearest.camera.latitude, nearest.camera.longitude)):
        association['nearest_candidate_outside_export_ticks'] += 1
      if nearest is not None:
        camera = nearest.camera
        directions = [camera.direction_deg] if camera.direction_deg is not None else []
        if camera.second_direction_deg is not None:
          directions.append(camera.second_direction_deg)
        if camera.direction_type == 2:
          directions += [(direction + 180) % 360 for direction in tuple(directions)]
        if camera.direction_type == 0:
          directions = [gps.bearingDeg]
        compatible = [direction for direction in directions if abs((direction - gps.bearingDeg + 180) % 360 - 180) <= 60]
        if compatible:
          travel = min(compatible, key=lambda direction: abs((direction - gps.bearingDeg + 180) % 360 - 180))
          if graph.associate((camera.latitude, camera.longitude), travel) is None:
            association['nearest_camera_projection_ambiguous_or_unmapped_ticks'] += 1
          elif graph.camera_distance((gps.latitude, gps.longitude), gps.bearingDeg,
                                     (camera.latitude, camera.longitude), travel) is None:
            association['nearest_camera_not_on_unique_vehicle_corridor_ticks'] += 1
    replay_gps = SimpleNamespace(**vars(gps))
    replay_gps.unixTimestampMillis += rebase_ms
    result = source.update(index, graph, gps.latitude, gps.longitude, gps.bearingDeg,
                           gps_valid=reliable, fix_time_s=replay_gps.unixTimestampMillis * .001,
                           now_s=now + rebase_ms * .001, reaction_distance_m=args.reaction_distance)
    states[result.state] += 1
    reasons[result.reason] += 1
    if result.valid:
      unique_events.add(result.camera_id)
      selected_distances.append(result.distance_m)
    passed_events += int(result.pass_proven)
  report = dict(input_sha256=hashlib.sha256(args.rlog.read_bytes()).hexdigest(),
                input_bytes=args.rlog.stat().st_size, gps_samples=len(gps_records), reliable_raw_samples=len(usable),
                llk_samples=len(llk_records), clock_anchors=len(clocks),
                utc_start=utc(gps_records[0][2].unixTimestampMillis * .001),
                utc_end=utc(gps_records[-1][2].unixTimestampMillis * .001),
                producer_ticks=sum(states.values()), reliable_ticks=reliable_ticks,
                reaction_distance_m=args.reaction_distance,
                clock_mode='constant Unix rebase; original fix deltas/duplicates/order and transport ages preserved',
                replay_reference_utc=utc(gps_records[0][2].unixTimestampMillis * .001 + rebase_ms * .001),
                gps_age_range_s=[min(ages), max(ages)], graph_edges=len(graph.edges),
                graph_sha256=hashlib.sha256(args.graph.read_bytes()).hexdigest(),
                camera_sha256=manifest.sha256, camera_count=len(cameras), camera_acquired_utc=utc(manifest.fetched_at),
                states=dict(states), reasons=dict(reasons), association=dict(association), unique_selected_events=len(unique_events),
                passed_events=passed_events,
                selected_distance_range_m=[min(selected_distances), max(selected_distances)] if selected_distances else None,
                source_sha256={name: hashlib.sha256((Path(__file__).resolve().parents[1] / name).read_bytes()).hexdigest()
                               for name in ('sunnypilot/mapd/camera_speed_source.py', 'sunnypilot/mapd/camera_road_graph.py')},
                limitations=['current map/provider data replayed against older recorded positions',
                             'source/road-association replay only; no full controller/model/MPC/physical CAN execution',
                             'zero selected events cannot validate approach/pass/restoration behavior'])
  args.report.write_text(json.dumps(report, indent=2) + '\n')
  print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
  main()
