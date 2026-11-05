"""Bounded, anonymous GET transport for the public Yandex road-event layer."""
from __future__ import annotations

import math
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class YandexTransportError(OSError):
  pass


class _NoRedirect(HTTPRedirectHandler):
  def redirect_request(self, request, fp, code, message, headers, new_url):
    fp.close()
    raise YandexTransportError("Yandex event endpoint redirected")


def request(url: str, *, timeout_s: float = 20, max_bytes: int = 2 * 1024 * 1024) -> bytes:
  parsed = urlsplit(url)
  if (parsed.scheme != "https" or parsed.hostname != "core-road-events-renderer.maps.yandex.net" or
      parsed.port not in (None, 443) or parsed.path != "/2.x/tiles" or parsed.username or parsed.password or parsed.fragment):
    raise ValueError("Only the public read-only Yandex event tile endpoint is supported")
  if not math.isfinite(timeout_s) or timeout_s <= 0 or not 0 < max_bytes <= 2 * 1024 * 1024:
    raise ValueError("Invalid Yandex transport bounds")
  from openpilot.sunnypilot.mapd.camera_database import _HTTPDeadline, _DeadlineHTTPHandler, _DeadlineHTTPSHandler
  budget = _HTTPDeadline(timeout_s)
  opener = build_opener(_DeadlineHTTPHandler(budget), _DeadlineHTTPSHandler(budget), _NoRedirect())
  query = Request(url, headers={"User-Agent": "SunnyPilot road-event acquisition"}, method="GET")
  try:
    with opener.open(query, timeout=budget.remaining()) as response:
      if response.status != 200 or response.headers.get("Content-Range"):
        raise YandexTransportError("Yandex event endpoint did not return a complete HTTP 200 response")
      if "protobuf" not in response.headers.get("Content-Type", "").lower():
        raise YandexTransportError("Yandex event endpoint did not return protobuf")
      length = response.headers.get("Content-Length")
      if length is not None:
        try:
          length = int(length)
        except ValueError:
          raise YandexTransportError("Invalid Yandex response length") from None
        if not 0 < length <= max_bytes:
          raise YandexTransportError("Yandex response exceeds bounds")
      parts, total = [], 0
      while True:
        budget.remaining()
        chunk = response.read1(min(65536, max_bytes + 1 - total))
        if not chunk:
          break
        total += len(chunk)
        if total > max_bytes:
          raise YandexTransportError("Yandex response exceeds bounds")
        parts.append(chunk)
      if total == 0 or length is not None and total != length:
        raise YandexTransportError("Incomplete Yandex response")
      return b"".join(parts)
  except HTTPError as error:
    status = error.code
    error.close()
    raise YandexTransportError(f"Yandex HTTP {status}") from None
