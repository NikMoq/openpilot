"""Public-source Yandex acquisition tests; no network or vehicle controls."""
import os
import socket
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
for name, relative in (("openpilot", "."), ("openpilot.sunnypilot", "sunnypilot"), ("openpilot.sunnypilot.mapd", "sunnypilot/mapd")):
  if name not in sys.modules:
    namespace = ModuleType(name)
    namespace.__path__ = [str(ROOT / relative)]
    sys.modules[name] = namespace

from openpilot.sunnypilot.mapd.yandex import backend
from openpilot.sunnypilot.mapd.yandex.transport import request


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
  def forbidden(*args, **kwargs):
    pytest.fail("Yandex tests must not open sockets")
  monkeypatch.setattr(socket, "create_connection", forbidden)
  monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def make_client(tmp_path, monkeypatch, **options):
  monkeypatch.setattr(backend, "covering_tiles", lambda *args: ((4952, 2568),))
  return backend.YandexPublicBackend(tmp_path / "cache", clock=lambda: 1000.0, **options)


def record():
  return {"source_id": "public-event", "latitude": 55.748, "longitude": 37.638,
          "raw_type": "ACCIDENT", "raw_tags": ["accident"]}


def test_real_projection_covers_observed_nonempty_moscow_tile():
  assert (4952, 2568) in backend.covering_tiles(55.748, 37.638, 100)


@pytest.mark.parametrize("args", [(True, 1, 100), (float("nan"), 1, 100), (81, 1, 100),
                                 (45, 181, 100), (45, 38, 0), (45, 38, 5001), (0, 179.999, 5000)])
def test_invalid_queries_rejected(args):
  with pytest.raises(ValueError):
    backend.covering_tiles(*args)


def test_new_and_cached_observations_preserve_original_age(tmp_path, monkeypatch):
  calls = []
  def transport(url, **kwargs):
    calls.append(url)
    return b"observed-public-tile"
  client = make_client(tmp_path, monkeypatch, transport=transport, decoder=lambda *a: [record()])
  first = client.fetch_events(55, 37, 100)
  client.clock = lambda: 1030.0
  cached = client.fetch_events(55, 37, 100)
  assert len(calls) == 1
  assert first.events[0]["observed_at_s"] == cached.events[0]["observed_at_s"] == 1000
  assert cached.events[0]["expires_at_s"] == 1060
  assert cached.camera_inventory_available is False
  assert cached.speed_limits_available is False
  assert cached.events[0]["limit_kind"] == "unavailable"


def test_unchanged_download_does_not_rewrite_flash(tmp_path, monkeypatch):
  downloads = []
  def transport(*a, **kw):
    downloads.append(1)
    return b"same"
  client = make_client(tmp_path, monkeypatch, transport=transport, decoder=lambda *a: [record()])
  client.fetch_events(55, 37, 100)
  path = client.cache_dir / "13_4952_2568.pb"
  stamp = path.stat().st_mtime_ns
  client.clock = lambda: 1100.0
  def no_sync(fd):
    pytest.fail("Unchanged tile must not be written/fsynced")
  monkeypatch.setattr(os, "fsync", no_sync)
  result = client.fetch_events(55, 37, 100)
  assert path.stat().st_mtime_ns == stamp
  assert result.events[0]["observed_at_s"] == 1100
  client.clock = lambda: 1101.0
  cached = client.fetch_events(55, 37, 100)
  assert cached.events[0]["observed_at_s"] == 1100
  assert len(downloads) == 2


def test_memory_verification_does_not_make_modified_disk_data_fresh(tmp_path, monkeypatch):
  calls = []
  def transport(*a, **kw):
    calls.append(1)
    return b"same"
  client = make_client(tmp_path, monkeypatch, transport=transport, decoder=lambda *a: [record()])
  client.fetch_events(55, 37, 100)
  path = client.cache_dir / "13_4952_2568.pb"
  path.write_bytes(b"changed")
  os.utime(path, (900, 900))
  client.clock = lambda: 1010.0
  result = client.fetch_events(55, 37, 100)
  assert len(calls) == 2
  assert result.events[0]["observed_at_s"] == 1010


def test_expired_cache_not_used_after_network_failure(tmp_path, monkeypatch):
  client = make_client(tmp_path, monkeypatch, transport=lambda *a, **kw: b"old", decoder=lambda *a: [record()])
  client.fetch_events(55, 37, 100)
  client.clock = lambda: 1100.0
  def unavailable(*a, **kw):
    raise OSError("Synthetic outage")
  client.transport = unavailable
  with pytest.raises(OSError):
    client.fetch_events(55, 37, 100)


def test_invalid_response_is_not_cached(tmp_path, monkeypatch):
  def malformed(*a):
    raise ValueError("Malformed tile")
  client = make_client(tmp_path, monkeypatch, transport=lambda *a, **kw: b"bad", decoder=malformed)
  with pytest.raises(ValueError):
    client.fetch_events(55, 37, 100)
  assert not list(client.cache_dir.glob("*.pb"))


def test_cache_prunes_by_bytes_and_count_without_touching_other_files(tmp_path, monkeypatch):
  client = make_client(tmp_path, monkeypatch, transport=lambda *a, **kw: b"unused", decoder=lambda *a: [])
  monkeypatch.setattr(backend, "MAX_CACHE_FILES", 2)
  monkeypatch.setattr(backend, "MAX_CACHE_BYTES", 6)
  for i in range(4):
    path = client.cache_dir / f"13_{i}_1.pb"
    path.write_bytes(b"1234")
    os.utime(path, (i + 1, i + 1))
  other = client.cache_dir / "guest.json"
  other.write_text("untouched")
  client._prune()
  assert [p.name for p in client.cache_dir.glob("*.pb")] == ["13_3_1.pb"]
  assert other.read_text() == "untouched"


def test_overlapping_records_do_not_refresh_oldest_observation(tmp_path, monkeypatch):
  client = make_client(tmp_path, monkeypatch, transport=lambda *a, **kw: b"tile", decoder=lambda *a: [record()])
  monkeypatch.setattr(backend, "covering_tiles", lambda *args: ((4952, 2568), (4953, 2568)))
  old = client.cache_dir / "13_4952_2568.pb"
  old.write_bytes(b"old")
  os.utime(old, (980, 980))
  result = client.fetch_events(55, 37, 100)
  assert len(result.events) == 1
  assert result.events[0]["observed_at_s"] == 980


def test_memory_cache_remains_bounded_when_disk_eviction_fails(tmp_path, monkeypatch):
  client = make_client(tmp_path, monkeypatch, transport=lambda *a, **kw: b"unused", decoder=lambda *a: [])
  monkeypatch.setattr(backend, "MAX_CACHE_FILES", 2)
  for i in range(4):
    name = f"13_{i}_1.pb"
    (client.cache_dir / name).write_bytes(b"data")
    client._verified[name] = (b"digest", i)
  def cannot_delete(*a, **kw):
    raise OSError("Synthetic deletion failure")
  monkeypatch.setattr(Path, "unlink", cannot_delete)
  client._prune()
  assert len(client._verified) == 2
  assert set(client._verified) == {"13_2_1.pb", "13_3_1.pb"}


@pytest.mark.parametrize("url", ["http://core-road-events-renderer.maps.yandex.net/2.x/tiles",
                                  "https://example.com/2.x/tiles",
                                  "https://core-road-events-renderer.maps.yandex.net/other",
                                  "https://user:secret@core-road-events-renderer.maps.yandex.net/2.x/tiles",
                                  "https://core-road-events-renderer.maps.yandex.net:444/2.x/tiles"])
def test_transport_rejects_other_endpoints_before_network(url):
  with pytest.raises(ValueError):
    request(url)
