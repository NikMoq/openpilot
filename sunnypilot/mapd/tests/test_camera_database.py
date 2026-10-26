"""Cache freshness uses persisted Unix timestamps, not process-relative time."""
# ruff: noqa: TID251

import ast
import json
import os
from pathlib import Path
from types import SimpleNamespace
import threading
import time
import pytest

from openpilot.sunnypilot.mapd.camera_database import CameraDatabase, cache_age_seconds, load_database, save_database
from openpilot.sunnypilot.mapd.camera_updater import (
  CameraDatabaseUpdater, CameraParams, CameraParamsAdapter, DEFAULT_SPEED_CAMERA_URL,
  DEFAULT_SPEED_CAMERA_VERSION, LEGACY_IGO_URL, LEGACY_RADARBASE_URL, read_config,
)
from openpilot.sunnypilot.mapd.speed_cameras import SpeedCamera


def test_camera_database_round_trip(tmp_path: Path):
  path = tmp_path / "speed_cameras.json"
  database = CameraDatabase(
    version="replay-1",
    fetched_at=1_700_000_000,
    cameras=(SpeedCamera(55.75, 37.61, 60, 180, "1"),),
  )
  save_database(str(path), database)
  loaded = load_database(str(path))
  assert loaded is not None
  assert loaded.version == "replay-1"
  assert loaded.cameras == database.cameras


def test_camera_database_rejects_corrupt_snapshot(tmp_path: Path):
  path = tmp_path / "speed_cameras.json"
  path.write_text('{"version": "broken", "data": [', encoding="utf-8")
  assert load_database(str(path)) is None


def test_cache_age_and_future_timestamp(tmp_path: Path):
  path = tmp_path / "speed_cameras.json"
  save_database(str(path), CameraDatabase("v1", 100.0, (SpeedCamera(55.75, 37.61, 60, 180, "1"),)))
  assert cache_age_seconds(str(path), now=130.0) == 30.0
  assert cache_age_seconds(str(path), now=90.0) == 0.0
  assert load_database(str(path), max_age_s=20.0) is None


def test_cache_age_reads_header_without_parsing_full_payload(tmp_path: Path):
  path = tmp_path / "speed_cameras.json"
  path.write_bytes(b'{"version":"v1","fetched_at":100.0,"data":' + b'9' * 10_000_000 + b'}')
  assert cache_age_seconds(str(path), now=130.0) == 30.0


class FakeParams:
  def __init__(self, values=None):
    self.values = dict(values or {})

  def get(self, key, return_default=False):
    return self.values.get(key)

  def put(self, key, value):
    self.values[key] = value

  def remove(self, key):
    self.values.pop(key, None)


def test_malformed_params_use_bounded_defaults():
  config = read_config(FakeParams({
    "SpeedCameraDatabaseUpdatePeriodHours": "nan",
    "SpeedCameraDatabaseCacheMaxAgeHours": "-10",
    "SpeedCameraDatabaseUrl": "",
  }), "/tmp/cameras.json")
  assert config.url.startswith("https://")
  assert config.update_period_s == 24 * 3600
  assert config.cache_max_age_s == 7 * 24 * 3600
  assert config.path == "/tmp/cameras.json"

  manual = read_config(FakeParams({"SpeedCameraDatabaseUpdatePeriodHours": "0"}), "/tmp/cameras.json")
  assert manual.update_period_s == 0
  assert CameraParams is CameraParamsAdapter

  config = read_config(FakeParams({"SpeedCameraDatabaseUrl": "file:///etc/passwd"}), "/tmp/cameras.json")
  assert config.url.startswith("https://")


def test_camera_params_adapter_is_atomic_and_whitelisted(tmp_path: Path):
  adapter = CameraParamsAdapter(str(tmp_path / "params"))
  adapter.put("SpeedCameraDatabaseStatus", "ready")
  assert adapter.get("SpeedCameraDatabaseStatus") == "ready"
  with pytest.raises(KeyError):
    adapter.put("GitBranch", "dev")


def test_updater_restores_ready_cache_and_count(tmp_path: Path):
  cache = tmp_path / "speed_cameras.json"
  save_database(str(cache), CameraDatabase("v2", time.time(), (
    SpeedCamera(55.75, 37.61, 60, 180, "1"),
    SpeedCamera(55.76, 37.62, 40, 180, "2"),
  )))
  params = CameraParamsAdapter(str(tmp_path / "params"))
  params.put("SpeedCameraDatabasePath", str(cache))
  CameraDatabaseUpdater(params=params, default_path=str(cache))
  assert params.get("SpeedCameraDatabaseStatus") == "ready"
  assert params.get("SpeedCameraDatabaseCount") == "2"
  assert params.get("SpeedCameraDatabaseVersion") == "v2"


def test_updater_checks_cache_age_at_most_every_30_seconds(tmp_path: Path, monkeypatch):
  import openpilot.sunnypilot.mapd.camera_updater as updater_module

  cache = tmp_path / "speed_cameras.json"
  save_database(str(cache), CameraDatabase("v1", 100.0, (SpeedCamera(55.75, 37.61, 60, 180, "1"),)))
  params = CameraParamsAdapter(str(tmp_path / "params"))
  params.put("SpeedCameraDatabasePath", str(cache))
  params.put("SpeedCameraDatabaseUpdatePeriodHours", "0")
  now = [100.0]
  checks = []
  monkeypatch.setattr(updater_module, "cache_age_seconds",
                      lambda path, now=None: checks.append((path, now)) or 0.0)
  updater = CameraDatabaseUpdater(params=params, default_path=str(cache), clock=lambda: now[0])
  assert updater.tick() is False
  assert updater.tick() is False
  assert len(checks) == 1
  now[0] = 131.0
  assert updater.tick() is False
  assert len(checks) == 2


def test_tick_starts_network_work_outside_caller(tmp_path: Path):
  params = FakeParams({"SpeedCameraDatabaseStatus": "update requested"})
  calls = []

  def download(url, path, version):
    calls.append((url, path, version))
    return CameraDatabase(version, time.time(), (SpeedCamera(55.75, 37.61, 60, 180, "1"),))

  updater = CameraDatabaseUpdater(params=params, default_path=str(tmp_path / "cameras.json"),
                                   download_fn=download, initial_backoff_s=0)
  assert updater.tick() is True
  updater._worker.join(timeout=2)
  assert calls
  assert params.values["SpeedCameraDatabaseStatus"] == "ready"


def test_updater_retries_and_preserves_last_error(tmp_path: Path):
  params = FakeParams({"SpeedCameraDatabaseStatus": "update requested"})
  attempts = []

  def download(url, path, version):
    attempts.append(1)
    raise OSError("offline")

  updater = CameraDatabaseUpdater(params=params, default_path=str(tmp_path / "cameras.json"),
                                   download_fn=download, sleep=lambda _: None, max_retries=3,
                                   initial_backoff_s=1, max_backoff_s=2)
  assert updater.tick() is True
  updater._worker.join(timeout=2)
  assert len(attempts) == 3
  assert params.values["SpeedCameraDatabaseStatus"] == "error"
  assert params.values["SpeedCameraDatabaseError"] == "offline"


@pytest.mark.parametrize("device", [False, True], ids=["pc", "tici"])
@pytest.mark.parametrize("prefix", [None, "isolated", ""])
@pytest.mark.parametrize("root_override", [None, "custom", ""])
def test_default_adapter_matches_native_params_paths(tmp_path, monkeypatch, device, prefix, root_override):
  # Independent path expectations from common/params.cc + system/hardware/hw.h.
  # Exercise path selection only; device paths are never created or modified.
  fake_home = tmp_path / "home"
  monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
  real_isfile = os.path.isfile
  monkeypatch.setattr(os.path, "isfile", lambda path: device if path == "/TICI" else real_isfile(path))
  for key, value in (("OPENPILOT_PREFIX", prefix), ("PARAMS_ROOT", root_override)):
    if value is None:
      monkeypatch.delenv(key, raising=False)
    else:
      monkeypatch.setenv(key, str(tmp_path / "custom-params") if key == "PARAMS_ROOT" and value == "custom" else value)

  suffix = "d" if prefix is None else prefix
  if root_override == "":
    expected = Path("/" + suffix)
  elif root_override == "custom":
    expected = tmp_path / "custom-params" / suffix
  elif device:
    expected = Path("/data/params") / suffix
  else:
    expected = fake_home / (".comma" + (prefix or "")) / "params" / suffix
  assert CameraParams().root == expected


def test_adapter_and_native_layout_share_requests_and_status(tmp_path, monkeypatch):
  monkeypatch.setenv("PARAMS_ROOT", str(tmp_path / "params"))
  monkeypatch.setenv("OPENPILOT_PREFIX", "smoke")
  native_key_dir = tmp_path / "params" / "smoke"
  native_key_dir.mkdir(parents=True)
  request = native_key_dir / "SpeedCameraDatabaseStatus"
  request.write_text("update requested", encoding="utf-8")
  adapter = CameraParams()
  assert adapter.get("SpeedCameraDatabaseStatus") == "update requested"
  adapter.put("SpeedCameraDatabaseStatus", "ready")
  assert request.read_text(encoding="utf-8") == "ready"
  adapter.remove("SpeedCameraDatabaseStatus")
  assert not request.exists()
  assert not list(native_key_dir.glob(".*"))


def test_explicit_adapter_root_is_final_directory_even_with_env(tmp_path, monkeypatch):
  monkeypatch.setenv("PARAMS_ROOT", str(tmp_path / "ignored"))
  monkeypatch.setenv("OPENPILOT_PREFIX", "ignored")
  root = tmp_path / "explicit-keys"
  adapter = CameraParams(str(root))
  adapter.put("SpeedCameraDatabaseStatus", "ready")
  assert (root / "SpeedCameraDatabaseStatus").read_text() == "ready"
  assert not (tmp_path / "ignored").exists()


@pytest.mark.parametrize("legacy_url", [LEGACY_RADARBASE_URL, LEGACY_IGO_URL])
@pytest.mark.parametrize("period", ["0", "24"])
def test_source_migration_queues_refresh_preserves_cache_and_survives_failure(tmp_path, legacy_url, period):
  cache = tmp_path / "cache.json"
  old = CameraDatabase("legacy-test", time.time(), (SpeedCamera(55, 37, 60),), "retained-provider", "a" * 64)
  save_database(str(cache), old)
  original_bytes = cache.read_bytes()
  params = CameraParams(str(tmp_path / "params"))
  for key, value in {
    "SpeedCameraDatabasePath": str(cache), "SpeedCameraDatabaseUrl": legacy_url,
    "SpeedCameraDatabaseUpdatePeriodHours": period, "SpeedCameraDatabaseUpdatedAt": str(old.fetched_at),
    "SpeedCameraDatabaseStatus": "ready", "SpeedCameraDatabaseError": "previous error",
  }.items():
    params.put(key, value)

  entered, release = threading.Event(), threading.Event()
  calls = []

  def offline(url, path, version):
    calls.append((url, path, version))
    entered.set()
    assert release.wait(timeout=2)
    raise OSError("local offline probe")

  options = dict(params=params, default_path=str(cache), download_fn=offline, max_retries=1)
  CameraDatabaseUpdater(**options)
  # A restart before the first tick must preserve the persisted request even
  # though the URL is already migrated and the retained cache is still fresh.
  updater = CameraDatabaseUpdater(**options)
  assert params.get("SpeedCameraDatabaseUrl") == DEFAULT_SPEED_CAMERA_URL
  assert params.get("SpeedCameraDatabaseStatus") == "update requested"
  assert params.get("SpeedCameraDatabaseError") is None
  assert params.get("SpeedCameraDatabaseCount") == "1"
  assert params.get("SpeedCameraDatabaseVersion") == "a" * 12
  assert params.get("SpeedCameraDatabaseUpdatedAt") == str(old.fetched_at)
  assert cache.read_bytes() == original_bytes and not calls

  assert updater.tick()
  try:
    assert entered.wait(timeout=2)
    assert updater.tick() is False
    assert params.get("SpeedCameraDatabaseStatus") == "downloading"
    assert cache.read_bytes() == original_bytes
  finally:
    release.set()
    updater._worker.join(timeout=2)
  assert not updater._worker.is_alive()
  assert calls == [(DEFAULT_SPEED_CAMERA_URL, str(cache), DEFAULT_SPEED_CAMERA_VERSION)]
  assert params.get("SpeedCameraDatabaseStatus") == "error"
  assert load_database(str(cache)) == old and cache.read_bytes() == original_bytes

  new = CameraDatabase(DEFAULT_SPEED_CAMERA_VERSION, time.time(), (SpeedCamera(56, 38, 80),), "speedcamonline.ru", "b" * 64)

  def successful(url, path, version):
    assert (url, version) == (DEFAULT_SPEED_CAMERA_URL, DEFAULT_SPEED_CAMERA_VERSION)
    save_database(path, new)
    return new

  updater.download_fn = successful
  updater.request_update()
  assert updater.tick()  # Explicit request bypasses backoff from the failed attempt.
  updater._worker.join(timeout=2)
  assert not updater._worker.is_alive()
  assert params.get("SpeedCameraDatabaseStatus") == "ready"
  assert params.get("SpeedCameraDatabaseVersion") == "b" * 12
  assert params.get("SpeedCameraDatabaseError") is None
  assert load_database(str(cache)) == new


@pytest.mark.parametrize("provider,version,kind", [
  ("radarbase.info", "old-custom-label", "stationary"),
  ("speedcamonline.ru", "old-custom-label", "speedcamonline:1"),
  ("", "speedcamonline-igo-RU", "speedcamonline:1"),
])
@pytest.mark.parametrize("custom_url", [False, True])
def test_ui_migrated_url_refreshes_known_old_cache_but_custom_source_is_preserved(
    tmp_path, provider, version, kind, custom_url):
  cache = tmp_path / "cache.json"
  old = CameraDatabase(version, time.time(), (SpeedCamera(55, 37, 60, camera_type=kind),), provider)
  save_database(str(cache), old)
  original_bytes = cache.read_bytes()
  url = "https://example.invalid/my-database" if custom_url else DEFAULT_SPEED_CAMERA_URL
  params = FakeParams({"SpeedCameraDatabaseUrl": url, "SpeedCameraDatabasePath": str(cache),
                       "SpeedCameraDatabaseUpdatePeriodHours": "0", "SpeedCameraDatabaseUpdatedAt": str(old.fetched_at)})
  updater = CameraDatabaseUpdater(params=params)
  assert params.values["SpeedCameraDatabaseUrl"] == url
  assert params.values["SpeedCameraDatabaseStatus"] == ("ready" if custom_url else "update requested")
  assert cache.read_bytes() == original_bytes and load_database(str(cache)) == old
  if custom_url:
    assert updater.tick() is False


def test_current_cityguide_cache_does_not_request_another_migration(tmp_path):
  cache = tmp_path / "cache.json"
  save_database(str(cache), CameraDatabase(DEFAULT_SPEED_CAMERA_VERSION, time.time(), (
    SpeedCamera(55, 37, 60, camera_type="speedcamonline-cg77:18059"),), "speedcamonline.ru"))
  params = FakeParams({"SpeedCameraDatabaseUrl": DEFAULT_SPEED_CAMERA_URL, "SpeedCameraDatabasePath": str(cache),
                       "SpeedCameraDatabaseUpdatePeriodHours": "0"})
  updater = CameraDatabaseUpdater(params=params)
  assert params.values["SpeedCameraDatabaseStatus"] == "ready"
  assert updater.tick() is False


@pytest.mark.parametrize("already_exists", [False, True])
def test_mapd_startup_reaches_camera_tick_without_osm_or_parent_directories(tmp_path, already_exists):
  # Execute the actual main_thread body while isolating native messaging and
  # scheduling. This verifies startup control flow, not full Cereal runtime.
  path = Path(__file__).resolve().parents[1] / "mapd_manager.py"
  function = next(node for node in ast.parse(path.read_text(encoding="utf-8")).body
                  if isinstance(node, ast.FunctionDef) and node.name == "main_thread")
  root = tmp_path / "new-profile" / "media" / "0" / "osm"
  if already_exists:
    root.mkdir(parents=True)
  calls = []

  class FinishedTick(Exception):
    pass

  def keep_time():
    raise FinishedTick

  environment = {
    "os": os, "Paths": SimpleNamespace(mapd_root=lambda: str(root)), "VERSION": "test",
    "params": SimpleNamespace(get_bool=lambda key: False), "CameraParams": lambda: None,
    "update_installed_version": lambda *args: None, "config_realtime_process": lambda *args: None,
    "Ratekeeper": lambda *args, **kwargs: SimpleNamespace(keep_time=keep_time),
    "OsmMapData": lambda: SimpleNamespace(tick=lambda: calls.append("publish")),
    "CameraDatabaseUpdater": lambda **kwargs: SimpleNamespace(tick=lambda: calls.append("camera.tick")),
    "cloudlog": SimpleNamespace(exception=lambda message: pytest.fail(message)),
    "get_files_for_cleanup": list, "set_offroad_alert": lambda *args: None,
    "update_osm_db": lambda: calls.append("osm-check"),
  }
  exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), environment)
  with pytest.raises(FinishedTick):
    environment["main_thread"]()
  assert root.is_dir()
  assert calls == ["osm-check", "camera.tick", "publish"]


@pytest.mark.parametrize("status", ["downloading", "retrying in 5s (1/2)"])
@pytest.mark.parametrize("cache_state", ["valid", "missing", "corrupt"])
@pytest.mark.parametrize("period", ["0", "24"])
def test_startup_resumes_orphan_worker_regardless_of_cache(tmp_path, status, cache_state, period):
  cache = tmp_path / "cache.json"
  now = time.time()
  database = CameraDatabase("current", now, (SpeedCamera(55, 37, 60),))
  if cache_state == "valid":
    save_database(str(cache), database)
  elif cache_state == "corrupt":
    cache.write_text(f'{{"fetched_at":{now},"data":[', encoding="utf-8")
  original = cache.read_bytes() if cache.exists() else None
  params = FakeParams({"SpeedCameraDatabasePath": str(cache), "SpeedCameraDatabaseStatus": status,
                       "SpeedCameraDatabaseUpdatedAt": str(now), "SpeedCameraDatabaseUpdatePeriodHours": period})
  calls = []

  def download(url, path, version):
    calls.append((url, path))
    save_database(path, database)
    return database

  updater = CameraDatabaseUpdater(params=params, download_fn=download, max_retries=1)
  assert params.get("SpeedCameraDatabaseStatus") == "update requested"
  assert (cache.read_bytes() if cache.exists() else None) == original and not calls
  assert updater.tick()
  updater._worker.join(timeout=2)
  assert not updater._worker.is_alive() and len(calls) == 1
  assert params.get("SpeedCameraDatabaseStatus") == "ready"
  assert load_database(str(cache)) == database


@pytest.mark.parametrize("period", ["0", "24"])
@pytest.mark.parametrize("invalid_data", ["truncated-json", "empty-records"])
def test_invalid_fresh_cache_is_checked_once_and_retried_after_backoff(tmp_path, monkeypatch, period, invalid_data):
  import openpilot.sunnypilot.mapd.camera_updater as updater_module

  cache = tmp_path / "cache.json"
  now = time.time()
  if invalid_data == "truncated-json":
    cache.write_text(f'{{"fetched_at":{now},"data":[', encoding="utf-8")
  else:
    cache.write_text(json.dumps({"fetched_at": now, "data": []}), encoding="utf-8")
  original = cache.read_bytes()
  assert cache_age_seconds(str(cache), now=now) == 0
  params = FakeParams({"SpeedCameraDatabasePath": str(cache), "SpeedCameraDatabaseStatus": "ready",
                       "SpeedCameraDatabaseUpdatedAt": str(now), "SpeedCameraDatabaseUpdatePeriodHours": period})
  loads, calls = [], []
  monotonic = [100.0]

  def counted_load(*args, **kwargs):
    loads.append(args[0])
    return load_database(*args, **kwargs)

  def offline(*args):
    calls.append(args)
    raise OSError("offline")

  monkeypatch.setattr(updater_module, "load_database", counted_load)
  updater = CameraDatabaseUpdater(params=params, download_fn=offline, max_retries=1,
                                  monotonic=lambda: monotonic[0], max_backoff_s=60)
  assert params.get("SpeedCameraDatabaseStatus") == "update requested"
  assert updater.tick()
  updater._worker.join(timeout=2)
  assert not updater._worker.is_alive()
  assert params.get("SpeedCameraDatabaseStatus") == "error" and len(calls) == 1
  for _ in range(100):
    assert updater.tick() is False
  assert loads == [str(cache)]  # No full-cache parse on regular mapd ticks.
  assert cache.read_bytes() == original

  monotonic[0] += 60
  assert updater.tick()  # A fresh-looking header must not suppress recovery.
  updater._worker.join(timeout=2)
  assert not updater._worker.is_alive() and len(calls) == 2
  repaired = CameraDatabase("repaired", time.time(), (SpeedCamera(55, 37, 60),))

  def success(url, path, version):
    save_database(path, repaired)
    return repaired

  updater.download_fn = success
  updater.request_update()
  assert updater.tick()
  updater._worker.join(timeout=2)
  assert not updater._worker.is_alive()
  assert updater.tick() is False and loads == [str(cache)]
  assert params.get("SpeedCameraDatabaseStatus") == "ready"
  assert load_database(str(cache)) == repaired


@pytest.mark.parametrize("change", ["url", "path", "both"])
@pytest.mark.parametrize("phase,old_fails", [
  ("download", False), ("download", True), ("terminal-status", False), ("terminal-status", True), ("backoff", True),
])
def test_latest_source_request_survives_old_worker_completion(tmp_path, change, old_fails, phase):
  old_url, new_url = "https://example.invalid/old", "https://example.invalid/new"
  old_path, new_path = str(tmp_path / "old.json"), str(tmp_path / "new.json")
  now = time.time()
  baseline = CameraDatabase("retained", now, (SpeedCamera(55, 37, 60),), sha256="a" * 64)
  save_database(old_path, baseline)
  save_database(new_path, baseline)
  desired_url = old_url if change == "path" else new_url
  desired_path = old_path if change == "url" else new_path

  class InterleavedParams(FakeParams):
    terminal_hook = None

    def put(self, key, value):
      if self.terminal_hook is not None and key == "SpeedCameraDatabaseStatus" and value in ("ready", "error"):
        hook, self.terminal_hook = self.terminal_hook, None
        hook()  # UI writes just before the old worker's terminal status write.
      super().put(key, value)

  params = InterleavedParams({"SpeedCameraDatabaseUrl": old_url, "SpeedCameraDatabasePath": old_path,
                              "SpeedCameraDatabaseStatus": "update requested", "SpeedCameraDatabaseUpdatedAt": str(now),
                              "SpeedCameraDatabaseUpdatePeriodHours": "0"})

  def edit_source():
    params.put("SpeedCameraDatabaseUrl", desired_url)
    params.put("SpeedCameraDatabasePath", desired_path)
    params.put("SpeedCameraDatabaseStatus", "update requested")

  calls, sleeps = [], []
  entered, release = threading.Event(), threading.Event()
  old_result = CameraDatabase("old-result", now, baseline.cameras * 2, sha256="b" * 64)
  new_result = CameraDatabase("new-result", now, baseline.cameras * 3, sha256="c" * 64)

  def download(url, path, version):
    calls.append((url, path))
    if len(calls) == 1:
      entered.set()
      assert release.wait(timeout=2)
      if old_fails:
        raise OSError("old-source failure")
      result = old_result
    else:
      assert (url, path) == (desired_url, desired_path)
      result = new_result
    save_database(path, result)
    return result

  def sleep(delay):
    sleeps.append(delay)
    edit_source()

  updater = CameraDatabaseUpdater(params=params, download_fn=download, sleep=sleep,
                                  max_retries=3 if phase == "backoff" else 1)
  if phase == "terminal-status":
    params.terminal_hook = edit_source
  assert updater.tick()
  try:
    assert entered.wait(timeout=2)
    if phase == "download":
      edit_source()
      assert updater.tick() is False  # No second worker until this one exits.
  finally:
    release.set()
    updater._worker.join(timeout=2)
  assert not updater._worker.is_alive()
  assert calls == [(old_url, old_path)]  # No retries of a superseded source.
  assert len(sleeps) == (1 if phase == "backoff" else 0)
  assert params.get("SpeedCameraDatabaseStatus") == "update requested"
  assert params.get("SpeedCameraDatabaseError") is None
  if phase != "terminal-status":
    assert params.get("SpeedCameraDatabaseVersion") == "a" * 12
  assert updater.tick()  # Latest request starts despite Manual and fresh cache.
  updater._worker.join(timeout=2)
  assert not updater._worker.is_alive()
  assert calls == [(old_url, old_path), (desired_url, desired_path)]
  assert params.get("SpeedCameraDatabaseStatus") == "ready"
  assert params.get("SpeedCameraDatabaseVersion") == "c" * 12
  assert params.get("SpeedCameraDatabaseCount") == "3"
  assert load_database(desired_path) == new_result


@pytest.mark.parametrize("period", [0, 6, 24, 168])
def test_successful_old_mirror_respects_monotonic_interval_and_producer_date(tmp_path, period):
  cache = str(tmp_path / "cache.json")
  wallclock, monotonic = [1_800_000_000.0], [100.0]
  producer_date = wallclock[0] - 10 * 24 * 3600
  params = FakeParams({"SpeedCameraDatabasePath": cache, "SpeedCameraDatabaseUpdatePeriodHours": str(period),
                       "SpeedCameraDatabaseCacheMaxAgeHours": "720"})
  calls = []

  def download(url, path, version):
    calls.append((url, path, version))
    monotonic[0] += 7  # Schedule from completion, not the start of this attempt.
    database = CameraDatabase(version, producer_date, (SpeedCamera(55, 37, 60),), "speedcamonline.ru", "d" * 64)
    save_database(path, database)
    return database

  updater = CameraDatabaseUpdater(params=params, download_fn=download, clock=lambda: wallclock[0],
                                  monotonic=lambda: monotonic[0], max_retries=1)
  assert updater.tick()
  updater._worker.join(timeout=2)
  assert not updater._worker.is_alive() and len(calls) == 1
  assert float(params.get("SpeedCameraDatabaseUpdatedAt")) == producer_date
  assert load_database(cache).fetched_at == producer_date
  completed_at = monotonic[0]
  assert updater.tick() is False
  interval = max(period * 3600, 30)
  monotonic[0] = completed_at + interval - 0.001
  wallclock[0] += 100 * 24 * 3600  # An NTP jump cannot expire the monotonic wait.
  assert updater.tick() is False
  monotonic[0] = completed_at + interval
  if period:
    assert updater.tick()
    updater._worker.join(timeout=2)
    assert not updater._worker.is_alive() and len(calls) == 2
    assert updater.tick() is False
  else:
    # Manual does not auto-refresh an existing cache, even beyond its max age.
    monotonic[0] += 365 * 24 * 3600
    assert updater.tick() is False and len(calls) == 1
  assert float(params.get("SpeedCameraDatabaseUpdatedAt")) == producer_date
  assert load_database(cache).fetched_at == producer_date


@pytest.mark.parametrize("period", [0, 6, 24, 168])
@pytest.mark.parametrize("request_kind", ["force", "ui-status", "request_update"])
def test_manual_request_bypasses_success_interval(tmp_path, period, request_kind):
  cache = str(tmp_path / "cache.json")
  now = time.time()
  params = FakeParams({"SpeedCameraDatabasePath": cache, "SpeedCameraDatabaseUpdatePeriodHours": str(period)})
  calls = []

  def download(url, path, version):
    calls.append(url)
    database = CameraDatabase(version, now - 3600, (SpeedCamera(55, 37, 60),))
    save_database(path, database)
    return database

  updater = CameraDatabaseUpdater(params=params, download_fn=download, monotonic=lambda: 100.0)
  assert updater.tick()
  updater._worker.join(timeout=2)
  assert not updater._worker.is_alive()
  assert updater.tick() is False
  if request_kind == "ui-status":
    params.put("SpeedCameraDatabaseStatus", "update requested")
  elif request_kind == "request_update":
    updater.request_update()
  assert updater.tick(force=request_kind == "force")
  updater._worker.join(timeout=2)
  assert not updater._worker.is_alive() and len(calls) == 2
  assert params.get("SpeedCameraDatabaseStatus") == "ready"


@pytest.mark.parametrize("old_fails", [False, True])
@pytest.mark.parametrize("change", ["url", "path"])
def test_ui_source_edit_bypasses_idle_success_or_failure_backoff(tmp_path, old_fails, change):
  original_path, new_path = str(tmp_path / "old.json"), str(tmp_path / "new.json")
  old_url, new_url = "https://example.invalid/old", "https://example.invalid/new"
  now = time.time()
  params = FakeParams({"SpeedCameraDatabasePath": original_path, "SpeedCameraDatabaseUrl": old_url,
                       "SpeedCameraDatabaseUpdatePeriodHours": "168"})
  calls = []

  def download(url, path, version):
    calls.append((url, path))
    if len(calls) == 1 and old_fails:
      raise OSError("old source unavailable")
    database = CameraDatabase(version, now - 3600, (SpeedCamera(55, 37, 60),))
    save_database(path, database)
    return database

  updater = CameraDatabaseUpdater(params=params, download_fn=download, monotonic=lambda: 100.0, max_retries=1)
  assert updater.tick()
  updater._worker.join(timeout=2)
  assert not updater._worker.is_alive()
  assert updater.tick() is False
  key, value = ("SpeedCameraDatabaseUrl", new_url) if change == "url" else ("SpeedCameraDatabasePath", new_path)
  params.put(key, value)
  params.put("SpeedCameraDatabaseStatus", "update requested")
  assert updater.tick()
  updater._worker.join(timeout=2)
  assert not updater._worker.is_alive()
  assert calls == [(old_url, original_path), (new_url if change == "url" else old_url,
                                             new_path if change == "path" else original_path)]
  assert params.get("SpeedCameraDatabaseStatus") == "ready"


def test_failure_after_success_uses_failure_backoff_not_previous_update_interval(tmp_path):
  cache = str(tmp_path / "cache.json")
  now, monotonic = time.time(), [100.0]
  params = FakeParams({"SpeedCameraDatabasePath": cache, "SpeedCameraDatabaseUpdatePeriodHours": "168",
                       "SpeedCameraDatabaseCacheMaxAgeHours": "720"})
  calls = []

  def download(url, path, version):
    calls.append(url)
    if len(calls) == 2:
      raise OSError("temporary failure")
    database = CameraDatabase(version, now - 10 * 24 * 3600, (SpeedCamera(55, 37, 60),))
    save_database(path, database)
    return database

  updater = CameraDatabaseUpdater(params=params, download_fn=download, monotonic=lambda: monotonic[0],
                                  max_retries=1, max_backoff_s=60)
  assert updater.tick()
  updater._worker.join(timeout=2)
  assert not updater._worker.is_alive()
  assert updater.tick(force=True)
  updater._worker.join(timeout=2)
  assert not updater._worker.is_alive() and params.get("SpeedCameraDatabaseStatus") == "error"
  monotonic[0] = 159.999
  assert updater.tick() is False
  monotonic[0] = 160.0
  assert updater.tick()
  updater._worker.join(timeout=2)
  assert not updater._worker.is_alive() and len(calls) == 3


@pytest.mark.parametrize("new_period", [6, 168])
def test_changing_update_period_reschedules_from_last_success(tmp_path, new_period):
  cache = str(tmp_path / "cache.json")
  now, monotonic = time.time(), [100.0]
  params = FakeParams({"SpeedCameraDatabasePath": cache, "SpeedCameraDatabaseUpdatePeriodHours": "24",
                       "SpeedCameraDatabaseCacheMaxAgeHours": "720"})
  calls = []

  def download(url, path, version):
    calls.append(url)
    database = CameraDatabase(version, now - 10 * 24 * 3600, (SpeedCamera(55, 37, 60),))
    save_database(path, database)
    return database

  updater = CameraDatabaseUpdater(params=params, download_fn=download, monotonic=lambda: monotonic[0])
  assert updater.tick()
  updater._worker.join(timeout=2)
  assert not updater._worker.is_alive()
  params.put("SpeedCameraDatabaseUpdatePeriodHours", str(new_period))
  monotonic[0] = 100.0 + new_period * 3600 - 0.001
  assert updater.tick() is False and len(calls) == 1
  monotonic[0] = 100.0 + new_period * 3600
  assert updater.tick()
  updater._worker.join(timeout=2)
  assert not updater._worker.is_alive() and len(calls) == 2


def test_default_updater_calls_camera_source_wrapper_with_shared_source_constant(tmp_path, monkeypatch):
  from openpilot.sunnypilot.mapd import camera_source

  path = str(tmp_path / "cache.json")
  params = FakeParams({"SpeedCameraDatabasePath": path, "SpeedCameraDatabaseUpdatePeriodHours": "24"})
  calls = []
  producer_date = time.time() - 2 * 24 * 3600

  def direct(url, path, version, *, timeout_s, max_bytes):
    calls.append((url, path, version, timeout_s, max_bytes))
    database = CameraDatabase(version, producer_date, (SpeedCamera(55, 37, 60),), "speedcamonline.ru")
    save_database(path, database)
    return database

  # Use the actual wrapper and replace only its external transport boundary.
  monkeypatch.setattr(camera_source, "download_database", direct)
  updater = CameraDatabaseUpdater(params=params)
  assert DEFAULT_SPEED_CAMERA_URL == camera_source.DEFAULT_SPEED_CAMERA_URL
  assert updater.download_fn is camera_source.download_camera_database
  assert updater.tick()
  updater._worker.join(timeout=2)
  assert not updater._worker.is_alive()
  assert calls == [(camera_source.DEFAULT_SPEED_CAMERA_URL, path, DEFAULT_SPEED_CAMERA_VERSION,
                    camera_source.DIRECT_TIMEOUT_S, 64 * 1024 * 1024)]
  assert params.get("SpeedCameraDatabaseStatus") == "ready"
  assert float(params.get("SpeedCameraDatabaseUpdatedAt")) == producer_date
  assert load_database(path).fetched_at == producer_date
  assert updater.tick() is False
