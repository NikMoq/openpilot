"""Versioned local cache and optional downloader for speed-camera snapshots."""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass
from urllib.request import Request, urlopen

from openpilot.sunnypilot.mapd.speed_cameras import SpeedCamera, parse_camera_snapshot


@dataclass(frozen=True)
class CameraDatabase:
  version: str
  fetched_at: float
  cameras: tuple[SpeedCamera, ...]


def load_database(path: str, max_age_s: float | None = None) -> CameraDatabase | None:
  try:
    with open(path, encoding="utf-8") as stream:
      payload = json.load(stream)
    fetched_at = float(payload["fetched_at"])
    if max_age_s is not None and time.time() - fetched_at > max_age_s:
      return None
    cameras = tuple(parse_camera_snapshot(payload["data"]))
    return CameraDatabase(str(payload.get("version", "unknown")), fetched_at, cameras)
  except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
    return None


def save_database(path: str, database: CameraDatabase) -> None:
  directory = os.path.dirname(path) or "."
  os.makedirs(directory, exist_ok=True)
  payload = {
    "version": database.version,
    "fetched_at": database.fetched_at,
    "data": [{
      "latitude": camera.latitude,
      "longitude": camera.longitude,
      "speed_limit_kph": camera.speed_limit_kph,
      "direction_deg": camera.direction_deg,
      "direction_type": camera.direction_type,
      "camera_type": camera.camera_type,
      "end_latitude": camera.end_latitude,
      "end_longitude": camera.end_longitude,
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


def download_database(url: str, path: str, version: str, timeout_s: float = 15,
                      max_bytes: int = 64 * 1024 * 1024) -> CameraDatabase:
  """Download one configured snapshot; callers must provide a permitted URL."""
  request = Request(url, headers={"User-Agent": "SunnyPilot speed-camera updater"})
  with urlopen(request, timeout=timeout_s) as response:
    body = response.read(max_bytes + 1)
  if len(body) > max_bytes:
    raise ValueError("speed-camera snapshot exceeds size limit")
  database = CameraDatabase(version, time.time(), tuple(parse_camera_snapshot(body)))
  if not database.cameras:
    raise ValueError("speed-camera snapshot contains no valid cameras")
  save_database(path, database)
  return database
