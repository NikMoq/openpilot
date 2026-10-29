"""Non-blocking speed-camera database refresh coordinator.

``mapd_manager`` can call :meth:`CameraDatabaseUpdater.tick` from its regular
loop. Network I/O and retry sleeps happen only in a daemon worker thread, so a
1 Hz mapd loop never stalls on DNS, Wi-Fi, or a slow endpoint.
"""

from __future__ import annotations

import math
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from openpilot.sunnypilot.mapd.camera_database import cache_age_seconds, load_database
from openpilot.sunnypilot.mapd.camera_source import DEFAULT_SPEED_CAMERA_URL, download_camera_database


LEGACY_RADARBASE_URL = "https://radarbase.info/export/cache/RU/main_extended.json"
LEGACY_IGO_URL = "https://speedcamonline.ru/igo/Rus/"
DEFAULT_SPEED_CAMERA_VERSION = "speedcamonline-cityguide-RU"
DEFAULT_UPDATE_PERIOD_HOURS = 24.0
DEFAULT_CACHE_MAX_AGE_HOURS = 7 * 24.0
DEFAULT_CACHE_CHECK_PERIOD_S = 30.0
CAMERA_PARAM_KEYS = frozenset({
  "SpeedCameraRoadGraphPath",
  "SpeedCameraRoadGraphStatus",
  "SpeedCameraReactionDistance",
  "SpeedCameraDatabasePath",
  "SpeedCameraDatabaseUrl",
  "SpeedCameraDatabaseUpdatePeriodHours",
  "SpeedCameraDatabaseCacheMaxAgeHours",
  "SpeedCameraDatabaseUpdatedAt",
  "SpeedCameraDatabaseVersion",
  "SpeedCameraDatabaseStatus",
  "SpeedCameraDatabaseError",
  "SpeedCameraDatabaseCount",
  "SpeedCameraControlEnabled",
  "SpeedCameraSpeedOffsetKph",
  "SpeedCameraEnabled",
  "SpeedCameraAlertDistanceMeters",
})


@dataclass(frozen=True)
class CameraUpdaterConfig:
  url: str
  version: str
  path: str
  update_period_s: float
  cache_max_age_s: float


def _default_camera_params_root() -> Path:
  """Match native Params paths without loading hardware or native bindings.

  Paths.comma_home()/Path::params() select the PC or device base directory;
  common/params.cc appends OPENPILOT_PREFIX (default "d"). Explicit empty
  environment values are significant, just as in util::getenv().
  """
  params_root = os.environ.get("PARAMS_ROOT")
  if params_root is None:
    if os.path.isfile("/TICI"):
      params_root = "/data/params"
    else:
      params_root = str(Path.home() / (".comma" + os.environ.get("OPENPILOT_PREFIX", "")) / "params")
  return Path(params_root + "/" + os.environ.get("OPENPILOT_PREFIX", "d"))


class CameraParamsAdapter:
  """Narrow file-backed compatibility layer for camera-only Params.

  This is deliberately independent of the generated Params binding. It can
  bridge an older ``params_pyx`` until the normal registry/binding is rebuilt,
  while refusing access to every non-camera key.
  """

  def __init__(self, root: str | None = None):
    # An explicit root remains the final key directory for existing callers.
    self.root = _default_camera_params_root() if root is None else Path(root)

  def _path(self, key: str) -> Path:
    if key not in CAMERA_PARAM_KEYS:
      raise KeyError(f"unsupported camera param: {key}")
    return self.root / key

  def get(self, key: str, return_default: bool = False):
    try:
      return self._path(key).read_bytes().decode("utf-8", errors="replace")
    except FileNotFoundError:
      return None

  def put(self, key: str, value: object) -> None:
    path = self._path(key)
    self.root.mkdir(parents=True, exist_ok=True)
    data = value.encode("utf-8") if isinstance(value, str) else str(value).encode("utf-8")
    fd, temporary_path = tempfile.mkstemp(prefix=f".{key}.", dir=self.root)
    try:
      with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
      os.replace(temporary_path, path)
    finally:
      if os.path.exists(temporary_path):
        os.unlink(temporary_path)

  def remove(self, key: str) -> None:
    try:
      self._path(key).unlink()
    except FileNotFoundError:
      pass


def _text(value: object, default: str) -> str:
  value = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
  value = str(value).strip() if value is not None else ""
  return value or default


def _url(value: object) -> str:
  candidate = _text(value, DEFAULT_SPEED_CAMERA_URL)
  if candidate in (LEGACY_RADARBASE_URL, LEGACY_IGO_URL):
    return DEFAULT_SPEED_CAMERA_URL
  return candidate if candidate.startswith(("https://", "http://")) else DEFAULT_SPEED_CAMERA_URL


def _positive_float(value: object, default: float, minimum: float, maximum: float) -> float:
  try:
    parsed = float(value)
  except (TypeError, ValueError):
    return default
  if not math.isfinite(parsed):
    return default
  return default if parsed < minimum or parsed > maximum else parsed


def read_config(params, default_path: str) -> CameraUpdaterConfig:
  """Read Params defensively; malformed user values fall back to safe defaults."""
  def get(key):
    return _param_get(params, key)

  url = _url(get("SpeedCameraDatabaseUrl"))
  # Do not feed a stale persisted package label back into the downloader.
  # A custom URL remains custom and is never overwritten by migration.
  version = DEFAULT_SPEED_CAMERA_VERSION if url == DEFAULT_SPEED_CAMERA_URL else "custom-speed-camera"
  return CameraUpdaterConfig(
    url=url,
    version=version,
    path=_text(get("SpeedCameraDatabasePath"), default_path),
    # 0 is an intentional Manual mode: only an explicit request or a missing
    # cache may start a refresh.
    update_period_s=_positive_float(get("SpeedCameraDatabaseUpdatePeriodHours"),
                                    DEFAULT_UPDATE_PERIOD_HOURS, 0.0, 24 * 30) * 3600,
    cache_max_age_s=_positive_float(get("SpeedCameraDatabaseCacheMaxAgeHours"),
                                    DEFAULT_CACHE_MAX_AGE_HOURS, 1.0, 24 * 90) * 3600,
  )


def _param_get(params, key: str):
  """Tolerate an older params binding that does not know new keys."""
  try:
    return params.get(key, return_default=True)
  except Exception:
    return None


def _param_put(params, key: str, value: str) -> None:
  try:
    params.put(key, value)
  except Exception:
    pass


def _param_remove(params, key: str) -> None:
  try:
    params.remove(key)
  except Exception:
    pass


def _display_version(database) -> str:
  """UI label for the exact accepted package, without changing its input version."""
  return database.sha256[:12] if len(database.sha256) >= 12 else database.version


def _migrate_legacy_default_url(params) -> None:
  """Queue a replacement for a retired built-in source, retaining its cache."""
  raw_url = _param_get(params, "SpeedCameraDatabaseUrl")
  if _text(raw_url, "") in (LEGACY_RADARBASE_URL, LEGACY_IGO_URL):
    # Persist the request first: a restart between these writes must not lose
    # the pending refresh after the old URL has disappeared.
    _param_put(params, "SpeedCameraDatabaseStatus", "update requested")
    _param_put(params, "SpeedCameraDatabaseUrl", DEFAULT_SPEED_CAMERA_URL)
    _param_remove(params, "SpeedCameraDatabaseError")


class CameraDatabaseUpdater:
  """Schedule refreshes without blocking the caller's mapd tick."""

  def __init__(self, params=None, default_path: str = "/data/media/0/osm/speed_cameras.json",
               download_fn=download_camera_database, clock=time.time, monotonic=time.monotonic,  # noqa: TID251 - persisted Unix timestamps
               sleep=time.sleep, max_retries: int = 3, initial_backoff_s: float = 5.0,
               max_backoff_s: float = 300.0, cache_check_period_s: float = DEFAULT_CACHE_CHECK_PERIOD_S):
    self.params = params if params is not None else CameraParamsAdapter()
    self.default_path = default_path
    self.download_fn = download_fn
    self.clock = clock
    self.monotonic = monotonic
    self.sleep = sleep
    self.max_retries = max(1, max_retries)
    self.initial_backoff_s = max(0.0, initial_backoff_s)
    self.max_backoff_s = max(self.initial_backoff_s, max_backoff_s)
    self._lock = threading.Lock()
    self._worker: threading.Thread | None = None
    self._next_attempt = 0.0
    self._last_success_monotonic: float | None = None
    self._cache_check_period_s = max(1.0, cache_check_period_s)
    self._cache_checked_at = 0.0
    self._cache_checked_path = ""
    self._cached_age: float | None = None
    self._unusable_cache_path: str | None = None
    _migrate_legacy_default_url(self.params)
    self._restore_cache_state()

  def _restore_cache_state(self) -> None:
    """Expose an already valid cache immediately, without starting network I/O."""
    status = _text(_param_get(self.params, "SpeedCameraDatabaseStatus"), "")
    if status == "downloading" or status.startswith("retrying"):
      # No worker exists in this new process. Never leave the UI disabled by
      # a persisted busy status, including when the old cache is missing.
      self.request_update()
    config = read_config(self.params, self.default_path)
    # Validate once at startup. The cheap header-age probe used on ticks does
    # not prove that the rest of the JSON is readable or contains records.
    database = load_database(config.path)
    if database is None or not database.cameras:
      self._unusable_cache_path = config.path
      self.request_update()
      return
    # Native settings can migrate the URL before this process starts. Detect
    # that case from existing cache metadata instead of adding a cache schema.
    # A custom URL is never migrated, even if its cache came from an old source.
    legacy_cache = (database.provider == "radarbase.info" or database.version == "speedcamonline-igo-RU" or
                    (database.provider == "speedcamonline.ru" and
                     any(camera.camera_type.startswith("speedcamonline:") for camera in database.cameras)))
    if config.url == DEFAULT_SPEED_CAMERA_URL and legacy_cache:
      _param_put(self.params, "SpeedCameraDatabaseStatus", "update requested")
      _param_remove(self.params, "SpeedCameraDatabaseError")
    if max(0.0, self.clock() - database.fetched_at) > config.cache_max_age_s:
      return
    _param_put(self.params, "SpeedCameraDatabaseCount", str(len(database.cameras)))
    _param_put(self.params, "SpeedCameraDatabaseVersion", _display_version(database))
    status = _text(_param_get(self.params, "SpeedCameraDatabaseStatus"), "")
    if status not in ("update requested", "downloading") and not status.startswith("retrying"):
      _param_put(self.params, "SpeedCameraDatabaseStatus", "ready")
    if self._safe_timestamp(_param_get(self.params, "SpeedCameraDatabaseUpdatedAt")) <= 0:
      _param_put(self.params, "SpeedCameraDatabaseUpdatedAt", str(database.fetched_at))

  def request_update(self) -> None:
    """Request an immediate refresh; the actual download starts on the next tick."""
    _param_put(self.params, "SpeedCameraDatabaseStatus", "update requested")
    _param_remove(self.params, "SpeedCameraDatabaseError")
    with self._lock:
      self._next_attempt = 0.0
      self._cache_checked_at = 0.0

  def tick(self, force: bool = False) -> bool:
    """Start at most one worker and return immediately.

    Returns ``True`` only when a worker was started. ``force`` is intended for
    a UI request; normal calls use the configured update period and cache age.
    """
    config = read_config(self.params, self.default_path)
    status = _text(_param_get(self.params, "SpeedCameraDatabaseStatus"), "")
    manual_request = force or status == "update requested"
    requested = manual_request
    if not requested:
      now = self.clock()
      if (config.path != self._cache_checked_path or
          now - self._cache_checked_at >= self._cache_check_period_s):
        self._cached_age = cache_age_seconds(config.path, now=now)
        self._cache_checked_path = config.path
        self._cache_checked_at = now
      age = self._cached_age
      updated_at = self._safe_timestamp(_param_get(self.params, "SpeedCameraDatabaseUpdatedAt"))
      requested = age is None or config.path == self._unusable_cache_path
      if config.update_period_s > 0:
        requested = requested or age > config.cache_max_age_s or now - updated_at >= config.update_period_s
    if not requested:
      return False

    with self._lock:
      if self._worker is not None and self._worker.is_alive():
        return False
      if self._last_success_monotonic is not None:
        # Reflect interval edits while idle without treating an old producer
        # timestamp as evidence that no successful refresh has just occurred.
        self._next_attempt = self._last_success_monotonic + max(config.update_period_s, self._cache_check_period_s)
      if not manual_request and self.monotonic() < self._next_attempt:
        return False
      self._last_success_monotonic = None  # A failed attempt uses its own backoff.
      self._worker = threading.Thread(target=self._run, args=(config,), name="camera-db-updater", daemon=True)
      self._worker.start()
      return True

  def _requeue_if_config_changed(self, config: CameraUpdaterConfig) -> bool:
    latest = read_config(self.params, self.default_path)
    if (latest.url, latest.path) != (config.url, config.path):
      self.request_update()
      return True
    return False

  def _run(self, config: CameraUpdaterConfig) -> None:
    try:
      self._run_config(config)
    finally:
      # Qt writes Params without our lock. Recheck after terminal status
      # writes too, so an edit interleaved with ready/error stays queued.
      self._requeue_if_config_changed(config)

  def _run_config(self, config: CameraUpdaterConfig) -> None:
    if self._requeue_if_config_changed(config):
      return
    _param_put(self.params, "SpeedCameraDatabaseStatus", "downloading")
    last_error = ""
    for attempt in range(self.max_retries):
      if self._requeue_if_config_changed(config):
        return
      try:
        database = self.download_fn(config.url, config.path, config.version)
        if self._requeue_if_config_changed(config):
          return
        _param_put(self.params, "SpeedCameraDatabaseUpdatedAt", str(database.fetched_at))
        _param_put(self.params, "SpeedCameraDatabaseVersion", _display_version(database))
        _param_put(self.params, "SpeedCameraDatabaseCount", str(len(database.cameras)))
        _param_remove(self.params, "SpeedCameraDatabaseError")
        _param_put(self.params, "SpeedCameraDatabaseStatus", "ready")
        with self._lock:
          # Mirrors retain the producer's fetched_at. Schedule the next check
          # separately so an accepted older snapshot cannot cause a 1 Hz loop.
          self._last_success_monotonic = self.monotonic()
          self._next_attempt = self._last_success_monotonic + max(config.update_period_s, self._cache_check_period_s)
          self._cache_checked_at = 0.0
          if self._unusable_cache_path == config.path:
            self._unusable_cache_path = None
        return
      except Exception as error:  # network and payload errors are reported, not fatal to mapd
        if self._requeue_if_config_changed(config):
          return
        last_error = str(error) or error.__class__.__name__
        if attempt + 1 >= self.max_retries:
          break
        delay = min(self.initial_backoff_s * (2 ** attempt), self.max_backoff_s)
        _param_put(self.params, "SpeedCameraDatabaseStatus",
                   f"retrying in {int(delay)}s ({attempt + 1}/{self.max_retries - 1})")
        self.sleep(delay)

    _param_put(self.params, "SpeedCameraDatabaseError", last_error)
    _param_put(self.params, "SpeedCameraDatabaseStatus", "error")
    with self._lock:
      self._next_attempt = self.monotonic() + self.max_backoff_s

  @staticmethod
  def _safe_timestamp(value: object) -> float:
    try:
      parsed = float(value or 0)
      return parsed if math.isfinite(parsed) and parsed >= 0 else 0.0
    except (TypeError, ValueError):
      return 0.0


# Public compatibility name used by mapd and the native UI integration.
CameraParams = CameraParamsAdapter
