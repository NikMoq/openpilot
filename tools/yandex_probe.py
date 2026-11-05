#!/usr/bin/env python3
"""Download real public Yandex road events without enabling vehicle controls.

python tools/yandex_probe.py --lat 45.060288 --lon 38.954668 --radius 5000
No account, API key, device-mode change, guest identity or location injection.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
if "openpilot" not in sys.modules:
  namespace = ModuleType("openpilot")
  namespace.__path__ = [str(ROOT)]
  sys.modules["openpilot"] = namespace

from openpilot.sunnypilot.mapd.yandex.backend import YandexPublicBackend


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--lat", type=float, required=True)
  parser.add_argument("--lon", type=float, required=True)
  parser.add_argument("--radius", type=float, default=1000)
  args = parser.parse_args()
  try:
    with tempfile.TemporaryDirectory(prefix="yandex-public-probe-") as directory:
      result = YandexPublicBackend(directory).fetch_events(args.lat, args.lon, args.radius)
      summary = {"status": "downloaded", "source": "yandex_public_road_events",
                 "events": len(result.events), "types": dict(Counter(e["raw_type"] for e in result.events)),
                 "loaded_tiles": result.loaded_tiles, "requested_tiles": result.requested_tiles,
                 "camera_inventory_available": result.camera_inventory_available,
                 "speed_limits_available": result.speed_limits_available,
                 "vehicle_control_enabled": False}
  except Exception as error:
    # Endpoint responses and account/challenge text are never printed.
    print(json.dumps({"status": "failed", "error_class": type(error).__name__}))
    return 1
  print(json.dumps(summary, ensure_ascii=True))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
