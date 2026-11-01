"""Direct SCO delivery, with one fixed same-provider release fallback.

Only the exact DEFAULT_SPEED_CAMERA_URL opts into fallback. This module owns
that constant so the updater can import it without a dependency cycle. It
never imports the updater, changes configuration, or accepts a manifest URL.

The HTTPS manifest and digest identify delivered bytes, not a signed provider
attestation. Reject stale/inconsistent snapshots before the atomic cache write;
retain the source acquisition time rather than rejuvenating mirrored data.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from http.client import HTTPException

from openpilot.sunnypilot.mapd.camera_database import CameraDatabase, download_database, fetch_snapshot, save_database
from openpilot.sunnypilot.mapd.cityguide import parse_cityguide_snapshot


DEFAULT_SPEED_CAMERA_URL = "https://speedcamonline.ru/cg77/Rus/comment/"
MIRROR_RELEASE_BASE = "https://github.com/NikMoq/openpilot/releases/download/speedcamonline-rus/"
MIRROR_MANIFEST_URL = MIRROR_RELEASE_BASE + "manifest.json"
MANIFEST_MAX_BYTES = 64 * 1024
MAX_SOURCE_AGE_S = 7 * 24 * 60 * 60
MAX_FUTURE_S = 300
MAX_TOTAL_TIMEOUT_S = 120.0
DIRECT_TIMEOUT_S = 30.0
MIN_RECORD_COUNT = 10_000


@dataclass(frozen=True)
class _Manifest:
  fetched_at: float
  record_count: int
  sha256: str
  size_bytes: int
  asset_name: str


def _unique_object(pairs):
  result = {}
  for key, value in pairs:
    if key in result:
      raise ValueError("duplicate mirror manifest field")
    result[key] = value
  return result


def _reject_constant(value):
  raise ValueError("non-finite JSON constant in mirror manifest")


def _check_timestamp(fetched_at: float, now: float) -> None:
  if not math.isfinite(now) or not math.isfinite(fetched_at) or not 0 <= fetched_at:
    raise ValueError("invalid mirror timestamp")
  if not now - MAX_SOURCE_AGE_S <= fetched_at <= now + MAX_FUTURE_S:
    raise ValueError("mirror timestamp is stale or too far in the future")


def _parse_manifest(raw: bytes, max_bytes: int) -> _Manifest:
  if len(raw) > MANIFEST_MAX_BYTES:
    raise ValueError("mirror manifest exceeds size limit")
  try:
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
  except (UnicodeDecodeError, json.JSONDecodeError) as error:
    raise ValueError("invalid mirror manifest JSON/encoding") from error
  if not isinstance(value, dict):
    raise ValueError("mirror manifest must be an object")
  if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
    raise ValueError("unsupported mirror schema_version")
  if value.get("source_url") != DEFAULT_SPEED_CAMERA_URL:
    raise ValueError("mirror source_url does not match SpeedCamOnline CG77")
  timestamp = value.get("fetched_at")
  if type(timestamp) not in (int, float):
    raise ValueError("invalid mirror fetched_at")
  try:
    timestamp = float(timestamp)
  except OverflowError as error:
    raise ValueError("invalid mirror fetched_at") from error
  _check_timestamp(timestamp, time.time())  # noqa: TID251 -- manifest acquisition time is Unix epoch
  count, size, digest = value.get("record_count"), value.get("size_bytes"), value.get("sha256")
  if type(count) is not int or count < MIN_RECORD_COUNT:
    raise ValueError("invalid mirror record_count")
  if type(size) is not int or not 0 < size <= max_bytes:
    raise ValueError("invalid mirror size_bytes")
  if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
    raise ValueError("invalid mirror sha256")
  name = value.get("asset_name")
  if name != f"speedcam-rus-{digest}.bkm":
    raise ValueError("invalid mirror asset_name")
  return _Manifest(timestamp, count, digest, size, name)


def download_camera_database(url: str, path: str, version: str, timeout_s: float = 120,
                             max_bytes: int = 64 * 1024 * 1024) -> CameraDatabase:
  """Direct import; exact default alone may fall back to the fixed release.

  One monotonic budget (at most 120s) covers direct/manifest/asset requests.
  The direct attempt gets at most 30s. Network deadlines retain the documented
  stdlib DNS/trust-store limits; CPU parsing/atomic disk writes are not
  interruptible. No retries or fallback to a manifest-provided URL occur.
  """
  if not path or not version:
    raise ValueError("speed-camera path and version are required")
  if not math.isfinite(timeout_s) or timeout_s <= 0:
    raise ValueError("speed-camera timeout must be positive")
  if type(max_bytes) is not int or max_bytes <= 0:
    raise ValueError("speed-camera size limit must be a positive integer")
  timeout_s = min(timeout_s, MAX_TOTAL_TIMEOUT_S)
  if url != DEFAULT_SPEED_CAMERA_URL:
    return download_database(url, path, version, timeout_s=timeout_s, max_bytes=max_bytes)

  deadline = time.monotonic() + timeout_s

  def remaining():
    result = deadline - time.monotonic()
    if result <= 0:
      raise TimeoutError("speed-camera source delivery total deadline exceeded")
    return result

  try:
    return download_database(url, path, version, timeout_s=min(DIRECT_TIMEOUT_S, remaining()), max_bytes=max_bytes)
  except (OSError, ValueError, HTTPException) as direct_error:
    try:
      raw_manifest = fetch_snapshot(MIRROR_MANIFEST_URL, timeout_s=remaining(), max_bytes=MANIFEST_MAX_BYTES)
      manifest = _parse_manifest(raw_manifest, max_bytes)
      # No manifest-provided URL or path is used, even if extra fields exist.
      raw = fetch_snapshot(MIRROR_RELEASE_BASE + manifest.asset_name,
                           timeout_s=remaining(), max_bytes=manifest.size_bytes)
      if len(raw) != manifest.size_bytes:
        raise ValueError("mirror asset length does not match manifest")
      if hashlib.sha256(raw).hexdigest() != manifest.sha256:
        raise ValueError("mirror asset SHA-256 does not match manifest")
      cameras = tuple(parse_cityguide_snapshot(raw))
      if len(cameras) != manifest.record_count or len(cameras) < MIN_RECORD_COUNT:
        raise ValueError("mirror parsed record count does not match manifest")
      _check_timestamp(manifest.fetched_at, time.time())  # noqa: TID251 -- manifest acquisition time is Unix epoch
      remaining()  # Do not commit a mirror result after exhausting the budget.
      result = CameraDatabase(version, manifest.fetched_at, cameras, "speedcamonline.ru", manifest.sha256)
      save_database(path, result)
      return result
    except (OSError, ValueError, HTTPException) as mirror_error:
      raise mirror_error from direct_error
