"""Versioned local cache and optional downloader for speed-camera snapshots."""

from __future__ import annotations

import json
import hashlib
import http.client
import io
import math
import os
import re
import socket
import tempfile
import time
from dataclasses import dataclass
from functools import partial
from urllib.error import HTTPError, URLError
from urllib.request import HTTPHandler, HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from urllib.parse import urlparse

from openpilot.sunnypilot.mapd.speed_cameras import SpeedCamera, parse_camera_snapshot


_FETCHED_AT_RE = re.compile(rb'"fetched_at"\s*:\s*([-+0-9.eE]+)')
_CACHE_HEADER_BYTES = 512


class _HTTPDeadline:
  """One request/redirect budget; no threads or process-wide socket changes.

  OS getaddrinfo and default TLS trust-store loading are not cancellable in
  stdlib. Once they return,
  each connection attempt, TLS handshake, header/body socket read and request
  write uses the remaining budget. Parsing and cache serialization are outside
  this network deadline. wire_bytes includes HTTP framing and headers.
  """

  def __init__(self, timeout: float, deadline: float | None = None):
    self.deadline = time.monotonic() + timeout if deadline is None else deadline
    self.started = self.deadline - timeout
    self.wire_bytes = 0

  def timeout(self) -> TimeoutError:
    return TimeoutError(f"speed-camera HTTP deadline/read timeout after {time.monotonic() - self.started:.3f}s; " +
                        f"wire_bytes={self.wire_bytes}")

  def remaining(self) -> float:
    remaining = self.deadline - time.monotonic()
    if remaining <= 0:
      raise self.timeout()
    return remaining

  def connect(self, address, timeout=None, source_address=None):
    # socket.create_connection gives every resolved address a fresh timeout.
    # Keep one absolute deadline even when DNS returns several addresses.
    host, port = address
    addresses = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    last_error = None
    for family, kind, protocol, _, target in addresses:
      remaining = self.remaining()
      connection = socket.socket(family, kind, protocol)
      try:
        connection.settimeout(remaining)
        if source_address:
          connection.bind(source_address)
        connection.connect(target)
        connection.settimeout(self.remaining())
        return connection
      except OSError as error:
        connection.close()
        last_error = error
    if last_error is not None:
      raise last_error
    raise OSError("speed-camera HTTP host resolved to no addresses")


class _DeadlineReader(io.RawIOBase):
  def __init__(self, connection, budget: _HTTPDeadline):
    super().__init__()
    self._sock = connection
    self._raw = connection.makefile("rb", buffering=0)
    self._budget = budget

  def readable(self):
    return True

  def readinto(self, buffer):
    self._sock.settimeout(self._budget.remaining())
    try:
      count = self._raw.readinto(buffer)
    except TimeoutError as error:
      raise self._budget.timeout() from error
    self._budget.wire_bytes += count or 0
    return count

  def close(self):
    try:
      self._raw.close()
    finally:
      super().close()


class _ResponseSocket:
  """Adapt only HTTPResponse.makefile, preserving real sockets for TLS."""

  def __init__(self, connection, budget):
    self.connection, self.budget = connection, budget

  def makefile(self, mode):
    if mode != "rb":
      raise ValueError("HTTP deadline reader requires binary input")
    return io.BufferedReader(_DeadlineReader(self.connection, self.budget))


class _DeadlineResponse(http.client.HTTPResponse):
  def __init__(self, connection, *args, budget, **kwargs):
    super().__init__(_ResponseSocket(connection, budget), *args, **kwargs)


class _DeadlineConnection:
  def __init__(self, *args, budget, **kwargs):
    self._budget = budget
    kwargs["timeout"] = budget.remaining()
    super().__init__(*args, **kwargs)
    self._create_connection = budget.connect
    self.response_class = partial(_DeadlineResponse, budget=budget)

  def send(self, data):
    remaining = self._budget.remaining()
    if self.sock is not None:
      self.sock.settimeout(remaining)
    return super().send(data)


class _DeadlineHTTPConnection(_DeadlineConnection, http.client.HTTPConnection):
  pass


class _DeadlineHTTPSConnection(_DeadlineConnection, http.client.HTTPSConnection):
  def connect(self):
    # Same stages as stdlib HTTPSConnection.connect; renew the remaining
    # timeout between TCP/proxy CONNECT and the TLS handshake.
    http.client.HTTPConnection.connect(self)
    self.sock.settimeout(self._budget.remaining())
    self.sock = self._context.wrap_socket(self.sock, server_hostname=self._tunnel_host or self.host)
    self.sock.settimeout(self._budget.remaining())


class _DeadlineHTTPHandler(HTTPHandler):
  def __init__(self, budget):
    super().__init__()
    self.budget = budget

  def http_open(self, request):
    return self.do_open(partial(_DeadlineHTTPConnection, budget=self.budget), request)


class _DeadlineHTTPSHandler(HTTPSHandler):
  def __init__(self, budget, context=None):
    # HTTPSHandler normally loads the OS trust store at construction, even
    # for an HTTP URL. Defer that work until an actual HTTPS connection.
    HTTPHandler.__init__(self)
    self.budget = budget
    self._context = context

  def https_open(self, request):
    return self.do_open(partial(_DeadlineHTTPSConnection, budget=self.budget), request,
                        context=self._context)


class _HTTPOnlyRedirect(HTTPRedirectHandler):
  def redirect_request(self, request, fp, code, message, headers, new_url):
    if urlparse(new_url).scheme not in ("http", "https"):
      fp.close()
      raise ValueError("speed-camera redirect must remain HTTP or HTTPS")
    return super().redirect_request(request, fp, code, message, headers, new_url)


def urlopen(request: Request, timeout: float):
  """Local urllib entry point, intentionally replaceable by offline test stubs."""
  budget = _HTTPDeadline(timeout, getattr(request, "_speed_camera_deadline", None))
  opener = build_opener(_DeadlineHTTPHandler(budget), _DeadlineHTTPSHandler(budget), _HTTPOnlyRedirect())
  return opener.open(request, timeout=budget.remaining())


@dataclass(frozen=True)
class CameraDatabase:
  version: str
  fetched_at: float
  cameras: tuple[SpeedCamera, ...]
  provider: str = ""
  sha256: str = ""


def cache_age_seconds(path: str, now: float | None = None) -> float | None:
  """Return the cache age, or ``None`` when the cache is absent/invalid.

  A future timestamp is treated as age zero. This keeps a clock correction from
  making a valid cache immediately stale while still allowing callers to
  enforce a maximum age.
  """
  try:
    with open(path, "rb") as stream:
      header = stream.read(_CACHE_HEADER_BYTES)
    match = _FETCHED_AT_RE.search(header)
    if match is None:
      return None
    fetched_at = float(match.group(1))
    if not math.isfinite(fetched_at) or fetched_at < 0:
      return None
    return max(0.0, (time.time() if now is None else now) - fetched_at)  # noqa: TID251 -- persisted Unix epoch age
  except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
    return None


def load_database(path: str, max_age_s: float | None = None) -> CameraDatabase | None:
  try:
    with open(path, encoding="utf-8") as stream:
      payload = json.load(stream)
    fetched_at = float(payload["fetched_at"])
    if not math.isfinite(fetched_at) or fetched_at < 0:
      return None
    if max_age_s is not None and (not math.isfinite(max_age_s) or max_age_s < 0):
      return None
    if max_age_s is not None and max(0.0, time.time() - fetched_at) > max_age_s:  # noqa: TID251 -- persisted Unix epoch age
      return None
    cameras = tuple(parse_camera_snapshot(payload["data"]))
    return CameraDatabase(str(payload.get("version", "unknown")), fetched_at, cameras,
                          str(payload.get("provider", "")), str(payload.get("sha256", "")))
  except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
    return None


def save_database(path: str, database: CameraDatabase) -> None:
  directory = os.path.dirname(path) or "."
  os.makedirs(directory, exist_ok=True)
  payload = {
    "version": database.version,
    "fetched_at": database.fetched_at,
    "provider": database.provider,
    "sha256": database.sha256,
    "data": [{
      "latitude": camera.latitude,
      "longitude": camera.longitude,
      "speed_limit_kph": camera.speed_limit_kph,
      "direction_deg": camera.direction_deg,
      "direction_type": camera.direction_type,
      "camera_type": camera.camera_type,
      "end_latitude": camera.end_latitude,
      "end_longitude": camera.end_longitude,
      "second_direction_deg": camera.second_direction_deg,
      "source_id": camera.source_id,
      "source_params": camera.source_params,
    } for camera in database.cameras],
  }
  fd, temporary_path = tempfile.mkstemp(prefix=".speed_cameras_", suffix=".tmp", dir=directory, text=True)
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
      json.dump(payload, stream, separators=(",", ":"))
      stream.flush()
      os.fsync(stream.fileno())
    os.replace(temporary_path, path)
  finally:
    if os.path.exists(temporary_path):
      os.unlink(temporary_path)


def fetch_snapshot(url: str, timeout_s: float = 120, max_bytes: int = 64 * 1024 * 1024) -> bytes:
  """Fetch complete HTTP bytes with bounded transport; never parse or save.

  Shared by the direct importer, fixed-release mirror and offline producer.
  Existing HTTP status/range, size, deadline and HTML checks apply unchanged.
  The OS DNS/trust-store cancellation limits of _HTTPDeadline still apply.
  """
  if not url or not url.startswith(("https://", "http://")):
    raise ValueError("speed-camera URL must use http or https")
  if not math.isfinite(timeout_s) or timeout_s <= 0:
    raise ValueError("speed-camera timeout must be positive")
  if max_bytes <= 0:
    raise ValueError("speed-camera size limit must be positive")
  request = Request(url, headers={"User-Agent": "SunnyPilot speed-camera updater"})
  deadline = time.monotonic() + timeout_s
  request._speed_camera_deadline = deadline
  total = 0
  try:
    with urlopen(request, timeout=timeout_s) as response:
      if response.status != 200:
        raise ValueError(f"speed-camera endpoint requires complete HTTP 200; got {response.status}")
      if response.headers.get("Content-Range") is not None:
        raise ValueError("speed-camera endpoint returned Content-Range instead of a complete snapshot")
      content_type = (response.headers.get("Content-Type") or "").lower()
      if "html" in content_type:
        raise ValueError("speed-camera endpoint returned HTML")
      content_length_header = response.headers.get("Content-Length")
      expected_length: int | None = None
      if content_length_header is not None:
        try:
          expected_length = int(content_length_header)
        except (TypeError, ValueError):
          raise ValueError("speed-camera Content-Length is invalid") from None
        if expected_length < 0:
          raise ValueError("speed-camera Content-Length is invalid")
        if expected_length > max_bytes:
          raise ValueError("speed-camera snapshot exceeds size limit")
      body_parts: list[bytes] = []
      while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
          raise TimeoutError("speed-camera download wallclock timeout")
        raw_socket = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
        if raw_socket is not None:
          raw_socket.settimeout(remaining)
        read = getattr(response, "read", None)
        read1 = getattr(response, "read1", None)
        # urllib's real HTTPResponse benefits from read1(); lightweight test
        # and compatibility streams may override read() but inherit BytesIO's
        # read1(), so do not bypass their interruption/short-read semantics.
        reader = (read1 if read is None or isinstance(response, http.client.HTTPResponse) else read)
        if reader is None:
          raise OSError("speed-camera response has no read method")
        chunk = reader(min(64 * 1024, max_bytes + 1 - total))
        if not chunk:
          break
        body_parts.append(chunk)
        total += len(chunk)
        if total > max_bytes:
          raise ValueError("speed-camera snapshot exceeds size limit")
      if expected_length is not None and total != expected_length:
        raise ValueError("speed-camera response length does not match Content-Length")
      body = b"".join(body_parts)
  except HTTPError as error:
    error.close()
    raise
  except TimeoutError as error:
    raise TimeoutError(f"speed-camera download read timeout; body_bytes={total}; {error}") from error
  except URLError as error:
    if isinstance(error.reason, TimeoutError):
      raise TimeoutError(f"speed-camera download connection timeout; body_bytes={total}; {error.reason}") from error
    raise
  if len(body) > max_bytes:
    raise ValueError("speed-camera snapshot exceeds size limit")
  if not body or body.lstrip().startswith((b"<", b"<!DOCTYPE")):
    raise ValueError("speed-camera snapshot is empty or HTML")
  return body


def download_database(url: str, path: str, version: str, timeout_s: float = 120,
                      max_bytes: int = 64 * 1024 * 1024) -> CameraDatabase:
  """Download, parse and atomically save one configured snapshot."""
  if not path or not version:
    raise ValueError("speed-camera path and version are required")
  body = fetch_snapshot(url, timeout_s, max_bytes)
  from openpilot.sunnypilot.mapd.speed_cameras import is_igo_snapshot, parse_igo_snapshot
  if is_igo_snapshot(body):
    cameras = tuple(parse_igo_snapshot(body, strict=True))
  else:
    cameras = tuple(parse_camera_snapshot(body))
  if not cameras:
    raise ValueError("speed-camera snapshot is empty or header-only")
  digest = hashlib.sha256(body).hexdigest()
  database = CameraDatabase(version, time.time(), cameras, urlparse(url).netloc.lower(), digest)  # noqa: TID251 -- persist Unix epoch
  if not database.cameras:
    raise ValueError("speed-camera snapshot contains no valid cameras")
  save_database(path, database)
  return database
