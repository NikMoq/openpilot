"""Generation-safe Yandex acquisition; event payloads exist only in bounded RAM."""
from __future__ import annotations

from datetime import UTC, datetime
import json
import math
from pathlib import Path
import threading
import time

from openpilot.sunnypilot.mapd.yandex_policy import events_to_cameras

MAX_EVENTS = 5000
MAX_BYTES = 8 * 1024 * 1024


def _utc_now_s():
  return datetime.now(UTC).timestamp()


class YandexCameraUpdater:
  def __init__(self, params, state_dir, *, backend=None, clock=_utc_now_s, monotonic=time.monotonic):
    self.params, self.state_dir = params, Path(state_dir)
    self.backend, self.clock, self.monotonic = backend, clock, monotonic
    self._lock = threading.RLock()
    self._generation, self._enabled = 0, False
    self._events, self._written = (), {}
    self._worker = None
    self._workers = []
    self._next_attempt, self._last = -math.inf, -math.inf
    self._point, self._heading = None, None
    self._failures = 0

  def _put(self, suffix, value):
    value = str(value)
    key = 'SpeedCameraYandex' + suffix
    if self._written.get(key) != value:
      try:
        self.params.put(key, value)
        self._written[key] = value
      except (OSError, KeyError, ValueError):
        pass

  def _status(self, value):
    self._put('Status', value)

  def _current(self, token):
    with self._lock:
      return self._enabled and token == self._generation

  def snapshot(self):
    with self._lock:
      if not self._enabled:
        return ()
      events, token = self._events, self._generation
    cameras = tuple(events_to_cameras(events, self.clock()))
    with self._lock:
      if not self._enabled or token != self._generation or events is not self._events:
        return ()
      count = len({c.source_params.get('source_id', c.source_id) for c in cameras})
      self._put('Count', count)
      return cameras

  @staticmethod
  def _number(value):
    return type(value) in (int, float) and math.isfinite(value)

  def tick(self, latitude, longitude, *, heading, enabled, position_valid):
    with self._lock:
      if bool(enabled) != self._enabled:
        self._enabled = bool(enabled)
        self._generation += 1
        self._worker = None
        self._events = ()
        self._point = None
        self._last = self._next_attempt = -math.inf
        self._failures = 0
        self._put('Count', 0)
      if not enabled:
        self._status('disabled')
        return False
      if (not position_valid or not all(self._number(v) for v in (latitude, longitude)) or
          not (-85 < latitude < 85 and -180 <= longitude <= 180)):
        self._status('waiting for localized position')
        return False
      if not self._number(heading) or not 0 <= heading < 360:
        self._status('waiting for heading')
        return False
      if self._worker is not None and self._worker.is_alive():
        return False
      now = self.monotonic()
      if now < self._next_attempt:
        return False
      moved = self._point is None or math.hypot((latitude - self._point[0]) * 111320,
          (longitude - self._point[1]) * 111320 * math.cos(math.radians(latitude))) >= 1000
      turned = self._heading is None or abs((heading - self._heading + 180) % 360 - 180) >= 30
      if not moved and not turned and now - self._last < 60:
        return False
      self._workers = [worker for worker in self._workers if worker.is_alive()]
      if len(self._workers) >= 2:
        return False
      token = self._generation
      self._next_attempt = now + 5
      self._status('downloading Yandex')
      worker = threading.Thread(target=self._refresh, args=((latitude, longitude), heading, token), daemon=True,
                                name='yandex-camera-fetch')
      self._worker = worker
      self._workers.append(worker)
      worker.start()
      return True

  def _validate(self, events):
    if not isinstance(events, (tuple, list)) or len(events) > MAX_EVENTS or any(not isinstance(e, dict) for e in events):
      raise ValueError('event budget/type')
    raw = json.dumps(events, allow_nan=False, separators=(',', ':')).encode()
    if len(raw) > MAX_BYTES:
      raise ValueError('event byte budget')
    # Own immutable-by-convention copy; backend cannot mutate retained state.
    return tuple(json.loads(raw))

  def _merge(self, old, new):
    now = self.clock()
    merged = {}
    for event in (*old, *new):
      expires, observed = event.get('expires_at'), event.get('observed_at')
      if not (self._number(expires) and self._number(observed) and observed <= now + 1 and now < expires):
        continue
      key = json.dumps([event.get('source_id'), event.get('travel_direction'), event.get('heading'),
                        event.get('road_variant'), event.get('road_geometry'), event.get('road_heading_deg')], sort_keys=True, separators=(',', ':'))
      merged[key] = event
    return self._validate(tuple(merged.values()))

  def _refresh(self, point, heading, token):
    try:
      if not self._current(token):
        return
      with self._lock:
        if self.backend is None:
          from openpilot.sunnypilot.mapd.yandex.native_provider import NativeYandexProvider
          self.backend = NativeYandexProvider(self.state_dir / 'private' / 'client.json')
        backend = self.backend
      result = backend.fetch_window(*point, heading, should_continue=lambda: self._current(token))
      events = self._validate(result.events)
      if type(result.complete) is not bool:
        raise ValueError('invalid completion flag')
      # Error details are deliberately not copied into Params (may contain tokens).
      partial = not result.complete or bool(result.errors)
      with self._lock:
        if not self._current(token):
          return
        combined = self._merge(self._events, events) if partial else events
        cameras = tuple(events_to_cameras(combined, self.clock()))
        count = len({c.source_params.get('source_id', c.source_id) for c in cameras})
        controls = sum(bool(c.allowed_auto_control) for c in cameras)
        self._events = combined
        self._last, self._point, self._heading = self.monotonic(), point, heading
        self._failures = self._failures + 1 if partial else 0
        self._next_attempt = self.monotonic() + (min(300, 30 * 2 ** min(self._failures - 1, 4)) if partial else 5)
        self._put('Count', count)
        if events or result.complete:
          self._put('UpdatedAt', self.clock())
        self._put('Error', 'incomplete_acquisition' if partial else '')
        self._status(('partial' if partial else 'ready') + f': {count} warnings; {controls} verified limits')
    except Exception as error:
      with self._lock:
        if self._current(token):
          self._failures += 1
          self._next_attempt = self.monotonic() + min(300, 30 * 2 ** min(self._failures - 1, 4))
          credentials = getattr(error, 'category', None) in ('credentials_unavailable', 'creds_unavailable')
          self._put('Error', 'creds_unavailable' if credentials else type(error).__name__)
          self._status('credentials unavailable' if credentials else 'error')
