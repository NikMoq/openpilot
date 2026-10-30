"""Prepare and retain complete local camera road geometry without blocking mapd.

Only ordinary unfiltered OSM map responses are cached. Online acquisition is
independent of camera control; downloaded regions keep working offline. A failed
or incomplete download never creates permission to acquire a camera.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import threading
import time
from urllib.error import HTTPError
import xml.etree.ElementTree as ET

from openpilot.sunnypilot.mapd.camera_road_graph import R, RoadGraph, acquire_map_export

MAX_AGE_S = 30 * 86400
MAX_CACHE_BYTES = 256 * 1024 * 1024
MAX_CACHE_REGIONS = 64


def region_bounds(latitude, longitude):
  # Snapping avoids a new download for every fix. Half-width 2.5 km leaves
  # enough coverage for the maximum 1 km reaction distance after snapping.
  lat = round(latitude / .005) * .005
  lon = round(longitude / .01) * .01
  dy = math.degrees(2500 / R)
  dx = dy / max(.1, math.cos(math.radians(lat)))
  return tuple(round(v, 7) for v in (max(-89.9, lat - dy), max(-179.9, lon - dx),
                                     min(89.9, lat + dy), min(179.9, lon + dx)))


def region_key(bounds):
  return hashlib.sha256(json.dumps(bounds).encode()).hexdigest()[:24]


def map_url(bounds):
  a, b, c, d = bounds
  return f'https://api.openstreetmap.org/api/0.6/map?bbox={b:.7f},{a:.7f},{d:.7f},{c:.7f}'


class RoadGraphUpdater:
  def __init__(self, cache_root, *, acquire=None, clock=time.time, monotonic=time.monotonic):
    self.root = Path(cache_root)
    self.acquire = acquire or (lambda url, path: acquire_map_export(url, path, timeout=15,
                                                                   max_bytes=16 * 1024 * 1024, allow_empty=True))
    self.clock, self.monotonic = clock, monotonic
    self._graph = None
    self.status = 'waiting_for_position'
    self._worker = None
    self._next_attempt = 0.
    self._lock = threading.Lock()

  @property
  def graph(self):
    graph = self._graph
    return graph if graph is not None and 0 <= self.clock() - graph.acquired_at <= MAX_AGE_S else None

  def tick(self, latitude, longitude, heading=0., reaction_distance_m=1000):
    """Return immediately; at most one disk/network worker exists at a time."""
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (latitude, longitude)) or not (
        -89.8 < latitude < 89.8 and -179.8 < longitude < 179.8):
      self.status = 'waiting_for_position'
      return False
    graph = self.graph
    # Refresh before the camera corridor reaches the edge of a cached region.
    # Heading is accepted for integration compatibility, not guessed as a route.
    if graph is not None and graph.inside((latitude, longitude), margin=1600):
      self.status = 'ready'
      return False
    with self._lock:
      if self._worker is not None and self._worker.is_alive():
        return False
      if self.monotonic() < self._next_attempt:
        return False
      self.status = 'loading'
      self._worker = threading.Thread(target=self._run, args=((latitude, longitude),),
                                      name='camera-road-updater', daemon=True)
      self._worker.start()
      return True

  def _load_region(self, directory):
    record = json.loads((directory / 'region.json').read_text(encoding='utf-8'))
    bounds = tuple(record['bounds'])
    parts = record['parts']
    if (record.get('schema_version') != 1 or not 1 <= len(parts) <= 16 or
        any(not isinstance(p, str) or not p.endswith('.osm') or Path(p).name != p for p in parts)):
      raise ValueError('road_graph_invalid_cache_index')
    graphs = [RoadGraph.load(directory / part, now=self.clock(), allow_empty=True) for part in parts]
    return RoadGraph.combine_complete(graphs, bounds)

  def _cached(self, point):
    # All cache work happens in this worker, including restart recovery offline.
    # Read small indexes first, never parse every cached regional XML graph.
    candidates = []
    for directory in self.root.glob('region-*'):
      try:
        index = directory / 'region.json'
        if index.stat().st_size > 16384:
          continue
        record = json.loads(index.read_text(encoding='utf-8'))
        bounds = record['bounds']
        if (len(bounds) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in bounds) or
            not (-90 <= bounds[0] < bounds[2] <= 90 and -180 <= bounds[1] < bounds[3] <= 180)):
          continue
        if RoadGraph((), bounds, 0).inside(point):
          candidates.append((index.stat().st_mtime, directory))
      except (OSError, ValueError, KeyError, TypeError, ET.ParseError):
        continue
    for _, directory in sorted(candidates, reverse=True):
      try:
        return self._load_region(directory)
      except (OSError, ValueError, KeyError, TypeError, ET.ParseError):
        continue
    return None

  def _download(self, bounds, directory, depth=0, label='map'):
    if self.monotonic() - self._started > 90:
      raise TimeoutError('road graph acquisition budget exhausted')
    if sum(p.stat().st_size for p in directory.iterdir() if p.is_file()) > 128 * 1024 * 1024:
      raise ValueError('road_graph_region_cache_budget')
    path = directory / (label + '.osm')
    try:
      self.acquire(map_url(bounds), path)
      graph = RoadGraph.load(path, now=self.clock(), allow_empty=True)
      if any(abs(a - b) > 1e-6 for a, b in zip(graph.bounds, bounds)):
        raise ValueError('road_graph_cache_bounds_mismatch')
      return [path.name], [graph]
    except (HTTPError, ValueError) as error:
      # The official map endpoint rejects >50,000 nodes with HTTP400. Split
      # into complete rectangles; never filter out competing roads to fit.
      splittable = (isinstance(error, HTTPError) and error.code == 400 or
                    isinstance(error, ValueError) and str(error) == 'map export exceeds size limit')
      if not splittable or depth >= 2:
        raise
    a, b, c, d = bounds
    y, x = round((a + c) / 2, 7), round((b + d) / 2, 7)
    parts, graphs = [], []
    for i, child in enumerate(((a, b, y, x), (a, x, y, d), (y, b, c, x), (y, x, c, d))):
      names, loaded = self._download(child, directory, depth + 1, label + str(i))
      parts.extend(names)
      graphs.extend(loaded)
    return parts, graphs

  def _run(self, point):
    # mapd can itself run under FIFO. Linux threads inherit that policy, where
    # nice has no effect; explicitly demote only this worker's native thread.
    if hasattr(os, 'sched_setscheduler'):
      try:
        tid = threading.get_native_id()
        try:
          os.sched_setscheduler(tid, os.SCHED_OTHER, os.sched_param(0))
        except OSError:
          if os.sched_getscheduler(tid) in (os.SCHED_FIFO, os.SCHED_RR):
            raise
      except OSError:
        self.status = 'worker_priority_error'
        self._next_attempt = self.monotonic() + 120
        return  # Never parse/download while this worker is still realtime.
    try:
      # Linux schedules this worker thread below the driving/control threads.
      # No process-wide priority change is made.
      os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
    except (AttributeError, OSError):
      pass
    self._started = self.monotonic()
    destination = None
    try:
      self.root.mkdir(parents=True, exist_ok=True)
      cached = self._cached(point)
      if cached is not None:
        self._graph = cached
        if cached.inside(point, margin=1600):
          self.status = 'ready'
          self._next_attempt = self.monotonic() + 30
          return
      bounds = region_bounds(*point)
      destination = self.root / ('region-' + region_key(bounds))
      destination.mkdir(exist_ok=True)
      self.status = 'downloading'
      parts, graphs = self._download(bounds, destination)
      graph = RoadGraph.combine_complete(graphs, bounds)
      # Publish a complete index only after every source export was validated.
      temporary = destination / 'region.json.tmp'
      temporary.write_text(json.dumps({'schema_version': 1, 'bounds': bounds, 'parts': parts,
                                       'acquired_at': graph.acquired_at}), encoding='utf-8')
      os.replace(temporary, destination / 'region.json')
      self._graph = graph
      self.status = 'ready'
      self._next_attempt = self.monotonic() + 30
      self._prune(destination)
    except Exception as error:
      # Keep existing verified data during network failures. Do not expose URLs
      # or coordinates in the status; source independently checks graph bounds.
      self.status = 'offline_cache' if self.graph is not None and self.graph.inside(point) else 'download_error'
      self._next_attempt = self.monotonic() + (120 if isinstance(error, HTTPError) and error.code == 429 else 30)
    finally:
      try:
        self._prune(destination)
      except OSError:
        pass

  def _prune(self, preserve):
    regions = sorted(self.root.glob('region-*'), key=lambda p: p.stat().st_mtime)
    sizes = {p: sum(f.stat().st_size for f in p.iterdir() if f.is_file()) for p in regions}
    total = sum(sizes.values())
    count = len(regions)
    for path in regions:
      if count <= MAX_CACHE_REGIONS and total <= MAX_CACHE_BYTES:
        break
      if path == preserve or path.is_symlink() or path.parent.resolve() != self.root.resolve():
        continue
      shutil.rmtree(path)
      total -= sizes[path]
      count -= 1
