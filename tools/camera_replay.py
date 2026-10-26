#!/usr/bin/env python3
"""Deterministic, synthetic camera replay for a local UI development session.

  python tools/camera_replay.py --scenario all --dryrun
  python tools/camera_replay.py --scenario approach --publish --repeat 3

Dryrun needs only Python's standard library. Publication needs a built Linux
cereal/msgq environment with this checkout's camera schema. Only liveMapDataSP
is published; no vehicle state, controls, GPS messages, or CAN are injected.
Use an isolated development messaging environment with mapd stopped.

The two-second GPS age gate below is a replay policy, NOT proof that production
mapd implements a freshness timeout. Likewise this does not test Qt rendering.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType


def _load_camera_helpers() -> ModuleType:
  # mapd/__init__.py imports openpilot.common. Load just the existing pure helper
  # so dryrun also works in Windows checkouts with unmaterialized package links.
  name = '_camera_replay_speed_cameras'
  if name not in sys.modules:
    path = Path(__file__).resolve().parents[1] / 'sunnypilot/mapd/speed_cameras.py'
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
      raise RuntimeError(f'Cannot load camera helpers: {path}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolves annotations through this entry
    try:
      spec.loader.exec_module(module)
    except Exception:
      del sys.modules[name]
      raise
  return sys.modules[name]


_cameras = _load_camera_helpers()
GPS_MAX_AGE_S = 2.0
MAX_DISTANCE_M = 5_000.0
SERVICE = 'liveMapDataSP'
LATITUDE = 55.75
LONGITUDE = 37.62


@dataclass(frozen=True)
class Sample:
  time_s: float
  latitude: float
  longitude: float = LONGITUDE
  heading_deg: float | None = 0.0
  fix_time_s: float | None = None
  gps_valid: bool = True


@dataclass(frozen=True)
class Expected:
  reason: str
  distance_m: float = 0.0
  speed_kph: float = 0.0


@dataclass(frozen=True)
class Scenario:
  name: str
  cameras: tuple
  samples: tuple[Sample, ...]
  expected: tuple[Expected, ...]


def _latitude(offset_m: float) -> float:
  return LATITUDE + math.degrees(offset_m / _cameras.EARTH_RADIUS_M)


def _camera(offset_m: float = 0, direction: float = 0, direction_type: int = 1, speed: float = 60):
  # Exercise the current snapshot parser as well as the current resolver.
  return _cameras.parse_camera_snapshot([{
    'lat': _latitude(offset_m), 'lng': LONGITUDE, 'dir': direction,
    'dirType': direction_type, 'speedControls': [{'type': 'current', 'speed': speed}], 'type': 'fixed',
  }])[0]


def _sample(time_s: float, offset_m: float, **kwargs) -> Sample:
  kwargs.setdefault('fix_time_s', time_s)
  return Sample(time_s=time_s, latitude=_latitude(offset_m), **kwargs)


def build_scenarios() -> dict[str, Scenario]:
  camera = _camera()
  cases = [
    Scenario('approach', (camera,),
             tuple(_sample(i, offset) for i, offset in enumerate((-800, -300, -100, -25, 25))),
             tuple(Expected('camera_ahead', distance, 60) for distance in (800, 300, 100, 25)) + (Expected('no_camera_ahead'),)),
    Scenario('behind', (_camera(-25),), (_sample(0, 0),), (Expected('no_camera_ahead'),)),
    Scenario('opposite_direction', (_camera(100, 180),), (_sample(0, 0),), (Expected('no_camera_ahead'),)),
    Scenario('direction_selection', (_camera(-25), _camera(100, 180, speed=40), _camera(300, speed=80)),
             (_sample(0, 0),), (Expected('camera_ahead', 300, 80),)),
    Scenario('bidirectional', (_camera(100, 180, 2),), (_sample(0, 0),), (Expected('camera_ahead', 100, 60),)),
    Scenario('all_directions', (_camera(100, 180, 0),), (_sample(0, 0),), (Expected('camera_ahead', 100, 60),)),
    Scenario('heading_wrap', (_camera(100, 1),), (_sample(0, 0, heading_deg=359),), (Expected('camera_ahead', 100, 60),)),
    Scenario('outside_radius', (_camera(5_100),), (_sample(0, 0),), (Expected('no_camera_ahead'),)),
    Scenario('stale_gps', (camera,),
             (_sample(0, -100), _sample(2, -100, fix_time_s=0), _sample(3, -100, fix_time_s=0), _sample(4, -50)),
             (Expected('camera_ahead', 100, 60), Expected('camera_ahead', 100, 60),
              Expected('gps_stale'), Expected('camera_ahead', 50, 60))),
    Scenario('invalid_gps', (camera,),
             (_sample(0, -100), _sample(1, -100, gps_valid=False), _sample(2, -100, fix_time_s=None), _sample(3, -50)),
             (Expected('camera_ahead', 100, 60), Expected('gps_invalid'), Expected('gps_missing'), Expected('camera_ahead', 50, 60))),
  ]
  return {case.name: case for case in cases}


def empty_map_data() -> dict:
  # Generic speed-limit fields stay invalid: this is camera HUD replay only.
  return {
    'speedLimitValid': False, 'speedLimit': 0.0,
    'speedLimitAheadValid': False, 'speedLimitAhead': 0.0, 'speedLimitAheadDistance': 0.0,
    'roadName': '', 'speedCameraAheadValid': False, 'speedCameraAheadDistance': 0.0,
    'speedCameraSpeedLimit': 0.0, 'speedCameraType': '',
  }


def resolve_sample(cameras: tuple, sample: Sample) -> dict:
  if not math.isfinite(sample.time_s) or sample.time_s < 0:
    raise ValueError('Replay time must be finite and nonnegative')
  data = empty_map_data()
  age = None
  gps_ok = False
  if not sample.gps_valid:
    reason = 'gps_invalid'
  elif sample.fix_time_s is None:
    reason = 'gps_missing'
  elif not math.isfinite(sample.fix_time_s):
    reason = 'gps_invalid'
  elif not (math.isfinite(sample.latitude) and -90 <= sample.latitude <= 90
            and math.isfinite(sample.longitude) and -180 <= sample.longitude <= 180):
    reason = 'gps_invalid'
  else:
    age = sample.time_s - sample.fix_time_s
    if age < 0:
      reason = 'gps_future'
    elif age > GPS_MAX_AGE_S:
      reason = 'gps_stale'
    else:
      gps_ok = True
      if sample.heading_deg is None or not math.isfinite(sample.heading_deg):
        reason = 'heading_missing'
      else:
        match = _cameras.find_nearest_camera(cameras, sample.latitude, sample.longitude,
                                             heading_deg=sample.heading_deg, max_distance_m=MAX_DISTANCE_M)
        reason = 'no_camera_ahead' if match is None else 'camera_ahead'
        if match is not None:
          data.update(speedCameraAheadValid=True, speedCameraAheadDistance=match.distance_m,
                      speedCameraSpeedLimit=(match.camera.speed_limit_kph or 0.0) / 3.6,
                      speedCameraType=match.camera.camera_type)
  return {'time_s': sample.time_s, 'gps_age_s': age, 'reason': reason, 'valid': gps_ok, SERVICE: data}


def replay_scenario(scenario: Scenario) -> list[dict]:
  if not scenario.samples or scenario.samples[0].time_s != 0:
    raise ValueError('Scenario must begin at zero and contain samples')
  if any(b.time_s <= a.time_s for a, b in zip(scenario.samples, scenario.samples[1:], strict=False)):
    raise ValueError('Scenario times must strictly increase')
  return [resolve_sample(scenario.cameras, sample) for sample in scenario.samples]


def validate_scenario(scenario: Scenario) -> list[dict]:
  frames = replay_scenario(scenario)
  if len(frames) != len(scenario.expected):
    raise ValueError(f'{scenario.name}: expectation count mismatch')
  for index, (frame, expected) in enumerate(zip(frames, scenario.expected, strict=True)):
    data = frame[SERVICE]
    visible = expected.reason == 'camera_ahead'
    expected_valid = expected.reason in ('camera_ahead', 'no_camera_ahead', 'heading_missing')
    if (frame['reason'] != expected.reason or frame['valid'] != expected_valid
        or data['speedCameraAheadValid'] != visible
        or not math.isclose(data['speedCameraAheadDistance'], expected.distance_m, abs_tol=0.05)
        or not math.isclose(data['speedCameraSpeedLimit'], expected.speed_kph / 3.6, abs_tol=1e-6)
        or data['speedCameraType'] != ('fixed' if visible else '')):
      raise ValueError(f'{scenario.name}[{index}]: scenario validation failed: {frame}')
  return frames


def _message(messaging, frame: dict):
  message = messaging.new_message(SERVICE)
  message.valid = frame['valid']
  for key, value in frame[SERVICE].items():
    setattr(message.liveMapDataSP, key, value)
  return message


def publish_scenarios(scenarios: list[Scenario], repeat: int = 1) -> None:
  if sys.platform != 'linux':
    raise RuntimeError('--publish requires Linux with built cereal/msgq; dryrun is portable')
  if not 1 <= repeat <= 100 or not scenarios:
    raise ValueError('Choose at least one scenario and a repeat count in 1..100')
  validated = [validate_scenario(scenario) for scenario in scenarios]
  messaging = importlib.import_module('cereal.messaging')
  clear = {'valid': False, SERVICE: empty_map_data()}
  # Fail on schema mismatch BEFORE opening a publisher or sending any frames.
  for frame in [frame for frames in validated for frame in frames] + [clear]:
    _message(messaging, frame)
  publisher = messaging.PubMaster([SERVICE])
  try:
    for _ in range(repeat):
      for frames in validated:
        start = time.monotonic()
        for frame in frames:
          time.sleep(max(0.0, start + frame['time_s'] - time.monotonic()))
          publisher.send(SERVICE, _message(messaging, frame))
        time.sleep(1.0)  # Let a 1 Hz subscriber observe the final frame.
  finally:
    publisher.send(SERVICE, _message(messaging, clear))


def main(argv: list[str] | None = None) -> int:
  scenarios = build_scenarios()
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument('--scenario', choices=[*scenarios, 'all'], default='approach')
  mode = parser.add_mutually_exclusive_group()
  mode.add_argument('--dryrun', '--dry-run', action='store_true', help='print deterministic JSON (default)')
  mode.add_argument('--publish', action='store_true', help='publish only liveMapDataSP in a Linux dev session')
  parser.add_argument('--repeat', type=int, default=1, help='publication repetitions, 1..100')
  args = parser.parse_args(argv)
  if not 1 <= args.repeat <= 100 or (not args.publish and args.repeat != 1):
    parser.error('--repeat must be in 1..100 and requires --publish')
  selected = list(scenarios.values()) if args.scenario == 'all' else [scenarios[args.scenario]]
  try:
    reports = [{'name': case.name, 'frames': validate_scenario(case)} for case in selected]
    if args.publish:
      publish_scenarios(selected, args.repeat)
    else:
      print(json.dumps({'mode': 'dryrun', 'service': SERVICE, 'validation': 'passed',
                        'gps_max_age_s': GPS_MAX_AGE_S, 'scenarios': reports}, sort_keys=True, indent=2, allow_nan=False))
  except (ValueError, RuntimeError, ImportError, AttributeError) as error:
    parser.exit(2, f'camera_replay: {error}\n')
  return 0


if __name__ == '__main__':
  raise SystemExit(main())
