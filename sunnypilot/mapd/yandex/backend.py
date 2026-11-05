"""Public Yandex road-event acquisition, not yet a qualified camera provider.

This channel has been observed returning accidents, works and closures. Neither
camera inventory nor posted/effective limits are established by those responses.
It is deliberately not wired to vehicle controls or the enabled-provider menu.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .transport import request

ZOOM = 13
MAX_TILES = 64
MAX_EVENTS = 5000
MAX_TILE_BYTES = 2 * 1024 * 1024
CACHE_TTL_S = 60
MAX_CACHE_FILES = 64
MAX_CACHE_BYTES = 16 * 1024 * 1024
_CACHE_NAME = re.compile(r"13_[0-9]+_[0-9]+\.pb\Z")


def _utc_now_s():
  return datetime.now(UTC).timestamp()


@dataclass(frozen=True)
class EventResult:
  events: tuple[dict, ...]
  requested_tiles: int
  loaded_tiles: int
  observed_at_s: float
  oldest_observation_s: float
  # A completed tile request is not evidence of complete Navigator camera data.
  camera_inventory_available: bool = False
  speed_limits_available: bool = False


def _tile_point(latitude, longitude):
  phi = math.radians(latitude)
  eccentricity = 0.0818191908426
  northing = math.asinh(math.tan(phi)) - eccentricity * math.atanh(eccentricity * math.sin(phi))
  scale = 2 ** ZOOM
  return (longitude + 180) / 360 * scale, (1 - northing / math.pi) / 2 * scale


def covering_tiles(latitude: float, longitude: float, radius_m: float) -> tuple[tuple[int, int], ...]:
  if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (latitude, longitude, radius_m)) or
      not -80 < latitude < 80 or not -180 <= longitude <= 180 or not 0 < radius_m <= 5000):
    raise ValueError("Yandex query requires valid coordinates and a radius up to 5 km")
  dlat = math.degrees(radius_m / 6371000)
  dlon = math.degrees(math.asin(math.sin(radius_m / 6371000) / math.cos(math.radians(latitude))))
  if longitude - dlon < -180 or longitude + dlon >= 180:
    raise ValueError("Yandex query crosses the world boundary")
  west, north = _tile_point(latitude + dlat, longitude - dlon)
  east, south = _tile_point(latitude - dlat, longitude + dlon)
  tiles = tuple((x, y) for x in range(math.floor(west), math.floor(east) + 1)
                for y in range(math.floor(north), math.floor(south) + 1))
  if not 0 < len(tiles) <= MAX_TILES:
    raise ValueError("Yandex tile query exceeds budget")
  return tiles


class YandexPublicBackend:
  def __init__(self, cache_dir, *, transport=request, decoder=None, clock=_utc_now_s, monotonic=time.monotonic):
    self.cache_dir = Path(cache_dir).absolute()
    repository = Path(__file__).resolve().parents[3]
    if self.cache_dir.is_symlink() or self.cache_dir.resolve().is_relative_to(repository):
      raise ValueError("Yandex runtime cache must be outside the checkout")
    self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    self.transport, self.clock, self.monotonic = transport, clock, monotonic
    self._verified = {}
    if decoder is None:
      from .tiles import parse_tile
      decoder = parse_tile
    self.decoder = decoder

  def _prune(self):
    files = [p for p in self.cache_dir.iterdir() if _CACHE_NAME.fullmatch(p.name) and not p.is_symlink() and p.is_file()]
    retained = 0
    for index, path in enumerate(sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)):
      try:
        retained += path.stat().st_size
        if index >= MAX_CACHE_FILES or retained > MAX_CACHE_BYTES:
          path.unlink()
      except OSError:
        pass
    retained_names = {p.name for p in files if p.is_file()}
    verified = sorted(((name, value) for name, value in self._verified.items() if name in retained_names),
                      key=lambda item: item[1][1], reverse=True)
    self._verified = dict(verified[:MAX_CACHE_FILES])

  def _save(self, path, body, observed):
    if path.is_symlink():
      raise ValueError("Yandex cache entry is a symbolic link")
    # Identical server data does not cause another flash write/fsync.
    if path.is_file() and path.stat().st_size <= MAX_TILE_BYTES and path.read_bytes() == body:
      return
    fd, name = tempfile.mkstemp(prefix=".yandex-", dir=self.cache_dir)
    try:
      with os.fdopen(fd, "wb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
      os.utime(name, (observed, observed))
      os.replace(name, path)
    finally:
      if os.path.exists(name):
        os.unlink(name)

  def fetch_events(self, latitude, longitude, radius_m, *, timeout_s=30) -> EventResult:
    expected = covering_tiles(latitude, longitude, radius_m)
    if not math.isfinite(timeout_s) or timeout_s <= 0:
      raise ValueError("Invalid Yandex acquisition timeout")
    deadline = self.monotonic() + timeout_s
    events, observations = {}, []
    try:
      for x, y in expected:
        path = self.cache_dir / f"{ZOOM}_{x}_{y}.pb"
        decoded, observed = None, None
        try:
          info = path.stat()
          if not path.is_symlink() and path.is_file() and info.st_size <= MAX_TILE_BYTES:
            body = path.read_bytes()
            digest = hashlib.sha256(body).digest()
            verified = self._verified.get(path.name)
            stamp = verified[1] if verified is not None and verified[0] == digest else info.st_mtime
            if 0 <= self.clock() - stamp < CACHE_TTL_S:
              decoded = self.decoder(body, x, y, ZOOM)
              observed = stamp
        except (OSError, ValueError):
          pass
        if decoded is None:
          remaining = deadline - self.monotonic()
          if remaining <= 0:
            raise TimeoutError("Yandex acquisition deadline exceeded")
          url = "https://core-road-events-renderer.maps.yandex.net/2.x/tiles?trf&l=trfe&lang=ru_RU"
          url += f"&x={x}&y={y}&z={ZOOM}&format=protobuf&vec_protocol=3"
          body = self.transport(url, timeout_s=remaining, max_bytes=MAX_TILE_BYTES)
          decoded = self.decoder(body, x, y, ZOOM)
          observed = self.clock()
          self._save(path, body, observed)
          self._verified[path.name] = (hashlib.sha256(body).digest(), observed)
          self._prune()
        observations.append(observed)
        for event in decoded:
          identifier = event.get("source_id")
          if not isinstance(identifier, str) or not identifier:
            raise ValueError("Yandex road event has no stable source identity")
          record = {**event, "source": "yandex", "observed_at_s": observed,
                    "expires_at_s": observed + CACHE_TTL_S, "limit_kind": "unavailable"}
          # Overlapping tile observations never extend the oldest observation.
          previous = events.get(identifier)
          if previous is None or previous["observed_at_s"] > observed:
            events[identifier] = record
          if len(events) > MAX_EVENTS:
            raise ValueError("Yandex event budget exceeded")
      return EventResult(tuple(events.values()), len(expected), len(observations), self.clock(), min(observations))
    finally:
      self._prune()
