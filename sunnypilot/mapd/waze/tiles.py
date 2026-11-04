"""Waze fixed-camera WZDF tiles, with strict complete-batch framing and bounded cache.

Section 25 layout is independently implemented from the archived Waze 2.4.0
format definitions (mkoloberdin/waze e199da023302b6475c4660e6a6e4eb386c511572).
Current public Waze tiles retained version 0x00030000 in November 2026 probes.
The speed byte's units remain unknown; it is never converted into a limit.
"""
from __future__ import annotations

import math
import os
import re
import struct
import tempfile
import time
import zlib
from pathlib import Path

from .clock import utc_now_s
from .transport import request

MAX_BYTES = 8 * 1024 * 1024
MAX_TILES = 8192
BATCH_SIZE = 32
CACHE_TTL_MS = 12 * 3600 * 1000
MAX_CACHE_FILES = 2048
_CACHE_NAME = re.compile(r"(?:row|na|il)_[0-9]+\.wdf\Z")


def region_for(lat: float, lon: float) -> str:
  if 29 <= lat <= 34 and 34 <= lon <= 36.5:
    return "il"
  if -170 <= lon <= -30 and -60 <= lat <= 85:
    return "na"
  return "row"


def validate_query(lat: float, lon: float, radius_m: float) -> None:
  if not all(math.isfinite(value) for value in (lat, lon, radius_m)) or not (-90 <= lat <= 90 and -180 <= lon <= 180) or radius_m <= 0:
    raise ValueError("Invalid Waze query coordinates or radius")


def covering_tiles(lat: float, lon: float, radius_m: float) -> list[int]:
  validate_query(lat, lon, radius_m)
  angle = radius_m / 6371000
  if angle >= math.pi:
    raise ValueError("Waze fixed-camera query too large; reduce radius")
  dlat = math.degrees(angle)
  south = max(0, math.floor((max(-90, lat - dlat) + 90) * 100 - 1e-9))
  north = min(17999, math.floor((min(90, lat + dlat) + 90) * 100 + 1e-9))
  polar = lat - dlat <= -90 or lat + dlat >= 90
  ratio = math.sin(angle) / max(1e-16, math.cos(math.radians(lat)))
  dlon = 180 if polar or ratio >= 1 else math.degrees(math.asin(max(0, ratio)))
  west = math.floor((lon - dlon + 180) * 100 - 1e-9)
  east = math.floor((lon + dlon + 180) * 100 + 1e-9)
  columns = min(36000, east - west + 1)
  count = columns * (north - south + 1)
  if count > MAX_TILES:
    raise ValueError(f"Waze fixed-camera query needs {count} tiles; maximum {MAX_TILES}; reduce radius")
  return sorted({(column % 36000) * 18000 + row for column in range(west, west + columns) for row in range(south, north + 1)})


def split_batch(data: bytes, expected_tiles: list[int]) -> dict[int, bytes]:
  if len(data) < 8 or len(data) > MAX_BYTES or data[:4] != b"....":
    raise ValueError("Invalid Waze fixed-camera batch header or size")
  count, = struct.unpack_from(">I", data, 4)
  expected = set(expected_tiles)
  if not expected or len(expected) != len(expected_tiles) or count != len(expected) or count > BATCH_SIZE:
    raise ValueError("Incomplete Waze fixed-camera batch count")
  offset, frames = 8, {}
  for _ in range(count):
    if offset + 12 > len(data):
      raise ValueError("Truncated Waze fixed-camera frame")
    identifier, _version, size = struct.unpack_from(">III", data, offset)
    offset += 12
    if identifier not in expected or identifier in frames or not 20 <= size <= MAX_BYTES or size > len(data) - offset:
      raise ValueError("Wrong, duplicate or missing Waze fixed-camera frame")
    body = data[offset:offset + size]
    if body[:4] != b"WZDF" or struct.unpack_from("<I", body, 12)[0] != size - 20:
      raise ValueError("Invalid Waze fixed-camera frame boundary")
    frames[identifier] = body
    offset += size
  if offset != len(data) or frames.keys() != expected:
    raise ValueError("Incomplete Waze fixed-camera batch")
  return frames


def parse_tile(body: bytes, expected_tile: int) -> list[dict]:
  if not 0 <= expected_tile < 36000 * 18000 or not 20 <= len(body) <= MAX_BYTES:
    raise ValueError("Invalid Waze fixed-camera tile size or id")
  magic, endian, version, compressed_size, raw_size = struct.unpack_from("<4sIIII", body)
  if magic != b"WZDF" or endian != 1 or version != 0x30000 or not 0 < compressed_size == len(body) - 20 or not 8 <= raw_size <= MAX_BYTES:
    raise ValueError("Unsupported Waze fixed-camera tile header")
  try:
    inflater = zlib.decompressobj()
    raw = inflater.decompress(body[20:], raw_size + 1)
  except zlib.error:
    raise ValueError("Invalid Waze fixed-camera compression") from None
  if not inflater.eof or inflater.unused_data or inflater.unconsumed_tail or len(raw) != raw_size:
    raise ValueError("Incomplete or oversized Waze fixed-camera decompression")
  count, alignment = struct.unpack_from("<II", raw)
  if not 27 <= count <= 256 or alignment > 16 or 8 + 4 * count > len(raw):
    raise ValueError("Invalid Waze fixed-camera section directory")
  base, previous, spans = 8 + 4 * count, 0, []
  mask = (1 << alignment) - 1
  for end, in struct.iter_unpack("<I", raw[8:base]):
    start = (previous + mask) & ~mask
    if end < previous or end > len(raw) - base or (end != previous and end < start):
      raise ValueError("Invalid Waze fixed-camera section bounds")
    spans.append((base + (end if end == previous else start), base + end))
    previous = end
  start, end = spans[26]
  if end - start < 12 or struct.unpack_from("<I", raw, start)[0] != expected_tile:
    raise ValueError("Waze fixed-camera tile header mismatch")
  start, end = spans[25]
  if (end - start) % 16:
    raise ValueError("Invalid Waze fixed-camera record length")
  west = (expected_tile // 18000) * 10000 - 180000000
  south = (expected_tile % 18000) * 10000 - 90000000
  cameras = []
  for lon, lat, heading, category, speed, local_id, filler in struct.iter_unpack("<iiHBBHh", raw[start:end]):
    if not (west <= lon < west + 10000 and south <= lat < south + 10000 and -180000000 <= lon < 180000000 and -90000000 <= lat < 90000000):
      raise ValueError("Waze fixed-camera coordinate lies outside requested tile")
    cameras.append({"latitude": lat / 1e6, "longitude": lon / 1e6, "heading_raw": heading if heading < 360 else None,
                    "category_raw": category, "speed_raw": speed, "raw_id": local_id,
                    "filler_raw": filler, "tile_id": expected_tile})
  return cameras


def _atomic_write(path: Path, body: bytes, now_ms: int) -> None:
  fd, temporary = tempfile.mkstemp(prefix=".waze-", suffix=".tmp", dir=path.parent)
  try:
    if os.name == "posix":
      os.fchmod(fd, 0o600)
    with os.fdopen(fd, "wb") as stream:
      stream.write(body)
      stream.flush()
      os.fsync(stream.fileno())
    os.replace(temporary, path)
    os.utime(path, (now_ms / 1000, now_ms / 1000))
  finally:
    if os.path.exists(temporary):
      os.unlink(temporary)


class FixedTileClient:
  def __init__(self, cache_dir: Path, transport=request, clock=utc_now_s, monotonic=time.monotonic):
    self.cache_dir, self.transport, self.clock, self.monotonic = Path(cache_dir), transport, clock, monotonic
    self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

  def fetch(self, lat: float, lon: float, radius_m: float, timeout_s: float = 120) -> dict:
    expected, region = covering_tiles(lat, lon, radius_m), region_for(lat, lon)
    if not math.isfinite(timeout_s) or timeout_s <= 0:
      raise ValueError("Waze fixed-camera timeout must be positive")
    deadline, now_ms = self.monotonic() + timeout_s, round(self.clock() * 1000)
    decoded, missing, observed = {}, [], {}
    try:
      for identifier in expected:
        path = self.cache_dir / f"{region}_{identifier}.wdf"
        try:
          stat = path.stat()
          age = now_ms - round(stat.st_mtime * 1000)
          if not path.is_symlink() and path.is_file() and 0 <= age < CACHE_TTL_MS and stat.st_size <= MAX_BYTES:
            decoded[identifier] = parse_tile(path.read_bytes(), identifier)
            observed[identifier] = round(stat.st_mtime * 1000)
            continue
        except (OSError, ValueError):
          pass
        missing.append(identifier)
      for index in range(0, len(missing), BATCH_SIZE):
        batch = missing[index:index + BATCH_SIZE]
        remaining = deadline - self.monotonic()
        if remaining <= 0:
          raise TimeoutError("Waze fixed-camera acquisition deadline exceeded")
        host_region = "am" if region == "na" else region
        url = f"https://ctilesgcs-{host_region}.waze.com/TileServer/multi-get?reqtype=tileBatch&protocol=2&num={len(batch)}&variation=PARTIAL_SIMPLIFICATION"
        url += "".join(f"&t{i}={identifier}&v{i}=0&p{i}=42" for i, identifier in enumerate(batch))
        frames = split_batch(self.transport(url, timeout_s=remaining, max_bytes=MAX_BYTES), batch)
        verified = {identifier: parse_tile(frames[identifier], identifier) for identifier in batch}
        batch_at = round(self.clock() * 1000)
        for identifier in batch:
          _atomic_write(self.cache_dir / f"{region}_{identifier}.wdf", frames[identifier], batch_at)
          observed[identifier] = batch_at
        decoded.update(verified)
      if decoded.keys() != set(expected):
        raise ValueError("Incomplete Waze fixed-camera coverage")
      events, unknown = [], 0
      categories = {2: "SPEED_CAMERA", 3: "DUMMY_CAMERA", 4: "RED_LIGHT_CAMERA"}
      for identifier in expected:
        for camera in decoded[identifier]:
          category = camera["category_raw"]
          unknown += category not in categories
          observed_at = observed[identifier]
          events.append({**camera, "source": "waze", "source_kind": "fixed",
                         "source_id": (f"waze:fixed:{region}:{identifier}:{camera['raw_id']}:" +
                                       f"{round(camera['latitude'] * 1e6)}:{round(camera['longitude'] * 1e6)}"),
                         "raw_type": "CAMERA", "raw_subtype": categories.get(category, f"UNKNOWN_CATEGORY_{category}"),
                         "published_at_ms": None, "observed_at_ms": observed_at, "expires_at_ms": observed_at + CACHE_TTL_MS,
                         "speed_unit": None, "speed_limit_kph": None, "street": None, "city": None})
      return {"events": events, "requested_tiles": len(expected), "loaded_tiles": len(decoded), "unknown_count": unknown,
              "observed_at_ms": min(observed.values(), default=now_ms), "complete": unknown == 0, "region": region}
    finally:
      files = [path for path in self.cache_dir.iterdir() if _CACHE_NAME.fullmatch(path.name) and path.is_file() and not path.is_symlink()]
      for path in sorted(files, key=lambda path: path.stat().st_mtime)[:-MAX_CACHE_FILES]:
        try:
          path.unlink()
        except OSError:
          pass
