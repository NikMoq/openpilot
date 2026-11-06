"""Bounded HTTPS-only Waze transport, sharing the existing camera HTTP deadline implementation."""
from __future__ import annotations

import math
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

ALLOWED_HOSTS = frozenset({"rt-xlb-row.waze.com", "rt-xlb-am.waze.com", "rt-xlb-il.waze.com",
                           "ctilesgcs-row.waze.com", "ctilesgcs-am.waze.com", "ctilesgcs-il.waze.com", "www.waze.com"})


class TransportError(OSError):
  def __init__(self, *args, category="transport", status=None):
    super().__init__(*args)
    self.category = category
    self.status = status


class _NoRedirect(HTTPRedirectHandler):
  def redirect_request(self, request, fp, code, message, headers, new_url):
    fp.close()
    raise TransportError("Waze endpoint redirected; credentials were not forwarded")


def request(url: str, body: bytes | None = None, headers: dict | None = None,
            timeout_s: float = 25.0, max_bytes: int = 8 * 1024 * 1024, cookie_jar: CookieJar | None = None) -> bytes:
  parsed = urlparse(url)
  if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.port not in (None, 443) or parsed.username or parsed.password:
    raise ValueError("Waze transport requires an approved HTTPS endpoint")
  if parsed.hostname == "www.waze.com" and (body is not None or parsed.path not in (
      "/row-Descartes/app/Features", "/Descartes/app/Features", "/il-Descartes/app/Features")):
    raise ValueError("Waze editor transport permits read-only map features only")
  if not math.isfinite(timeout_s) or timeout_s <= 0 or max_bytes <= 0:
    raise ValueError("Waze transport requires positive bounds")
  from openpilot.sunnypilot.mapd.camera_database import _HTTPDeadline, _DeadlineHTTPHandler, _DeadlineHTTPSHandler
  budget = _HTTPDeadline(timeout_s)
  handlers = [_DeadlineHTTPHandler(budget), _DeadlineHTTPSHandler(budget), _NoRedirect()]
  if cookie_jar is not None:
    handlers.append(HTTPCookieProcessor(cookie_jar))
  opener = build_opener(*handlers)
  safe_headers = {"User-Agent": "SunnyPilot Waze acquisition", **(headers or {})}
  query = Request(url, data=body, headers=safe_headers, method="POST" if body is not None else "GET")
  total = 0
  try:
    with opener.open(query, timeout=budget.remaining()) as response:
      if response.status != 200 or response.headers.get("Content-Range"):
        raise TransportError(f"Waze endpoint requires complete HTTP 200; got {response.status}")
      if "html" in response.headers.get("Content-Type", "").lower():
        raise TransportError("Waze endpoint returned HTML")
      length = response.headers.get("Content-Length")
      if length is not None:
        try:
          length = int(length)
        except ValueError:
          raise TransportError("Waze endpoint returned invalid length") from None
        if length < 0 or length > max_bytes:
          raise TransportError("Waze response exceeds size limit")
      parts = []
      while True:
        budget.remaining()
        chunk = response.read1(min(65536, max_bytes + 1 - total))
        if not chunk:
          break
        total += len(chunk)
        if total > max_bytes:
          raise TransportError("Waze response exceeds size limit")
        parts.append(chunk)
      if length is not None and total != length:
        raise TransportError("Waze endpoint returned a truncated response")
      result = b"".join(parts)
      if not result or result.lstrip().startswith(b"<"):
        raise TransportError("Waze endpoint returned empty data or HTML")
      return result
  except HTTPError as error:
    status = error.code
    error.close()
    raise TransportError(f"Waze HTTP {status}", category="http", status=status) from None


class WazeTransport:
  """One RT session's cookies, including server affinity; never process-global."""
  def __init__(self):
    self.cookie_jar = CookieJar()

  def __call__(self, url: str, **kwargs) -> bytes:
    return request(url, cookie_jar=self.cookie_jar, **kwargs)
