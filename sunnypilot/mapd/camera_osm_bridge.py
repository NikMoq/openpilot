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
import heapq
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
MAX_LOCAL_WAY_HOPS = 64
MAX_JUNCTION_EDGES = 256
MAX_JUNCTION_NODES = 128
MAX_JUNCTION_DEGREE = 16
MAX_JUNCTION_APPROACH_EDGES = 4096
JUNCTION_CAMERA_DISTANCE_M = 100.
SEARCH_RADIUS_M = 1800.
CROP_RADIUS_M = 2300.
FAILURE_CODES = frozenset({
  'offline_map_directory_budget', 'offline_map_tile_budget', 'offline_map_size_budget',
  'offline_map_index_budget', 'offline_map_edge_budget', 'offline_map_long_segment',
  'offline_map_bounds_mismatch', 'offline_map_invalid_coordinate', 'offline_map_changed_during_read',
  'offline_maps_unavailable',
})


def failure_code(error, phase):
  """Expose only stable categories, never exception text, file paths or payload."""
  if isinstance(error, ValueError) and error.args and isinstance(error.args[0], str) and error.args[0] in FAILURE_CODES:
    return error.args[0]
  if isinstance(error, PermissionError):
    return 'offline_map_permission_denied'
  if phase == 'schema':
    return 'offline_map_schema_error'
  if isinstance(error, OSError):
    return 'offline_map_io_error'
  if phase == 'decode':
    return 'offline_map_decode_error'
  return 'offline_map_load_error'


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
  supports_vehicle_uncertainty = True

  def __init__(self, edges, bounds, anchor):
    self.edges = tuple(edges)
    if len(self.edges) > MAX_LOCAL_EDGES:
      raise ValueError('offline_map_edge_budget')
    self.bounds = tuple(bounds)
    self.anchor = anchor
    self.cells = {}
    self.outgoing = {}
    self.incoming = {}
    self._association_cache = OrderedDict()
    self._route_key = None
    self._route_entries = {}
    self._junction_context_key = None
    self._junction_context_value = None
    index_references = 0
    for edge in self.edges:
      self.outgoing.setdefault(edge.start, []).append(edge)
      self.incoming.setdefault(edge.end, []).append(edge)
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

  def associate(self, point, heading, tolerance=12., heading_tolerance=35.):
    # Static camera projections recur for every GPS fix. Keep a small LRU;
    # snapshot geometry never changes, so a cached refusal is equally stable.
    key = (tuple(point),heading,tolerance,heading_tolerance)
    if key in self._association_cache:
      value = self._association_cache.pop(key)
      self._association_cache[key] = value
      return value
    value = self._associate_uncached(point,heading,tolerance,heading_tolerance)
    self._association_cache[key] = value
    if len(self._association_cache) > 256:
      self._association_cache.popitem(last=False)
    return value

  def _associate_uncached(self, point, heading, tolerance=12., heading_tolerance=35.):
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
      if distance <= tolerance and self._heading_matches(edge,point,bearing,heading,tolerance,heading_tolerance):
        candidates.append((distance, edge.key, edge, along))
    if not candidates:
      return None
    candidates.sort(key=lambda item: (item[0],item[1]))
    distance,_,edge,along = candidates[0]
    local_positions = None
    junction_nodes = None
    junction_checked = False
    for rival_distance,_,rival,rival_along in candidates[1:]:
      same_road = rival.way == edge.way and bool({rival.start,rival.end}.intersection((edge.start,edge.end)))
      same_road = same_road or self._unique_continuation(edge,rival) or self._unique_continuation(rival,edge)
      if not same_road and rival_distance <= distance+8:
        # Dense nodes can put several nonadjacent pieces of one local road
        # inside the search circle. Do not mistake those pieces for rivals.
        # Build this bounded neighborhood once, not a route per candidate.
        if local_positions is None and rival.way == edge.way:
          local_positions = self._local_way_positions(edge, along, heading, tolerance)
        offset = local_positions.get(rival.key) if local_positions is not None else None
        if offset is not None and abs(offset+rival_along) <= 2*tolerance:
          continue
        if not junction_checked:
          projected = tuple(edge.start[i]+(edge.end[i]-edge.start[i])*along/edge.length for i in (0,1))
          seeds = [(node,partial) for node,partial in ((edge.start,along),(edge.end,edge.length-along))
                   if self.segment_covered(projected,node)]
          junction_nodes = self._walk_local_nodes(seeds,2*tolerance)
          junction_checked = True
        rival_projection = tuple(rival.start[i]+(rival.end[i]-rival.start[i])*rival_along/rival.length for i in (0,1))
        if junction_nodes is None or not any(
            has_junction and distance_to_node+partial <= 2*tolerance
            for node,partial in ((rival.start,rival_along),(rival.end,rival.length-rival_along))
            for (reached,has_junction),distance_to_node in junction_nodes.items()
            if reached == node and self.segment_covered(node,rival_projection)):
          return None
    return edge,along

  def _local_way_positions(self, origin, along, heading, tolerance):
    # Candidate projections lie in one tolerance-radius circle. Permit only
    # a unique local chain within its diameter, never an entire shared way ID.
    limit = 2*tolerance
    positions = {origin.key: -along}
    hx,hy = math.sin(math.radians(heading)),math.cos(math.radians(heading))
    for forward in (True,False):
      edge,offset = origin,-along
      visited = {origin.key}
      for _ in range(MAX_LOCAL_WAY_HOPS):
        joint_offset = offset+edge.length if forward else offset
        if abs(joint_offset) > limit:
          break
        joint = edge.end if forward else edge.start
        incoming = self.incoming.get(joint,())
        outgoing = self.outgoing.get(joint,())
        if len(incoming) > 2 or len(outgoing) > 2:
          break
        choices = ([candidate for candidate in outgoing if candidate.end != edge.start] if forward else
                   [candidate for candidate in incoming if candidate.start != edge.end])
        if len(choices) != 1:
          break
        neighbor = choices[0]
        before,after = (edge,neighbor) if forward else (neighbor,edge)
        preceding = [candidate for candidate in incoming if candidate.start != after.end]
        following = [candidate for candidate in outgoing if candidate.end != before.start]
        if (len(preceding) != 1 or preceding[0].key != before.key or
            len(following) != 1 or following[0].key != after.key or
            neighbor.way != origin.way or neighbor.key in visited):
          break
        vx,vy = xy(neighbor.end,neighbor.start)
        if vx*hx+vy*hy <= 1e-9:
          break  # A sideways/backtracking connector cannot merge hairpin legs.
        offset = joint_offset if forward else joint_offset-neighbor.length
        positions[neighbor.key] = offset
        visited.add(neighbor.key)
        edge = neighbor
    return positions

  def _neighbors(self, node):
    incoming,outgoing = self.incoming.get(node,()),self.outgoing.get(node,())
    if len(incoming) > MAX_JUNCTION_DEGREE or len(outgoing) > MAX_JUNCTION_DEGREE:
      return None
    neighbors = {}
    for edge in (*incoming,*outgoing):
      other = edge.end if edge.start == node else edge.start
      if other not in neighbors or edge.key < neighbors[other].key:
        neighbors[other] = edge
    return neighbors if len(neighbors) <= MAX_JUNCTION_DEGREE else None

  def _walk_local_nodes(self, seeds, limit, blocked=None):
    limit += 1e-6  # Coordinate conversion roundoff at the closed distance bound.
    distances,seen_nodes,seen_edges,queue = {},set(),set(),[]
    for node,distance in seeds:
      heapq.heappush(queue,(distance,node,False))
    while queue:
      distance,node,has_junction = heapq.heappop(queue)
      if distance > limit:
        continue
      neighbors = self._neighbors(node)
      if neighbors is None:
        return None
      has_junction = has_junction or len(neighbors) >= 3
      key = (node,has_junction)
      if distance >= distances.get(key,math.inf):
        continue
      distances[key] = distance
      seen_nodes.add(node)
      if len(seen_nodes) > MAX_JUNCTION_NODES:
        return None
      for other,edge in neighbors.items():
        pair = frozenset((node,other))
        if pair == blocked or not self.segment_covered(node,other):
          continue
        seen_edges.add(pair)
        if len(seen_edges) > MAX_JUNCTION_EDGES:
          return None
        following = distance+edge.length
        if following <= limit and following < distances.get((other,has_junction),math.inf):
          heapq.heappush(queue,(following,other,has_junction))
    return distances

  def _heading_matches(self, edge, point, bearing, heading, tolerance, heading_tolerance=35.):
    if angle_difference(bearing,heading) <= heading_tolerance:
      return True
    # A sparse polyline can encode a real bend as two coarse segments. Near
    # their exact joint, compare the course with the joined local tangent too;
    # keep the original segment's lateral and heading tests everywhere else.
    for joint,before_side in ((edge.start,True),(edge.end,False)):
      if math.hypot(*xy(point,joint)) > tolerance:
        continue
      incoming = self.incoming.get(joint,())
      outgoing = self.outgoing.get(joint,())
      neighbors_at_joint = self._neighbors(joint)
      if neighbors_at_joint is not None and len(neighbors_at_joint) >= 3:
        neighbors = incoming if before_side else outgoing
        for neighbor in neighbors:
          before,after = (neighbor,edge) if before_side else (edge,neighbor)
          if before.start == after.end:
            continue
          ax,ay = xy(before.end,before.start)
          bx,by = xy(after.end,after.start)
          tx,ty = ax/before.length+bx/after.length,ay/before.length+by/after.length
          if math.hypot(tx,ty) > 1e-9 and angle_difference(math.degrees(math.atan2(tx,ty))%360,heading) <= heading_tolerance:
            return True
      # At most a continuation and its immediate reverse are admissible. This
      # bounds lookup work and refuses merges/forks rather than choosing one.
      if len(incoming) > 2 or len(outgoing) > 2:
        continue
      neighbors = incoming if before_side else outgoing
      for neighbor in neighbors:
        before,after = (neighbor,edge) if before_side else (edge,neighbor)
        if not self._unique_continuation(before,after):
          continue
        preceding = [candidate for candidate in incoming if candidate.start != after.end]
        if len(preceding) != 1 or preceding[0].key != before.key:
          continue
        ax,ay = xy(before.end,before.start)
        bx,by = xy(after.end,after.start)
        tx,ty = ax/before.length+bx/after.length,ay/before.length+by/after.length
        if math.hypot(tx,ty) <= 1e-9:
          continue  # Opposite/degenerate legs do not define a travel tangent.
        tangent = math.degrees(math.atan2(tx,ty)) % 360
        if angle_difference(tangent,heading) <= heading_tolerance:
          return True
    return False

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

  def camera_distance(self, point, heading, camera_point, camera_heading, *, vehicle_tolerance_m=12., vehicle_heading_tolerance_deg=35.):
    return self._distance_between(self.associate(point,heading,tolerance=min(30.,max(12.,vehicle_tolerance_m)),
                                                heading_tolerance=min(45.,max(35.,vehicle_heading_tolerance_deg))),
                                  self.associate(camera_point,camera_heading,tolerance=25.))

  def passed_distance(self, point, heading, camera_point, camera_heading, *, vehicle_tolerance_m=12., vehicle_heading_tolerance_deg=35.):
    return self._distance_between(self.associate(camera_point,camera_heading,tolerance=25.),
                                  self.associate(point,heading,tolerance=min(30.,max(12.,vehicle_tolerance_m)),
                                                 heading_tolerance=min(45.,max(35.,vehicle_heading_tolerance_deg))))

  def _junction_context(self, point, heading, tolerance, heading_tolerance):
    key = (tuple(point),heading,tolerance,heading_tolerance)
    if key == self._junction_context_key:
      return self._junction_context_value
    self._junction_context_key,self._junction_context_value = key,None
    origin = self.associate(point,heading,tolerance,heading_tolerance)
    if origin is None:
      return None
    edge,along = origin
    distance,visited = edge.length-along,set()
    for _ in range(MAX_JUNCTION_APPROACH_EDGES):
      if edge.key in visited or distance > 5000:
        return None
      visited.add(edge.key)
      start = tuple(edge.start[i]+(edge.end[i]-edge.start[i])*along/edge.length for i in (0,1))
      if not self.segment_covered(start,edge.end):
        return None
      neighbors = self._neighbors(edge.end)
      if neighbors is None:
        return None
      if len(neighbors) >= 3:
        junction = edge.end
        blocked = frozenset((junction,edge.start))
        local = self._walk_local_nodes(((junction,0.),),JUNCTION_CAMERA_DISTANCE_M,blocked)
        if local is None:
          return None
        value = (distance,junction,blocked,local)
        self._junction_context_value = value
        return value
      following = [item for item in self.outgoing.get(edge.end,()) if item.end != edge.start]
      if len(following) != 1:
        return None
      edge,along = following[0],0.
      distance += edge.length
    return None

  def junction_camera_approach(self, point, heading, camera_point, *, vehicle_tolerance_m=12., vehicle_heading_tolerance_deg=35.):
    tolerance = min(30.,max(12.,vehicle_tolerance_m))
    heading_tolerance = min(45.,max(35.,vehicle_heading_tolerance_deg))
    if not self.inside(camera_point):
      return None
    vx,vy = xy(camera_point,point)
    if vx*math.sin(math.radians(heading))+vy*math.cos(math.radians(heading)) < 0:
      return None
    context = self._junction_context(point,heading,tolerance,heading_tolerance)
    if context is None:
      return None
    incoming_distance,junction,blocked,local = context
    if math.hypot(*xy(camera_point,junction)) > JUNCTION_CAMERA_DISTANCE_M+1e-6:
      return None
    camera_tolerance = 25.
    lat,lon = camera_point
    dy = math.degrees(camera_tolerance/6371000)
    dx = dy/max(.01,math.cos(math.radians(lat)))
    edges = {}
    for a in range(math.floor((lat-dy)/.002),math.floor((lat+dy)/.002)+1):
      for b in range(math.floor((lon-dx)/.002),math.floor((lon+dx)/.002)+1):
        edges.update((edge.key,edge) for edge in self.cells.get((a,b),()))
    candidates = []
    for edge in edges.values():
      x,y = xy(camera_point,edge.start)
      ex,ey = xy(edge.end,edge.start)
      along = max(0.,min(edge.length,(x*ex+y*ey)/edge.length))
      lateral = math.hypot(x-along*ex/edge.length,y-along*ey/edge.length)
      if lateral > camera_tolerance:
        continue
      projected = tuple(edge.start[i]+(edge.end[i]-edge.start[i])*along/edge.length for i in (0,1))
      distances = [reached+partial for node,partial in ((edge.start,along),(edge.end,edge.length-along))
                   for (end,_),reached in local.items() if end == node and self.segment_covered(node,projected)]
      route_distance = min(distances,default=math.inf)
      incoming_arm = frozenset((edge.start,edge.end)) == blocked
      if incoming_arm:
        route_distance = math.inf
      candidates.append((lateral,route_distance,incoming_arm,edge,along))
    if not candidates:
      return None
    best = min(candidates,key=lambda item:(item[0],item[1]))
    if best[1] > JUNCTION_CAMERA_DISTANCE_M+1e-6:
      return None
    camera_nodes = None
    for lateral,route,incoming_arm,edge,along in candidates:
      if incoming_arm or lateral > best[0]+8 or route <= JUNCTION_CAMERA_DISTANCE_M+1e-6:
        continue
      if camera_nodes is None:
        origin,origin_along = best[3],best[4]
        projected = tuple(origin.start[i]+(origin.end[i]-origin.start[i])*origin_along/origin.length for i in (0,1))
        seeds = [(node,partial) for node,partial in ((origin.start,origin_along),(origin.end,origin.length-origin_along))
                 if self.segment_covered(projected,node)]
        camera_nodes = self._walk_local_nodes(seeds,2*camera_tolerance)
      projected = tuple(edge.start[i]+(edge.end[i]-edge.start[i])*along/edge.length for i in (0,1))
      if camera_nodes is None or not any(
          distance_to_node+partial <= 2*camera_tolerance
          for node,partial in ((edge.start,along),(edge.end,edge.length-along))
          for (reached,_),distance_to_node in camera_nodes.items()
          if reached == node and self.segment_covered(node,projected)):
        return None
    return incoming_distance+best[1],junction

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
    phase = 'schema'
    try:
      if self._schema is None:
        import capnp
        self._schema = capnp.load(str(Path(__file__).with_suffix('.capnp')))
      phase = 'discover'
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
        phase = 'decode'
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
      phase = 'index'
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
    except Exception as error:
      self.snapshot = None
      self.status = failure_code(error,phase)
      self._next_check = self.monotonic()+30
