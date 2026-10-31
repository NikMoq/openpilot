"""Read-only local bridge to installed mapd packed Cap'n Proto road tiles.

No downloader, map speed-limit reader, or country-wide scan exists here. Tile
nodes contain coordinates but no original OSM node/way IDs or layer metadata;
connectivity is derived from exact shared coordinates and ambiguous choices are
rejected, not represented as proof of grade-separated road identity.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import threading
import time

from openpilot.sunnypilot.mapd.camera_speed_source import xy, angle_difference

MAX_TILE_BYTES = 16 * 1024 * 1024
MAX_CACHED_BYTES = 32 * 1024 * 1024
MAX_NEARBY_TILES = 16
MAX_LOCAL_EDGES = 40_000
MAX_INDEX_REFERENCES = 250_000
MAX_DIRECTORY_ENTRIES = 20_000
SEARCH_RADIUS_M = 1800.
CROP_RADIUS_M = 2300.


def bounding_box(point, radius):
  lat, lon = point
  dy = math.degrees(radius / 6371000)
  dx = dy / max(.01, math.cos(math.radians(lat)))
  return lat-dy, lon-dx, lat+dy, lon+dx


def inside(bounds, point):
  return bounds[0] <= point[0] <= bounds[2] and bounds[1] <= point[1] <= bounds[3]


def intersects(a, b):
  return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


@dataclass(frozen=True)
class RoadEdge:
  key: str
  way: str
  start: tuple
  end: tuple
  length: float


class OfflineRoadSnapshot:
  def __init__(self, edges, bounds, anchor):
    self.edges = tuple(edges)
    self.bounds = tuple(bounds)
    self.anchor = anchor
    self.cells = {}
    self.outgoing = {}
    self._association_cache = OrderedDict()
    self._route_key = None
    self._route_entries = {}
    index_references = 0
    for edge in self.edges:
      self.outgoing.setdefault(edge.start, []).append(edge)
      low = [math.floor(min(edge.start[i], edge.end[i])/.002) for i in (0, 1)]
      high = [math.floor(max(edge.start[i], edge.end[i])/.002) for i in (0, 1)]
      references = math.prod(high[i]-low[i]+1 for i in (0, 1))
      if references > 4096:
        raise ValueError('offline_map_long_segment')
      index_references += references
      if index_references > MAX_INDEX_REFERENCES:
        raise ValueError('offline_map_index_budget')
      for lat in range(low[0], high[0]+1):
        for lon in range(low[1], high[1]+1):
          self.cells.setdefault((lat, lon), []).append(edge)

  def inside(self, point):
    return any(inside(bounds, point) for bounds in self.bounds)

  def associate(self, point, heading, tolerance=12.):
    # Static camera projections recur for every GPS fix. Keep a small LRU;
    # snapshot geometry never changes, so a cached refusal is equally stable.
    key = (tuple(point),heading,tolerance)
    if key in self._association_cache:
      value = self._association_cache.pop(key)
      self._association_cache[key] = value
      return value
    value = self._associate_uncached(point,heading,tolerance)
    self._association_cache[key] = value
    if len(self._association_cache) > 256:
      self._association_cache.popitem(last=False)
    return value

  def _associate_uncached(self, point, heading, tolerance=12.):
    if not self.inside(point):
      return None
    lat, lon = point
    dy = math.degrees(tolerance / 6371000)
    dx = dy / max(.01, math.cos(math.radians(lat)))
    edges = {}
    for a in range(math.floor((lat-dy)/.002), math.floor((lat+dy)/.002)+1):
      for b in range(math.floor((lon-dx)/.002), math.floor((lon+dx)/.002)+1):
        edges.update((edge.key, edge) for edge in self.cells.get((a,b), ()))
    candidates = []
    for edge in edges.values():
      x,y = xy(point, edge.start)
      vx,vy = xy(edge.end, edge.start)
      along = max(0., min(edge.length, (x*vx+y*vy)/edge.length))
      distance = math.hypot(x-along*vx/edge.length, y-along*vy/edge.length)
      bearing = math.degrees(math.atan2(vx,vy)) % 360
      if distance <= tolerance and angle_difference(bearing, heading) <= 35:
        candidates.append((distance, edge.key, edge, along))
    if not candidates:
      return None
    candidates.sort(key=lambda item: (item[0],item[1]))
    distance,_,edge,along = candidates[0]
    for rival_distance,_,rival,_ in candidates[1:]:
      same_road = rival.way == edge.way and bool({rival.start,rival.end}.intersection((edge.start,edge.end)))
      same_road = same_road or self._unique_continuation(edge,rival) or self._unique_continuation(rival,edge)
      if not same_road and rival_distance <= distance+8:
        return None
    return edge,along

  def _unique_continuation(self, before, after):
    # Packed tiles have no original OSM way ID. A metadata/geometry split is
    # not a rival road when its exact shared endpoint has one forward choice.
    # Apply the same no-branch rule as routing, excluding only immediate reverse.
    if before.end != after.start:
      return False
    following = [edge for edge in self.outgoing.get(before.end, ()) if edge.end != before.start]
    return len(following) == 1 and following[0].key == after.key

  def segment_covered(self, start, end):
    intervals = []
    for box in self.bounds:
      low, high = 0., 1.
      for axis in (0, 1):
        delta = end[axis]-start[axis]
        if abs(delta) < 1e-15:
          if not box[axis] <= start[axis] <= box[axis+2]:
            high = -1.
            break
        else:
          a,b = sorted(((box[axis]-start[axis])/delta,(box[axis+2]-start[axis])/delta))
          low,high = max(low,a),min(high,b)
      if low <= high:
        intervals.append((low,high))
    covered = 0.
    for low,high in sorted(intervals):
      if low > covered+1e-9:
        return False
      covered = max(covered,high)
      if covered >= 1-1e-9:
        return True
    return False

  def camera_distance(self, point, heading, camera_point, camera_heading):
    return self._distance_between(self.associate(point,heading),
                                  self.associate(camera_point,camera_heading,tolerance=25.))

  def passed_distance(self, point, heading, camera_point, camera_heading):
    return self._distance_between(self.associate(camera_point,camera_heading,tolerance=25.),
                                  self.associate(point,heading))

  def _route(self, origin):
    edge,along = origin
    key = (edge.key,along)
    if key == self._route_key:
      return self._route_entries
    entries = {}
    offset = -along
    visited = set()
    while offset <= 5000 and edge.key not in visited:
      begin_along = along if not visited else 0.
      segment_start = tuple(edge.start[i]+(edge.end[i]-edge.start[i])*begin_along/edge.length for i in (0,1))
      # Keep this entry even if only its prefix is covered: an earlier target
      # on that prefix remains usable, but no continuation beyond a gap does.
      entries[edge.key] = (offset,segment_start)
      if not self.segment_covered(segment_start,edge.end):
        break
      visited.add(edge.key)
      offset += edge.length
      if not self.inside(edge.end):
        break
      following = [e for e in self.outgoing.get(edge.end, ()) if e.end != edge.start and e.key not in visited]
      if len(following) != 1:
        break
      edge = following[0]
    self._route_key,self._route_entries = key,entries
    return entries

  def _distance_between(self, origin, destination):
    if origin is None or destination is None:
      return None
    target,target_along = destination
    entry = self._route(origin).get(target.key)
    if entry is None:
      return None
    offset,segment_start = entry
    distance = offset+target_along
    if distance < 0:
      return None
    segment_end = tuple(target.start[i]+(target.end[i]-target.start[i])*target_along/target.length for i in (0,1))
    return distance if self.segment_covered(segment_start,segment_end) else None


class OfflineOsmBridge:
  def __init__(self, mapd_root, *, monotonic=time.monotonic):
    self.root = Path(mapd_root)/'offline'
    self.monotonic = monotonic
    self.snapshot = None
    self.status = 'offline_maps_unavailable'
    self._worker = None
    self._next_check = 0.
    self._schema = None
    self._raw_cache = OrderedDict()
    self._signature = None
    self._lock = threading.Lock()

  def tick(self, latitude, longitude):
    if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in (latitude,longitude)) or not (
        -89.8 < latitude < 89.8 and -179.8 < longitude < 179.8):
      return False
    point = (latitude,longitude)
    if self.snapshot is not None and self.snapshot.inside(point) and math.hypot(*xy(point,self.snapshot.anchor)) < 500:
      if self.monotonic() < self._next_check:
        return False
    elif self.monotonic() < self._next_check and self.snapshot is None:
      return False
    with self._lock:
      if self._worker is not None and self._worker.is_alive():
        return False
      self.status = 'offline_maps_loading'
      self._worker = threading.Thread(target=self._load, args=(point,), name='camera-offline-osm', daemon=True)
      self._worker.start()
      return True

  def _paths(self, point):
    search = bounding_box(point, SEARCH_RADIUS_M)
    found = []
    for lat in range(math.floor(search[0]/2)*2,math.floor(search[2]/2)*2+1,2):
      for lon in range(math.floor(search[1]/2)*2,math.floor(search[3]/2)*2+1,2):
        directory = self.root/str(lat)/str(lon)
        try:
          names = directory.iterdir()
          for i,path in enumerate(names):
            if i >= MAX_DIRECTORY_ENTRIES:
              raise ValueError('offline_map_directory_budget')
            values = path.name.split('_')
            if len(values) != 4:
              continue
            try:
              bounds = tuple(float(value) for value in values)
            except ValueError:
              continue
            if (all(math.isfinite(value) for value in bounds) and -90 <= bounds[0] < bounds[2] <= 90 and
                -180 <= bounds[1] < bounds[3] <= 180 and intersects(bounds,search)):
              found.append((path,bounds))
        except FileNotFoundError:
          continue
    if len(found) > MAX_NEARBY_TILES:
      raise ValueError('offline_map_tile_budget')
    return sorted(found,key=lambda item:str(item[0]))

  def _bytes(self,path):
    stat = path.stat()
    if not 0 < stat.st_size <= MAX_TILE_BYTES:
      raise ValueError('offline_map_size_budget')
    stamp = (stat.st_mtime_ns,stat.st_size)
    cached = self._raw_cache.pop(path,None)
    if cached is not None and cached[0] == stamp:
      self._raw_cache[path] = cached
      return cached[1]
    raw = path.read_bytes()
    if len(raw) != stat.st_size:
      raise ValueError('offline_map_changed_during_read')
    self._raw_cache[path] = (stamp,raw)
    while sum(len(value[1]) for value in self._raw_cache.values()) > MAX_CACHED_BYTES:
      self._raw_cache.popitem(last=False)
    return raw

  def _load(self,point):
    try:
      tid = threading.get_native_id()
      if hasattr(os,'sched_setscheduler'):
        os.sched_setscheduler(tid,os.SCHED_OTHER,os.sched_param(0))
      if hasattr(os,'setpriority'):
        os.setpriority(os.PRIO_PROCESS,tid,10)
    except OSError:
      self.status = 'offline_map_worker_priority_error'
      self._next_check = self.monotonic()+30
      return
    try:
      if self._schema is None:
        import capnp
        self._schema = capnp.load(str(Path(__file__).with_suffix('.capnp')))
      paths = self._paths(point)
      signature = tuple((str(path),path.stat().st_mtime_ns,path.stat().st_size) for path,_ in paths)
      if (self.snapshot is not None and signature == self._signature and
          math.hypot(*xy(point,self.snapshot.anchor)) < 500):
        self.status = 'ready'
        self._next_check = self.monotonic()+30
        return
      edges,bounds = {},[]
      crop = bounding_box(point,CROP_RADIUS_M)
      for path,expected in paths:
        reader = self._schema.Offline.from_bytes_packed(self._bytes(path),traversal_limit_in_words=16*1024*1024)
        actual = (reader.minLat,reader.minLon,reader.maxLat,reader.maxLon)
        if any(abs(a-b)>1e-6 for a,b in zip(expected,actual)):
          raise ValueError('offline_map_bounds_mismatch')
        bounds.append(actual)
        for way in reader.ways:
          box = (way.minLat,way.minLon,way.maxLat,way.maxLon)
          if not intersects(box,crop):
            continue
          nodes = tuple((node.latitude,node.longitude) for node in way.nodes)
          if not all(math.isfinite(a) and math.isfinite(b) and -90 <= a <= 90 and -180 <= b <= 180 for a,b in nodes):
            raise ValueError('offline_map_invalid_coordinate')
          # Stable derived geometry identity, not an original OSM ID.
          identity = hashlib.sha256(repr((nodes,bool(way.oneWay))).encode()).hexdigest()[:24]
          for i,(start,end) in enumerate(zip(nodes,nodes[1:])):
            segment_box = (min(start[0],end[0]),min(start[1],end[1]),max(start[0],end[0]),max(start[1],end[1]))
            if not intersects(segment_box,crop):
              continue
            length = math.hypot(*xy(end,start))
            if length <= .1:
              continue
            pairs = ((start,end),) if way.oneWay else ((start,end),(end,start))
            for a,b in pairs:
              key = f'{identity}:{i}:{a}>{b}'
              edges[key] = RoadEdge(key,identity,a,b,length)
            if len(edges)>MAX_LOCAL_EDGES:
              raise ValueError('offline_map_edge_budget')
      snapshot = OfflineRoadSnapshot(edges.values(),bounds,point)
      if not paths or not snapshot.inside(point):
        raise ValueError('offline_maps_unavailable')
      final_signature = tuple((str(path),path.stat().st_mtime_ns,path.stat().st_size) for path,_ in paths)
      if signature != final_signature:
        raise ValueError('offline_map_changed_during_read')
      self.snapshot = snapshot
      self._signature = signature
      self.status = 'ready'
      self._next_check = self.monotonic()+30
    except Exception:
      self.snapshot = None
      self.status = 'offline_maps_unavailable'
      self._next_check = self.monotonic()+30
