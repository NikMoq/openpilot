"""Raw Waze acquisition facade. Call only from the map-data network worker."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .rt import GuestStore, RTClient
from .clock import utc_now_s
from .tiles import FixedTileClient, region_for
from .transport import request
from .roads import WazeRoadClient
from .road_match import annotate_road_limit, annotate_mobile_corridor


@dataclass(frozen=True)
class AcquisitionResult:
  events: list[dict]
  removed_ids: list[str] = field(default_factory=list)
  requested_tiles: int = 0
  loaded_tiles: int = 0
  unknown_count: int = 0
  observed_at_ms: int = 0
  complete: bool = True
  region: str = "row"
  # Successful transport/coverage is not a guarantee that Waze knows every camera.
  inventory_complete: bool = False


class WazeBackend:
  def __init__(self, state_dir: Path | str, transport=request, clock=utc_now_s, monotonic=time.monotonic):
    self._store = GuestStore(Path(state_dir))
    self._transport, self._clock, self._monotonic = transport, clock, monotonic
    self._tiles = FixedTileClient(self._store.directory / "tiles", transport, clock, monotonic)
    self._roads = WazeRoadClient(self._store.directory / "roads", transport=transport, clock=clock, monotonic=monotonic)
    self.road_error_count = 0
    self._sessions: dict[str, RTClient] = {}
    self._lock = threading.Lock()

  def fetch_fixed(self, lat: float, lon: float, radius_m: float, timeout_s: float = 120) -> AcquisitionResult:
    with self._lock:
      return AcquisitionResult(**self._tiles.fetch(lat, lon, radius_m, timeout_s))

  def fetch_mobile(self, lat: float, lon: float, radius_m: float, timeout_s: float = 120) -> AcquisitionResult:
    with self._lock:
      region = region_for(lat, lon)
      client = self._sessions.setdefault(region, RTClient(self._store, region, self._transport, self._clock, self._monotonic))
      return AcquisitionResult(**client.fetch(lat, lon, radius_m, timeout_s))

  def enrich_events(self, events: list[dict], timeout_s: float = 45, *, should_continue=None) -> list[dict]:
    """Attach independently sourced posted road limits; failed lookups keep warnings.

    Report facing never filters mobile enforcement. A two-way road can yield two
    legal-direction limits, which policy exposes as separate approach candidates.
    """
    with self._lock:
      deadline = self._monotonic() + timeout_s
      output, attempted = [], 0
      self.road_error_count = 0
      for event in events:
        if should_continue is not None and not should_continue():
          return output
        relevant = ((event.get("source_kind") == "fixed" and event.get("raw_subtype") in ("SPEED_CAMERA", "RED_LIGHT_CAMERA")) or
                    (event.get("source_kind") == "mobile" and event.get("raw_type") in ("POLICE", "CAMERA")))
        remaining = deadline - self._monotonic()
        if not relevant:
          output.append(dict(event))
          continue
        if attempted >= 32 or remaining <= 0:
          self.road_error_count += 1
          output.append(annotate_road_limit(event, (), int(self._clock() * 1000)))
          continue
        attempted += 1
        try:
          roads = self._roads.fetch(event["latitude"], event["longitude"], radius_m=150, timeout_s=remaining)
          if not roads.complete or roads.unknown_count or roads.loaded_windows != roads.requested_windows:
            raise ValueError("Incomplete Waze road window")
          now_ms = int(self._clock() * 1000)
          if event["source_kind"] == "mobile":
            output.extend(annotate_mobile_corridor(event, roads.segments, now_ms))
          else:
            output.append(annotate_road_limit(event, roads.segments, now_ms, camera_links=roads.camera_links))
        except (OSError, ValueError, TimeoutError):
          self.road_error_count += 1
          output.append(annotate_road_limit(event, (), int(self._clock() * 1000)))
      return output
