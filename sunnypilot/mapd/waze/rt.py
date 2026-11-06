"""Guest-session Waze RT fetch client. No reporting or automatic account replacement."""
from __future__ import annotations

import json
import math
import os
import time
import uuid
from pathlib import Path

from . import codec
from .clock import utc_now_s
from .tiles import _atomic_write, validate_query
from .transport import WazeTransport, request

MOBILE_TTL_MS = 30 * 60 * 1000
# Fresh sessions resupply the delta stream before the outer policy's 3-minute
# mobile freshness cap. Empty deltas never refresh old events' observation times.
SESSION_REFRESH_S = 2 * 60
MAX_CACHE_EVENTS = 5000


class AccountRejectedError(RuntimeError):
  """Existing guest was rejected; operator intervention, never replacement registration."""


class RetryableError(RuntimeError):
  pass


class ThrottledError(RuntimeError):
  pass


def _diagnostic(error, category, stage=None, status=None):
  error.category, error.stage, error.status = category, stage, status
  return error


def _check_errors(batch: list[dict]) -> None:
  for element in batch:
    if 2003 in element:
      error = codec.nested(element, 2003)
      code = codec.scalar(error, 10101)
      # Descriptions are inspected for classification only and never exported.
      description = codec.text(error, 10102).lower()
      if code >= 500:
        raise _diagnostic(RetryableError(f"Waze server operation error {code}"), "protobuf_unavailable", status=code)
      if code >= 400:
        raise _diagnostic(AccountRejectedError(f"Waze guest/session rejected ({code}); existing credentials retained"),
                          "protobuf_rejected", status=code)
      if any(value in description for value in ("relogin", "unknown userid", "secretkey missing", "secret key missing")):
        raise _diagnostic(RetryableError("Waze session needs login"), "relogin")
    errors = []
    if 2224 in element:
      errors.append(codec.nested(element, 2224))
    if 2745 in element:
      response = codec.nested(element, 2745)
      if 2 in response:
        errors.append(codec.nested(response, 2))
    for error in errors:
      code = codec.scalar(error, 2)
      if code in (1, 3, 7):
        raise _diagnostic(AccountRejectedError(f"Waze guest rejected ({code}); existing credentials retained"),
                          "protobuf_rejected", status=code)
      raise _diagnostic(RetryableError(f"Waze login unavailable ({code})"), "protobuf_unavailable", status=code)


class GuestStore:
  def __init__(self, directory: Path):
    self.directory = Path(directory).absolute()
    repository = Path(__file__).resolve().parents[3]
    resolved = self.directory.resolve()
    if resolved == repository or repository in resolved.parents:
      raise ValueError("Waze guest credentials must be stored outside the checkout")
    if self.directory.is_symlink():
      raise ValueError("Waze credential directory must not be a symbolic link")
    self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
      self.directory.chmod(0o700)

  def _path(self, region: str) -> Path:
    if region not in ("row", "na", "il"):
      raise ValueError("Invalid Waze region")
    return self.directory / f"guest_{region}.json"

  def load(self, region: str) -> dict | None:
    path = self._path(region)
    if path.is_symlink():
      raise ValueError("Invalid local Waze credential file")
    if not path.exists():
      return None
    if not path.is_file() or path.stat().st_size > 16384:
      raise ValueError("Invalid local Waze credential file")
    if os.name == "posix":
      path.chmod(0o600)
    try:
      result = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
      raise ValueError("Invalid local Waze credential state; file retained") from None
    if not isinstance(result, dict) or any(not isinstance(result.get(key), str) or not result[key] or len(result[key]) > 4096
                                          for key in ("installation", "username", "password")):
      raise ValueError("Invalid local Waze credential state; file retained")
    return result

  def begin_registration(self, region: str) -> str:
    path = self.directory / f"registration_{region}.pending"
    if path.exists():
      raise _diagnostic(AccountRejectedError("Previous Waze guest registration was not completed; automatic registration stopped"),
                        "local_pending", "registration")
    installation = str(uuid.uuid4())
    # Persist before the network call, preventing account churn on process crashes.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
      stream.write(installation)
      stream.flush()
      os.fsync(stream.fileno())
    return installation

  def save(self, region: str, state: dict, now_ms: int) -> None:
    _atomic_write(self._path(region), json.dumps(state, separators=(",", ":")).encode(), now_ms)
    (self.directory / f"registration_{region}.pending").unlink(missing_ok=True)


class RTClient:
  def __init__(self, store: GuestStore, region: str, transport=request, clock=utc_now_s, monotonic=time.monotonic,
               ttl_ms: int = MOBILE_TTL_MS, min_interval_s: float = 30):
    if region not in ("row", "na", "il") or ttl_ms <= 0 or min_interval_s < 0:
      raise ValueError("Invalid Waze RT client settings")
    self.store, self.region = store, region
    self.transport = WazeTransport() if transport is request else transport
    self.clock, self.monotonic, self.ttl_ms, self.min_interval_s = clock, monotonic, ttl_ms, min_interval_s
    self._uid, self._sequence = None, 1
    self._logged_at, self._last_use, self._last_attempt = None, None, None
    self._cache: dict[str, dict] = {}
    self._last_key, self._last_result = None, None

  def _request(self, path: str, payload: str, deadline: float, login: bool = False, stage: str | None = None) -> list[dict]:
    stage = stage or ("login" if login else "registration" if path.endswith("/static") else "viewport")
    remaining = deadline - self.monotonic()
    if remaining <= 0:
      raise _diagnostic(TimeoutError("Waze RT acquisition deadline exceeded"), "timeout", stage)
    region = "am" if self.region == "na" else self.region
    headers = {"User-Agent": ("waze/" if login else "") + "5.17.1.0", "x-waze-network-version": "3",
               "sequence-number": str(self._sequence), "Content-Type": "binary/octet-stream", "cache-control": "no-cache"}
    self._sequence += 1
    if login:
      headers["x-waze-wait-timeout"] = "8500"
    if self._uid and path.endswith("/command"):
      headers.update({"uid": self._uid, "x-waze-wait-timeout": "10500"})
    try:
      raw = self.transport(f"https://rt-xlb-{region}.waze.com{path}", body=payload.encode(), headers=headers,
                           timeout_s=remaining, max_bytes=codec.MAX_MESSAGE_BYTES)
      batch = codec.elements(raw)
      _check_errors(batch)
    except Exception as error:
      error.stage = stage
      if not hasattr(error, "category"):
        error.category = "timeout" if isinstance(error, TimeoutError) else "network" if isinstance(error, OSError) else "protocol"
      raise
    self._last_use = self.monotonic()
    return batch

  def _ensure_login(self, lat: float, lon: float, deadline: float) -> bool:
    now = self.monotonic()
    if self._uid and self._last_use is not None and self._logged_at is not None and now - self._last_use < 100 and now - self._logged_at < SESSION_REFRESH_S:
      return False
    self._uid = None
    state = self.store.load(self.region)
    now_ms = round(self.clock() * 1000)
    if state is None:
      installation = self.store.begin_registration(self.region)
      batch = self._request("/rtserver/distrib/static", codec.client_info(lat, lon, installation, now_ms) + "\n" + codec.line(2219, b""), deadline)
      registered = next((codec.nested(element, 2220) for element in batch if 2220 in element), None)
      if registered is None:
        raise _diagnostic(RetryableError("Waze guest registration did not succeed; automatic repeat stopped"), "protocol", "registration")
      username, password = codec.text(registered, 1), codec.text(registered, 2)
      if not username or not password:
        raise _diagnostic(RetryableError("Waze guest registration returned incomplete credentials; automatic repeat stopped"), "protocol", "registration")
      state = {"installation": installation, "username": username, "password": password}
      self.store.save(self.region, state, now_ms)
    payload = (codec.client_info(lat, lon, state["installation"], now_ms) + "\n"
               + codec.login_line(state["username"], state["password"]) + "\n" + codec.line(2108, b""))
    batch = self._request("/rtserver/distrib/login", payload, deadline, login=True)
    for element in batch:
      response = codec.nested(element, 2745)
      if 1 not in response:
        continue
      success = codec.nested(response, 1)
      identifier, secret = codec.scalar(success, 1), codec.text(success, 3)
      if not identifier or not secret:
        raise _diagnostic(RetryableError("Waze login returned an invalid session"), "protocol", "login")
      self._uid = codec.uid_header(identifier, secret)
      self._sequence, self._logged_at = 2, self.monotonic()
      self._cache.clear()
      return True
    raise _diagnostic(RetryableError("Waze login returned no accepted session; credentials retained"), "protocol", "login")

  def _merge(self, batch: list[dict]) -> list[str]:
    now_ms = round(self.clock() * 1000)
    additions, removals = codec.parse_events(batch, now_ms, self.ttl_ms)
    for event in additions:
      self._cache[event["source_id"]] = event
    for identifier in removals:
      self._cache.pop(identifier, None)
    self._cache = {key: event for key, event in self._cache.items() if event["expires_at_ms"] > now_ms}
    while len(self._cache) > MAX_CACHE_EVENTS:
      del self._cache[next(iter(self._cache))]
    return removals

  def fetch(self, lat: float, lon: float, radius_m: float, timeout_s: float = 120) -> dict:
    validate_query(lat, lon, radius_m)
    if radius_m > 100_000:
      raise ValueError("Waze RT query radius exceeds 100 km; reduce radius")
    if not math.isfinite(timeout_s) or timeout_s <= 0:
      raise ValueError("Waze RT timeout must be positive")
    key, now = (lat, lon, radius_m), self.monotonic()
    if self._last_attempt is not None and now - self._last_attempt < self.min_interval_s:
      if key == self._last_key and self._last_result is not None:
        result = dict(self._last_result)
        now_ms = round(self.clock() * 1000)
        result["events"] = [event for event in result["events"] if event["expires_at_ms"] > now_ms]
        return result
      raise ThrottledError("Waze RT request throttled; retry after update interval")
    self._last_attempt = now
    deadline = now + timeout_s
    dlat = math.degrees(radius_m / 6371000)
    dlon = math.degrees(math.asin(min(1, math.sin(radius_m / 6371000) / max(1e-12, math.cos(math.radians(lat))))))
    west, south, east, north = lon - dlon, lat - dlat, lon + dlon, lat + dlat
    if west < -180 or east > 180 or south < -90 or north > 90:
      raise ValueError("Waze RT query crosses unsupported world boundary")
    removed = []
    try:
      fresh = self._ensure_login(lat, lon, deadline)
      if fresh:
        handshake = f"SeeMe,1,2,T,T,T,1,-1,1,7\nSetMood,1\nLocation,{lon},{lat}\n" + codec.map_command(lon - .018, lat - .015, lon + .018, lat + .015)
        removed += self._merge(self._request("/rtserver/distrib/command", handshake, deadline, stage="handshake"))
      removed += self._merge(self._request("/rtserver/distrib/command", codec.map_command(west, south, east, north), deadline))
    except Exception:
      self._uid = None  # retry existing credentials on next scheduled call, never replace guest
      raise
    result = {"events": list(self._cache.values()), "removed_ids": list(dict.fromkeys(removed)), "observed_at_ms": round(self.clock() * 1000),
              "complete": True, "region": self.region, "inventory_complete": False}
    self._last_key, self._last_result = key, result
    return result
