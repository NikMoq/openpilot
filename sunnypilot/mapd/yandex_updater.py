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
NETWORK_ERRORS = frozenset(('network_error', 'timeout', 'worker_timeout'))
PROBE_INTERVAL_S = 10.
WORKER_STALL_S = 65.


def _utc_now_s():
  return datetime.now(UTC).timestamp()


class YandexCameraUpdater:
  def __init__(self, params, state_dir, *, backend=None, connectivity_probe=None, clock=_utc_now_s, monotonic=time.monotonic):
    self.params, self.state_dir = params, Path(state_dir)
    self.backend, self.clock, self.monotonic = backend, clock, monotonic
    self._owns_backend = backend is None
    self._credentials_fingerprint = self._credential_metadata()
    self._lock = threading.RLock()
    self._generation, self._enabled = 0, False
    self._events, self._written = (), {}
    self._approach_contexts = ()
    self._worker = None
    self._workers = []
    self._next_attempt, self._last = -math.inf, -math.inf
    self._point, self._heading = None, None
    self._failures = 0
    self._connectivity_probe = connectivity_probe
    self._network_failure = False
    self._probe_worker = None
    self._next_probe = -math.inf
    self._last_probe_started = -math.inf
    self._worker_started = None
    self._last_fetch_start = -math.inf

  def _reset_network_recovery(self):
    self._network_failure = False
    self._next_probe = -math.inf
    self._worker_started = None

  def _probe_current(self, token):
    with self._lock:
      return self._current(token) and self._network_failure

  def _probe(self, token):
    try:
      if not self._probe_current(token):
        return
      probe = self._connectivity_probe
      if probe is None:
        from openpilot.sunnypilot.mapd.yandex.connectivity import ConnectivityProbe
        probe = ConnectivityProbe()
      online = probe(should_continue=lambda: self._probe_current(token))
      with self._lock:
        if online is not True or not self._probe_current(token):
          return
        # A typed acquisition network failure already establishes unhealthy
        # connectivity. Its first successful probe is a recovery edge.
        self._generation += 1
        self._worker = None
        self._worker_started = None
        self._network_failure = False
        self._failures = 0
        self._last = -math.inf
        self._point = self._heading = None
        self._next_attempt = max(self.monotonic(), self._last_fetch_start + 5.)
        self._put('Error', '')
    except Exception:
      pass  # Probe diagnostics never export exception text or reset auth backoff.
    finally:
      with self._lock:
        if self._probe_worker is threading.current_thread():
          self._probe_worker = None

  def _maybe_probe(self, now):
    if (not self._network_failure or now < max(self._next_probe, self._last_probe_started + PROBE_INTERVAL_S) or
        (self._worker is not None and self._worker.is_alive()) or
        (self._probe_worker is not None and self._probe_worker.is_alive())):
      return
    self._next_probe = now + PROBE_INTERVAL_S
    self._last_probe_started = now
    worker = threading.Thread(target=self._probe, args=(self._generation,), daemon=True, name='yandex-connectivity-probe')
    self._probe_worker = worker
    worker.start()

  def _check_worker_age(self, now):
    if (self._worker is None or not self._worker.is_alive() or self._worker_started is None or
        now - self._worker_started < WORKER_STALL_S):
      return
    # The default subprocess deadline is 60s. This defensive generation fence
    # also cancels a delayed parent/injected backend, preserving the thread cap.
    self._generation += 1
    self._worker = None
    self._worker_started = None
    self._network_failure = True
    self._failures += 1
    self._next_attempt = now + min(300, 30 * 2 ** min(self._failures - 1, 4))
    self._put('Error', 'worker_timeout')
    self._status('error')

  def _credential_metadata(self):
    """Observe atomic configuration replacement without reading its contents."""
    try:
      info = (self.state_dir / 'private' / 'client.json').lstat()
      return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
              info.st_mode, info.st_uid, info.st_gid)
    except OSError as error:
      return ('unavailable', error.errno)

  def _reload_credentials_if_changed(self):
    fingerprint = self._credential_metadata()
    if fingerprint == self._credentials_fingerprint:
      return
    self._credentials_fingerprint = fingerprint
    self._generation += 1
    self._worker = None
    self._events = ()
    self._approach_contexts = ()
    self._point = self._heading = None
    self._last = self._next_attempt = -math.inf
    self._failures = 0
    self._reset_network_recovery()
    if self._owns_backend:
      self.backend = None
    # Keep old worker references so repeated edits cannot exceed the two-worker
    # cap. Their generation tokens immediately stop authorizing publication.
    self._put('Count', 0)
    self._put('UpdatedAt', 0)
    self._put('Error', '')

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

  def _contexts(self, events, now):
    contexts = {}
    for event in events:
      geometry = event.get('approach_geometry')
      observed, expires = event.get('approach_observed_at'), event.get('approach_expires_at')
      if (event.get('approach_source') != 'yandex_native_route' or
          not self._number(observed) or not self._number(expires) or
          not observed <= now + 1 or not now < expires or not 0 < expires - observed <= 300 or
          not self._number(event.get('expires_at')) or expires > event['expires_at'] or
          not isinstance(geometry, (list, tuple)) or not 2 <= len(geometry) <= 512):
        continue
      if any(not isinstance(p, (tuple, list)) or len(p) != 2 or
             not all(self._number(v) for v in p) or abs(p[0]) > 180 or abs(p[1]) >= 85 for p in geometry):
        continue
      points = tuple(tuple(p) for p in geometry)
      if any(abs(a[0] - b[0]) > 180 for a, b in zip(points, points[1:], strict=False)):
        continue
      contexts[points] = (observed, expires, points)
      if len(contexts) == 4:
        break
    return tuple(contexts.values())

  def _left_approach(self, latitude, longitude, heading):
    now = self.clock()
    active = [points for observed, expires, points in self._approach_contexts if observed <= now + 1 and now < expires]
    if not active:
      return False  # Old producers retain the existing time/movement/turn policy.
    cosine = math.cos(math.radians(latitude))
    for points in active:
      nearest = None
      for a, b in zip(points, points[1:], strict=False):
        ax, ay = (a[0] - longitude) * 111320 * cosine, (a[1] - latitude) * 111320
        bx, by = (b[0] - longitude) * 111320 * cosine, (b[1] - latitude) * 111320
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        if length2 <= 0:
          continue
        fraction = max(0., min(1., -(ax * dx + ay * dy) / length2))
        distance = math.hypot(ax + fraction * dx, ay + fraction * dy)
        tangent = math.degrees(math.atan2(dx, dy)) % 360
        if nearest is None or distance < nearest[0]:
          nearest = distance, tangent
      if nearest is not None and nearest[0] <= 12 and abs((heading - nearest[1] + 180) % 360 - 180) <= 35:
        return False
    return True

  def tick(self, latitude, longitude, *, heading, enabled, position_valid):
    with self._lock:
      if bool(enabled) != self._enabled:
        self._enabled = bool(enabled)
        self._generation += 1
        self._worker = None
        self._events = ()
        self._approach_contexts = ()
        self._point = None
        self._last = self._next_attempt = -math.inf
        self._failures = 0
        self._reset_network_recovery()
        self._put('Count', 0)
      if not enabled:
        self._status('disabled')
        return False
      self._reload_credentials_if_changed()
      now = self.monotonic()
      self._check_worker_age(now)
      self._maybe_probe(now)
      if (not position_valid or not all(self._number(v) for v in (latitude, longitude)) or
          not (-85 < latitude < 85 and -180 <= longitude <= 180)):
        self._status('waiting for localized position')
        return False
      if not self._number(heading) or not 0 <= heading < 360:
        self._status('waiting for heading')
        return False
      if self._probe_worker is not None and self._probe_worker.is_alive():
        return False
      if self._worker is not None and self._worker.is_alive():
        return False
      if now < self._next_attempt:
        return False
      moved = self._point is None or math.hypot((latitude - self._point[0]) * 111320,
          (longitude - self._point[1]) * 111320 * math.cos(math.radians(latitude))) >= 1000
      turned = self._heading is None or abs((heading - self._heading + 180) % 360 - 180) >= 30
      departed = self._left_approach(latitude, longitude, heading)
      if not moved and not turned and not departed and now - self._last < 60:
        return False
      self._workers = [worker for worker in self._workers if worker.is_alive()]
      if len(self._workers) >= 2:
        return False
      token = self._generation
      self._next_attempt = now + 5
      self._worker_started = self._last_fetch_start = now
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
          from openpilot.sunnypilot.mapd.yandex.isolated import IsolatedYandexProvider
          self.backend = IsolatedYandexProvider(self.state_dir / 'private' / 'client.json')
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
        self._approach_contexts = self._contexts(combined, self.clock())
        self._last, self._point, self._heading = self.monotonic(), point, heading
        self._failures = self._failures + 1 if partial else 0
        self._network_failure = partial and getattr(result, 'network_failure', False) is True
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
          category = getattr(error, 'category', None)
          self._network_failure = category in NETWORK_ERRORS
          known = NETWORK_ERRORS | {'http_error', 'invalid_service_configuration', 'invalid_timeout', 'unexpected_content_type',
                                    'response_over_budget', 'invalid_request', 'closed', 'invalid_payload'}
          self._put('Error', 'creds_unavailable' if credentials else category if category in known else 'acquisition_error')
          self._status('credentials unavailable' if credentials else 'error')
