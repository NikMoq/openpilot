"""Offline acquisition tests; no real sockets, accounts or credentials.

python -B -m pytest -c tools/tests/camera_replay_pytest.ini -p no:cacheprovider tools/tests/test_waze_backend.py -q
"""
import os
import socket
import struct
import sys
import zlib
from pathlib import Path
from types import ModuleType
from urllib.parse import parse_qs, urlparse
from email.message import Message
from io import BytesIO
from urllib.response import addinfourl

import pytest

ROOT = Path(__file__).resolve().parents[2]
for name, relative in (("openpilot", "."), ("openpilot.sunnypilot", "sunnypilot"), ("openpilot.sunnypilot.mapd", "sunnypilot/mapd")):
  if name not in sys.modules:
    namespace = ModuleType(name)
    namespace.__path__ = [str(ROOT / relative)]
    sys.modules[name] = namespace

from openpilot.sunnypilot.mapd.waze import WazeBackend, codec
from openpilot.sunnypilot.mapd.waze.rt import AccountRejectedError, GuestStore, RetryableError, RTClient
from openpilot.sunnypilot.mapd.waze.tiles import CACHE_TTL_MS, FixedTileClient, covering_tiles, parse_tile, split_batch
from openpilot.sunnypilot.mapd.waze.transport import WazeTransport, request

FIXTURES = ROOT / "sunnypilot/mapd/waze/fixtures"
NOW = 1793550000.0


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
  def forbidden(*args, **kwargs):
    pytest.fail("Waze acquisition unit tests must not open network sockets")
  monkeypatch.setattr(socket, "create_connection", forbidden)
  monkeypatch.setattr(socket, "getaddrinfo", forbidden)


def tile_payload(identifier, camera=False, category=None):
  reference = 348170252 if camera else 348134252
  body = (FIXTURES / f"{reference}.bin").read_bytes()[20:]
  raw = bytearray(zlib.decompress(body[20:]))
  count, bits = struct.unpack_from("<II", raw)
  base, mask = 8 + count * 4, (1 << bits) - 1
  header26 = base + ((struct.unpack_from("<I", raw, 8 + 25 * 4)[0] + mask) & ~mask)
  struct.pack_into("<I", raw, header26, identifier)
  if category is not None:
    record = base + ((struct.unpack_from("<I", raw, 8 + 24 * 4)[0] + mask) & ~mask)
    raw[record + 10] = category
  compressed = zlib.compress(raw)
  return body[:12] + struct.pack("<II", len(compressed), len(raw)) + compressed


def tile_batch(ids, camera=False, category=None):
  out = b"...." + struct.pack(">I", len(ids))
  for identifier in ids:
    payload = tile_payload(identifier, camera and identifier == 348170252, category if identifier == 348170252 else None)
    out += struct.pack(">III", identifier, 0, len(payload)) + payload
  return out


class TileServer:
  def __init__(self, camera=True, category=None):
    self.calls, self.fail, self.camera, self.category = [], False, camera, category

  def __call__(self, url, **kwargs):
    self.calls.append((url, kwargs))
    if self.fail:
      raise OSError("synthetic network failure")
    params = parse_qs(urlparse(url).query)
    ids = [int(params[f"t{index}"][0]) for index in range(int(params["num"][0]))]
    return tile_batch(ids, self.camera, self.category)


def batch(*elements):
  return b"".join(codec.binary(1001, element) for element in elements)


def mobile(identifier="synthetic-mobile", *, heading=286, report_time=1793545703, archived=False):
  position = codec.integer(101, 11591403) + codec.integer(102, 48142539)
  info = codec.integer(1, 2) + codec.integer(2, 203) + codec.binary(3, position)
  if heading is not None:
    info += codec.integer(6, heading)
  reporting = codec.binary(8, codec.binary(2, "Synthetic street") + codec.binary(3, "Munich"))
  if report_time is not None:
    reporting += codec.integer(4, report_time)
  if archived:
    reporting += codec.integer(13, 1)
  realtime = codec.integer(1, 123) + codec.binary(2, info) + codec.binary(3, reporting) + codec.binary(6, identifier)
  return codec.binary(2708, codec.binary(1, realtime))


class RTServer:
  def __init__(self):
    self.paths, self.headers, self.reject, self.fail_registration = [], [], False, False
    self.commands = []

  def __call__(self, url, body=None, headers=None, **kwargs):
    path = urlparse(url).path
    self.paths.append(path)
    self.headers.append(headers)
    assert kwargs["timeout_s"] > 0
    if path.endswith("/static"):
      if self.fail_registration:
        return batch(codec.binary(2003, codec.integer(10101, 504)))
      return batch(codec.binary(2220, codec.binary(1, "synthetic-user") + codec.binary(2, "synthetic-password")))
    if path.endswith("/login"):
      if self.reject:
        return batch(codec.binary(2224, codec.integer(2, 1)))
      success = codec.integer(1, 111) + codec.binary(3, "synthetic-session-secret")
      return batch(codec.binary(2745, codec.binary(1, success)))
    assert path.endswith("/command")
    assert headers.get("uid")
    if self.commands:
      return self.commands.pop(0)
    return batch(mobile())


def test_captured_tile_has_real_fields_but_no_invented_speed_unit():
  captured = (FIXTURES / "348170252.bin").read_bytes()
  body = split_batch(captured, [348170252])[348170252]
  camera = parse_tile(body, 348170252)[0]
  assert camera == {"latitude": 52.525152, "longitude": 13.420412, "heading_raw": 303, "category_raw": 2,
                    "speed_raw": 50, "raw_id": 28710, "filler_raw": 0, "tile_id": 348170252}


def test_grid_covers_circle_border_date_line_and_refuses_truncation():
  ids = covering_tiles(52.525152, 13.420412, 500)
  assert 348170252 in ids and ids == sorted(set(ids))
  wrap = covering_tiles(0, 179.999, 1000)
  assert any(identifier // 18000 == 0 for identifier in wrap)
  assert any(identifier // 18000 == 35999 for identifier in wrap)
  with pytest.raises(ValueError, match="reduce radius"):
    covering_tiles(52.5, 13.4, 100000)
  with pytest.raises(ValueError):
    covering_tiles(float("nan"), 13.4, 1000)


@pytest.mark.parametrize("fault", ["missing", "duplicate", "wrong", "truncated", "trailing"])
def test_batch_never_accepts_partial_or_wrong_tiles(fault):
  ids = [348134252, 348170252]
  data = tile_batch(ids)
  if fault == "missing":
    data = tile_batch(ids[:1])
  elif fault == "duplicate":
    data = tile_batch([ids[0], ids[0]])
  elif fault == "wrong":
    data = tile_batch([ids[0], ids[1] + 1])
  elif fault == "truncated":
    data = data[:-1]
  else:
    data += b"x"
  with pytest.raises(ValueError):
    split_batch(data, ids)


def test_tile_rejects_wrong_identity_and_expansion_bomb():
  body = tile_payload(348170252, camera=True)
  with pytest.raises(ValueError, match="mismatch"):
    parse_tile(body, 348170253)
  modified = bytearray(body)
  struct.pack_into("<I", modified, 16, 100 * 1024 * 1024)
  with pytest.raises(ValueError, match="header"):
    parse_tile(bytes(modified), 348170252)


def test_fixed_fetch_reuses_cache_and_preserves_source_freshness(tmp_path):
  server, clock = TileServer(), [NOW]
  client = FixedTileClient(tmp_path, server, lambda: clock[0])
  first = client.fetch(52.525152, 13.420412, 500)
  assert first["loaded_tiles"] == first["requested_tiles"] == 2
  event = first["events"][0]
  assert event["source"] == "waze" and event["source_kind"] == "fixed"
  assert event["speed_raw"] == 50 and event["speed_unit"] is None and event["speed_limit_kph"] is None
  assert len(server.calls) == 1
  clock[0] += 60
  cached = client.fetch(52.525152, 13.420412, 500)
  assert len(server.calls) == 1
  assert cached["events"][0]["observed_at_ms"] == event["observed_at_ms"]
  assert cached["events"][0]["expires_at_ms"] == event["observed_at_ms"] + CACHE_TTL_MS
  path = next(tmp_path.glob("*.wdf"))
  path.write_bytes(b"corrupt")
  client.fetch(52.525152, 13.420412, 500)
  assert len(server.calls) == 2
  clock[0] += CACHE_TTL_MS / 1000 + 1
  server.fail = True
  with pytest.raises(OSError, match="synthetic"):
    client.fetch(52.525152, 13.420412, 500)


def test_unknown_fixed_camera_category_is_preserved_and_marks_incomplete(tmp_path):
  result = FixedTileClient(tmp_path, TileServer(category=99), lambda: NOW).fetch(52.525152, 13.420412, 500)
  assert result["unknown_count"] == 1 and not result["complete"]
  assert result["events"][0]["raw_subtype"] == "UNKNOWN_CATEGORY_99"


def test_rt_codec_roundtrip_missing_values_archive_and_removal():
  data = batch(mobile(heading=None, report_time=None), mobile("archived", archived=True), codec.binary(2001, "RmAlert, gone "))
  events, removed = codec.parse_events(codec.elements(data), 123000, 60000)
  assert len(events) == 1 and events[0]["raw_subtype"] == "POLICE_WITH_MOBILE_CAMERA"
  assert events[0]["heading_raw"] is None and events[0]["published_at_ms"] is None
  assert events[0]["expires_at_ms"] == 183000
  assert removed == ["waze:rt:archived", "waze:rt:gone"]
  assert events[0]["speed_limit_kph"] is None


@pytest.mark.parametrize("data", [b"\x00", b"\x0b", b"\x0a\x05xx", b"\x80" * 11])
def test_protobuf_malformed_messages_fail(data):
  with pytest.raises(codec.ProtocolError):
    codec.message(data)


def test_rt_delta_cache_removal_throttle_and_session_refresh(tmp_path):
  server, clock = RTServer(), [NOW]
  backend = WazeBackend(tmp_path / "state", server, lambda: clock[0], lambda: clock[0])
  first = backend.fetch_mobile(48.142539, 11.591403, 3000)
  assert len(first.events) == 1 and first.complete and not first.inventory_complete
  assert len(server.paths) == 4  # one registration, login, handshake, viewport
  assert backend.fetch_mobile(48.142539, 11.591403, 3000).events == first.events
  assert len(server.paths) == 4
  clock[0] += 31
  server.commands = [batch(codec.binary(2001, "RmAlert,synthetic-mobile"))]
  second = backend.fetch_mobile(48.142539, 11.591403, 3000)
  assert not second.events and second.removed_ids == ["waze:rt:synthetic-mobile"]
  clock[0] += 601
  backend.fetch_mobile(48.142539, 11.591403, 3000)
  assert server.paths.count("/rtserver/distrib/static") == 1
  assert server.paths.count("/rtserver/distrib/login") == 2


def test_guest_files_are_private_reused_and_rejection_never_registers_replacement(tmp_path):
  store = GuestStore(tmp_path)
  state = {"installation": "synthetic-installation", "username": "synthetic-user", "password": "synthetic-password"}
  store.save("row", state, round(NOW * 1000))
  path = tmp_path / "guest_row.json"
  original = path.read_bytes()
  server = RTServer()
  server.reject = True
  client = RTClient(store, "row", server, lambda: NOW)
  with pytest.raises(AccountRejectedError, match="retained"):
    client.fetch(48.14, 11.59, 1000)
  assert server.paths == ["/rtserver/distrib/login"]
  assert path.read_bytes() == original
  if os.name == "posix":
    assert tmp_path.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600


def test_registration_failure_is_not_repeated_on_restart(tmp_path):
  server = RTServer()
  server.fail_registration = True
  for expected in (RetryableError, AccountRejectedError):
    with pytest.raises(expected):
      RTClient(GuestStore(tmp_path), "row", server, lambda: NOW).fetch(48.14, 11.59, 1000)
  assert server.paths == ["/rtserver/distrib/static"]


def test_empty_rt_delta_does_not_refresh_observation_or_expiry(tmp_path):
  server, clock = RTServer(), [NOW]
  backend = WazeBackend(tmp_path, server, lambda: clock[0], lambda: clock[0])
  initial = backend.fetch_mobile(48.142539, 11.591403, 3000).events[0]
  server.commands = [batch(codec.binary(2001, "KeepAlive"))]
  clock[0] += 31
  repeated = backend.fetch_mobile(48.142539, 11.591403, 3000).events[0]
  assert repeated["observed_at_ms"] == initial["observed_at_ms"]
  assert repeated["expires_at_ms"] == initial["expires_at_ms"]


def test_transport_retains_affinity_cookie_only_in_its_session(monkeypatch):
  from openpilot.sunnypilot.mapd import camera_database
  observed = []

  def fake_https_open(handler, query):
    observed.append(query.get_header("Cookie"))
    headers = Message()
    headers["Content-Type"] = "application/octet-stream"
    headers["Content-Length"] = "2"
    headers["Set-Cookie"] = "waze_affinity=synthetic; Path=/; Secure; HttpOnly"
    response = addinfourl(BytesIO(b"ok"), headers, query.full_url, 200)
    response.msg = "OK"
    return response

  monkeypatch.setattr(camera_database._DeadlineHTTPSHandler, "https_open", fake_https_open)
  first, other = WazeTransport(), WazeTransport()
  endpoint = "https://rt-xlb-row.waze.com/rtserver/distrib/command"
  assert first(endpoint, body=b"synthetic") == b"ok"
  assert first(endpoint, body=b"synthetic") == b"ok"
  assert other(endpoint, body=b"synthetic") == b"ok"
  assert observed == [None, "waze_affinity=synthetic", None]


def test_credentials_cannot_be_stored_in_checkout():
  with pytest.raises(ValueError, match="outside"):
    GuestStore(ROOT / "sunnypilot/mapd/waze/private-state-forbidden")


@pytest.mark.parametrize("url", ["http://rt-xlb-row.waze.com/", "https://example.org/", "https://user:password@rt-xlb-row.waze.com/"])
def test_transport_rejects_unsafe_targets_without_network(url):
  with pytest.raises(ValueError, match="approved HTTPS"):
    request(url)
