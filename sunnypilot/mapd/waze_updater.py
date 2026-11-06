"""Bounded background Waze acquisition; never runs HTTP in mapd's publishing loop."""
from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
from pathlib import Path

from openpilot.sunnypilot.mapd.waze.clock import utc_now_s
from openpilot.sunnypilot.mapd.waze_policy import events_to_cameras


def _prepare_snapshot(path, events):
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, name = tempfile.mkstemp(prefix=".events-", dir=path.parent)
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
      json.dump({"schema_version": 1, "events": events}, stream, allow_nan=False, separators=(",", ":"))
      stream.flush()
      os.fsync(stream.fileno())
    return name
  except BaseException:
    if os.path.exists(name):
      os.unlink(name)
    raise


def _safe_error(error):
  # Never stringify server errors: messages may carry tokens, URLs or user IDs.
  fields = []
  category = getattr(error, "category", None)
  stage = getattr(error, "stage", None)
  status = getattr(error, "status", None)
  if isinstance(category, str) and category in {"transport", "http", "local_pending",
                                          "protobuf_rejected", "protobuf_unavailable", "relogin", "protocol", "network", "timeout"}:
    fields.append("category=" + category)
  if isinstance(stage, str) and stage in {"registration", "login", "handshake", "viewport"}:
    fields.append("stage=" + stage)
  if category in ("http", "protobuf_rejected", "protobuf_unavailable") and type(status) is int and 0 <= status <= 99999:
    fields.append("status=" + str(status))
  return type(error).__name__ + ("[" + ",".join(fields) + "]" if fields else "")


def _retain_partial(old, new, point, now):
  """Bound a partial snapshot around this query; retained TTLs stay untouched."""
  merged = {}
  latitude, longitude = point
  for index, item in enumerate((*old, *new)):
    if not events_to_cameras((item,), now):
      continue
    lat, lon = item["latitude"], item["longitude"]
    a, b = math.radians(latitude), math.radians(lat)
    h = math.sin((b - a) / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(math.radians(lon - longitude) / 2) ** 2
    distance = 6371000 * 2 * math.asin(math.sqrt(min(1, max(0, h))))
    # Query radius is5km; at most1km tile margin may survive a partial refresh.
    if distance > 6000:
      continue
    identity = json.dumps([item.get("source_id"), item.get("road_segment_id"), item.get("road_geometry")],
                          sort_keys=True, separators=(",", ":"))
    merged[identity] = (distance, index < len(old), item)
  retained, size = [], 2  # JSON list brackets and comma separators count too.
  for _, _, item in sorted(merged.values(), key=lambda row: row[:2]):
    if len(retained) == 5000:
      break
    encoded_size = len(json.dumps(item, allow_nan=False, separators=(",", ":")).encode()) + bool(retained)
    if size + encoded_size > 8 * 1024 * 1024:
      continue
    retained.append(item)
    size += encoded_size
  return retained


class WazeCameraUpdater:
  def __init__(self, params, state_dir, *, backend=None, clock=utc_now_s, monotonic=time.monotonic):
    self.params, self.state_dir = params, Path(state_dir)
    self.clock, self.monotonic = clock, monotonic
    self.backend = backend
    self._events = {"fixed": [], "mobile": []}
    self._last = {"fixed": -math.inf, "mobile": -math.inf}
    self._position = {"fixed": None, "mobile": None}
    self._lock = threading.Lock()
    self._worker = None
    self._generation = 0
    self._enabled = False
    self._next_attempt = -math.inf
    self._status = None
    try:
      path = self.state_dir / "events.json"
      if path.stat().st_size <= 8 * 1024 * 1024:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and payload.get("schema_version") == 1 and isinstance(payload.get("events"), list):
          for event in payload["events"][:5000]:
            if isinstance(event, dict) and event.get("source_kind") in self._events:
              self._events[event["source_kind"]].append(event)
    except (OSError, ValueError, KeyError, TypeError):
      pass

  def _put(self, key, value):
    try:
      self.params.put(key, str(value))
    except (OSError, KeyError, ValueError):
      pass

  def _set_status(self, value):
    if value != self._status:
      self._status = value
      self._put("SpeedCameraWazeStatus", value)

  def snapshot(self):
    with self._lock:
      if not self._enabled:
        return ()
      events = tuple(e.copy() for group in self._events.values() for e in group)
    return tuple(events_to_cameras(events, self.clock()))

  @staticmethod
  def _moved(a, b):
    if a is None:
      return True
    lat, lon = b
    return math.hypot((a[0] - lat) * 111320, (a[1] - lon) * 111320 * math.cos(math.radians(lat))) >= 1000

  def tick(self, latitude, longitude, *, enabled, position_valid):
    with self._lock:
      if bool(enabled) != self._enabled:
        self._enabled = bool(enabled)
        self._generation += 1
    if not enabled:
      self._set_status("disabled")
      return False
    coordinates_valid = all(type(v) in (int, float) and math.isfinite(v) for v in (latitude, longitude))
    if not position_valid or not coordinates_valid or not (-85 < latitude < 85 and -180 <= longitude <= 180):
      self._set_status("waiting for localized position")
      return False
    if self._worker is not None and self._worker.is_alive():
      return False
    now = self.monotonic()
    if now < self._next_attempt:
      return False
    point = (latitude, longitude)
    kinds = [kind for kind, period in (("fixed", 120), ("mobile", 60))
             if now - self._last[kind] >= period or self._moved(self._position[kind], point)]
    if not kinds:
      return False
    token = self._generation
    self._set_status("downloading Waze")
    self._worker = threading.Thread(target=self._refresh, args=(point, kinds, token), daemon=True, name="waze-camera-fetch")
    self._worker.start()
    return True

  def _refresh(self, point, kinds, token):
    failures = []
    successful = []
    staged = None

    def current_generation():
      with self._lock:
        return token == self._generation and self._enabled

    try:
      with self._lock:
        if token != self._generation or not self._enabled:
          return
      if self.backend is None:
        from openpilot.sunnypilot.mapd.waze import WazeBackend
        self.backend = WazeBackend(self.state_dir / "private")
      for kind in kinds:
        with self._lock:
          if token != self._generation or not self._enabled:
            return
        try:
          result = getattr(self.backend, "fetch_" + kind)(*point, 5000)
          coverage_gap = kind == "fixed" and result.loaded_tiles != result.requested_tiles
          partial_fixed = kind == "fixed" and result.unknown_count > 0 and not coverage_gap
          if coverage_gap or ((not result.complete or result.unknown_count) and not partial_fixed):
            raise ValueError("incomplete Waze coverage")
          events = [dict(e) for e in result.events]
          if len(events) > 5000 or any(e.get("source_kind") != kind for e in events):
            raise ValueError("invalid Waze event set")
          if hasattr(self.backend, "enrich_events"):
            if not current_generation():
              return
            events = self.backend.enrich_events(events, should_continue=current_generation, center=point)
            if self.backend.road_error_count:
              failures.append(kind + ":road_limit_unavailable")
          if len(events) > 5000 or len(json.dumps(events, allow_nan=False).encode()) > 8 * 1024 * 1024:
            raise ValueError("Waze event budget exceeded")
          with self._lock:
            if token != self._generation or not self._enabled:
              return
            if partial_fixed:
              # Unknown categories do not invalidate independently decoded records.
              events = _retain_partial(self._events[kind], events, point, self.clock())
              failures.append("fixed:unsupported_categories")
            self._events[kind] = events
            self._last[kind] = self.monotonic()
            self._position[kind] = point
          successful.append(kind)
        except Exception as error:
          # Server/parser text can contain tokens; expose classes only.
          failures.append(kind + ":" + _safe_error(error))
      with self._lock:
        if token != self._generation or not self._enabled:
          return
        raw = [dict(e) for group in self._events.values() for e in group]
      if successful:
        # Serialize/fsync the bulk payload without holding mapd's snapshot lock.
        staged = _prepare_snapshot(self.state_dir / "events.json", raw)
      cameras = events_to_cameras(raw, self.clock())
      controls = sum(c.allowed_auto_control for c in cameras)
      reports = len({c.source_params.get("source_id", c.source_id) for c in cameras})
      with self._lock:
        if token != self._generation or not self._enabled:
          return
        if staged is not None:
          os.replace(staged, self.state_dir / "events.json")
          staged = None
        self._put("SpeedCameraWazeCount", reports)
        if successful:
          self._put("SpeedCameraWazeUpdatedAt", self.clock())
        self._put("SpeedCameraWazeError", "; ".join(failures))
        self._set_status(("partial" if failures else "ready") + f": {reports} warnings; {controls} verified limits")
    except Exception as error:
      with self._lock:
        if token == self._generation and self._enabled:
          self._put("SpeedCameraWazeError", _safe_error(error))
          self._set_status("error")
    finally:
      if staged is not None and os.path.exists(staged):
        os.unlink(staged)
      with self._lock:
        if token == self._generation and self._enabled:
          self._next_attempt = self.monotonic() + (30 if failures or not successful else 1)
