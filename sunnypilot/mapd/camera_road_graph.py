"""Offline OSM topology for conservative camera association; never reads speed tags.

Input is an ordinary complete OSM XML region export, not manually assigned camera
road IDs. Missing, old, ambiguous or branching geometry does not authorize a cap.
"""
from __future__ import annotations

import math
import hashlib
import json
import os
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, parse_qs
from dataclasses import dataclass

R = 6_371_000.0
DRIVABLE = frozenset(('motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'unclassified',
                      'residential', 'living_street', 'service', 'motorway_link', 'trunk_link',
                      'primary_link', 'secondary_link', 'tertiary_link'))


@dataclass(frozen=True)
class Edge:
  key: str
  start: str
  end: str
  a: tuple[float, float]
  b: tuple[float, float]
  layer: str

  @property
  def length(self):
    return math.hypot(*xy(self.b, self.a))


def xy(point, origin):
  return (math.radians(point[1] - origin[1]) * R * math.cos(math.radians(origin[0])),
          math.radians(point[0] - origin[0]) * R)


def project(point, edge):
  x, y = xy(point, edge.a)
  dx, dy = xy(edge.b, edge.a)
  length = math.hypot(dx, dy)
  t = (x * dx + y * dy) / (length * length)
  clamped = min(1., max(0., t))
  return math.hypot(x - clamped * dx, y - clamped * dy), clamped * length, math.degrees(math.atan2(dx, dy)) % 360


class RoadGraph:
  @classmethod
  def combine_complete(cls, graphs, bounds):
    """Combine verified exports only when they cover every part of a rectangle.

    Each input retains its own downloaded bytes/provenance on disk. This is an
    in-memory union, never a fabricated complete-export acquisition manifest.
    """
    graphs = tuple(graphs)
    if (not graphs or len(bounds) != 4 or not all(math.isfinite(v) for v in bounds) or
        not (-90 <= bounds[0] < bounds[2] <= 90 and -180 <= bounds[1] < bounds[3] <= 180)):
      raise ValueError('road_graph_incomplete_region')
    a, b, c, d = bounds
    xs = sorted({b, d, *(max(b, min(d, v)) for g in graphs for v in (g.bounds[1], g.bounds[3]))})
    ys = sorted({a, c, *(max(a, min(c, v)) for g in graphs for v in (g.bounds[0], g.bounds[2]))})
    for y0, y1 in zip(ys, ys[1:]):
      for x0, x1 in zip(xs, xs[1:]):
        y, x = (y0 + y1) / 2, (x0 + x1) / 2
        if not any(g.bounds[0] <= y <= g.bounds[2] and g.bounds[1] <= x <= g.bounds[3] for g in graphs):
          raise ValueError('road_graph_incomplete_region')
    edges = {}
    for graph in graphs:
      for edge in graph.edges:
        if edge.key in edges and edges[edge.key] != edge:
          raise ValueError('road_graph_conflicting_exports')
        edges[edge.key] = edge
    return cls(edges.values(), bounds, min(g.acquired_at for g in graphs))

  def __init__(self, edges, bounds, acquired_at):
    self.edges = tuple(edges)
    self.bounds = bounds
    self.acquired_at = acquired_at
    self.outgoing = {}
    self.cells = {}
    self.long_edges = []
    for edge in self.edges:
      self.outgoing.setdefault(edge.start, []).append(edge)
      low = [math.floor(min(edge.a[i], edge.b[i]) / .002) for i in (0, 1)]
      high = [math.floor(max(edge.a[i], edge.b[i]) / .002) for i in (0, 1)]
      if (high[0] - low[0] + 1) * (high[1] - low[1] + 1) > 10000:
        self.long_edges.append(edge)
      else:
        for lat in range(low[0], high[0] + 1):
          for lon in range(low[1], high[1] + 1):
            self.cells.setdefault((lat, lon), []).append(edge)

  @classmethod
  def load(cls, path, max_age_s=30 * 86400, now=None, allow_empty=False):
    now = time.time() if now is None else now
    with open(path, 'rb') as stream:
      raw = stream.read(128 * 1024 * 1024 + 1)
    if len(raw) > 128 * 1024 * 1024:
      raise ValueError('road_graph_too_large')
    with open(str(path) + '.manifest.json', encoding='utf-8') as stream:
      manifest = json.load(stream)
    if not isinstance(manifest, dict) or not isinstance(manifest.get('source_url'), str):
      raise ValueError('road_graph_invalid_manifest')
    acquired = manifest.get('retrieved_at')
    if not isinstance(acquired, (float, int)) or not math.isfinite(acquired):
      raise ValueError('road_graph_invalid_acquisition_time')
    if not 0 <= now - acquired <= max_age_s:
      raise ValueError('road_graph_stale')
    if (manifest.get('schema_version') != 1 or manifest.get('complete_region_export') is not True or
        manifest.get('sha256') != hashlib.sha256(raw).hexdigest()):
      raise ValueError('road_graph_provenance_mismatch')
    source = urlparse(manifest.get('source_url', ''))
    query = parse_qs(source.query)
    if (source.scheme != 'https' or source.netloc != 'api.openstreetmap.org' or source.path != '/api/0.6/map' or
        set(query) != {'bbox'} or len(query['bbox']) != 1):
      raise ValueError('road_graph_not_complete_map_export')
    root = ET.fromstring(raw)
    if root.tag != 'osm':
      raise ValueError('road_graph_not_osm')
    bounds_node = root.find('bounds')
    if bounds_node is None:
      raise ValueError('road_graph_missing_bounds')
    bounds = tuple(float(bounds_node.attrib[k]) for k in ('minlat', 'minlon', 'maxlat', 'maxlon'))
    if not all(math.isfinite(v) for v in bounds) or not (-90 <= bounds[0] < bounds[2] <= 90 and -180 <= bounds[1] < bounds[3] <= 180):
      raise ValueError('road_graph_invalid_bounds')
    request_bounds = tuple(float(value) for value in query['bbox'][0].split(','))
    if len(request_bounds) != 4 or any(abs(a - b) > 1e-6 for a, b in zip(request_bounds, (bounds[1], bounds[0], bounds[3], bounds[2]))):
      raise ValueError('road_graph_export_bounds_mismatch')
    nodes = {}
    for node in root.findall('node'):
      p = (float(node.attrib['lat']), float(node.attrib['lon']))
      if not all(math.isfinite(v) for v in p) or not (-90 <= p[0] <= 90 and -180 <= p[1] <= 180):
        raise ValueError('road_graph_invalid_node')
      nodes[node.attrib['id']] = p
    edges = []
    for way in root.findall('way'):
      tags = {tag.attrib['k']: tag.attrib['v'] for tag in way.findall('tag')}
      if tags.get('highway') not in DRIVABLE or tags.get('motor_vehicle', tags.get('access')) in ('no', 'private'):
        continue
      refs = [nd.attrib['ref'] for nd in way.findall('nd')]
      if any(ref not in nodes for ref in refs):
        raise ValueError('road_graph_incomplete_way')
      oneway = tags.get('oneway', 'yes' if tags.get('junction') == 'roundabout' else 'no')
      if oneway not in ('yes', '1', 'true', '-1', 'no', '0', 'false'):
        continue
      for i, (a, b) in enumerate(zip(refs, refs[1:])):
        pairs = [(b, a)] if oneway == '-1' else [(a, b)]
        if oneway in ('no', '0', 'false'):
          pairs.append((b, a))
        for start, end in pairs:
          edge = Edge(f"{way.attrib['id']}:{i}:{start}>{end}", start, end, nodes[start], nodes[end], tags.get('layer', '0'))
          if edge.length > 0.1:
            edges.append(edge)
    if not edges and not allow_empty:
      raise ValueError('road_graph_empty')
    return cls(edges, bounds, acquired)

  def inside(self, point, margin=25.):
    lat, lon = point
    a, b, c, d = self.bounds
    lat_margin = math.degrees(margin / R)
    lon_margin = lat_margin / max(.01, math.cos(math.radians(lat)))
    return a + lat_margin < lat < c - lat_margin and b + lon_margin < lon < d - lon_margin

  def associate(self, point, heading=None, tolerance=12.):
    if not self.inside(point):
      return None
    candidates = []
    latitude, longitude = point
    latitude_radius = math.degrees(tolerance / R)
    longitude_radius = latitude_radius / max(.01, math.cos(math.radians(latitude)))
    edges = {e.key: e for e in self.long_edges}
    for lat in range(math.floor((latitude - latitude_radius) / .002), math.floor((latitude + latitude_radius) / .002) + 1):
      for lon in range(math.floor((longitude - longitude_radius) / .002), math.floor((longitude + longitude_radius) / .002) + 1):
        edges.update((e.key, e) for e in self.cells.get((lat, lon), ()))
    for edge in edges.values():
      distance, along, bearing = project(point, edge)
      if distance > tolerance:
        continue
      if heading is not None and abs((bearing - heading + 180) % 360 - 180) > 35:
        continue
      candidates.append((distance, edge, along))
    if not candidates:
      return None
    candidates.sort(key=lambda candidate: candidate[0])
    best = candidates[0]
    # Adjacent segments of the same way are one road. Different ways/layers,
    # including stacked roads, remain ambiguous even when one is slightly nearer.
    way = best[1].key.split(':')[0]
    if any((edge.key.split(':')[0] != way or
            not {edge.start, edge.end}.intersection((best[1].start, best[1].end))) and
           distance <= best[0] + 8 for distance, edge, _ in candidates[1:]):
      return None
    return best[1], best[2]

  def corridor(self, point, heading, distance=5000.):
    match = self.associate(point, heading)
    if match is None:
      return ()
    edge, along = match
    result = [(edge, -along)]
    offset = edge.length - along
    visited = {edge.key}
    while offset <= distance:
      if not self.inside(edge.b, margin=0.):
        break  # Full returned ways may extend beyond the requested complete region.
      next_edges = [e for e in self.outgoing.get(edge.end, ()) if e.end != edge.start and e.key not in visited]
      # No route choice is inferred at an interchange/junction.
      if len(next_edges) != 1:
        break
      edge = next_edges[0]
      result.append((edge, offset))
      visited.add(edge.key)
      offset += edge.length
    return tuple(result)

  def camera_distance(self, point, heading, camera_point, camera_heading):
    camera_match = self.associate(camera_point, camera_heading)
    if camera_match is None:
      return None
    camera_edge, along = camera_match
    for edge, offset in self.corridor(point, heading):
      if edge.key == camera_edge.key:
        return offset + along
    return None


def acquire_map_export(url, path, *, timeout=60, max_bytes=128 * 1024 * 1024, allow_empty=False):
  """Download an unfiltered bounded OSM map response and record exact provenance.

  Network I/O must run outside mapd's live loop. The manifest is local transport
  evidence, not a signed completeness attestation.
  """
  from urllib.request import Request, urlopen
  import tempfile
  source = urlparse(url)
  query = parse_qs(source.query)
  if (source.scheme != 'https' or source.netloc != 'api.openstreetmap.org' or source.path != '/api/0.6/map' or
      set(query) != {'bbox'} or len(query['bbox']) != 1):
    raise ValueError('only an unfiltered OSM map bbox export is supported')
  with urlopen(Request(url, headers={'User-Agent': 'CameraOfflineRoadGraph/1'}), timeout=timeout) as response:
    if response.status != 200 or response.geturl().split('?')[0] != 'https://api.openstreetmap.org/api/0.6/map':
      raise ValueError('unexpected map export response')
    raw = response.read(max_bytes + 1)
  if len(raw) > max_bytes:
    raise ValueError('map export exceeds size limit')
  acquired = time.time()
  manifest = {'schema_version': 1, 'source_url': url, 'retrieved_at': acquired,
              'sha256': hashlib.sha256(raw).hexdigest(), 'complete_region_export': True}
  directory = os.path.dirname(os.path.abspath(path))
  os.makedirs(directory, exist_ok=True)
  fd, temporary = tempfile.mkstemp(prefix='.camera_roads_', dir=directory)
  try:
    with os.fdopen(fd, 'wb') as stream:
      stream.write(raw)
    with open(temporary + '.manifest.json', 'w', encoding='utf-8') as stream:
      json.dump(manifest, stream)
    RoadGraph.load(temporary, allow_empty=allow_empty)
    # A concurrent read between replacements fails hash validation, never uses
    # a graph with another export's provenance. The next tick retries.
    os.replace(temporary, path)
    os.replace(temporary + '.manifest.json', str(path) + '.manifest.json')
  finally:
    for candidate in (temporary, temporary + '.manifest.json'):
      if os.path.exists(candidate):
        os.unlink(candidate)
  return manifest


if __name__ == '__main__':
  import argparse
  parser = argparse.ArgumentParser(description='Acquire an unfiltered offline OSM XML road graph; no vehicle I/O.')
  parser.add_argument('url', help='https://api.openstreetmap.org/api/0.6/map?bbox=minlon,minlat,maxlon,maxlat')
  parser.add_argument('path', help='local camera_roads.osm output path')
  arguments = parser.parse_args()
  print(json.dumps(acquire_map_export(arguments.url, arguments.path), sort_keys=True))
