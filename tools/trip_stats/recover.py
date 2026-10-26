#!/usr/bin/env python3
"""Export measured local log samples as JSONL; never modifies the live ledger.

Usage: python3 tools/trip_stats/recover.py LOG_DIRECTORY... --output NEW.jsonl
Requires openpilot LogReader dependencies only when actual logs are read.
"""
from __future__ import annotations

import argparse
import bisect
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import shutil
import tempfile
import warnings

NAMES = {'rlog', 'rlog.bz2', 'rlog.zst', 'qlog', 'qlog.bz2', 'qlog.zst'}
MAX_BYTES = 128 * 1024 * 1024
MAX_EVENTS = 1_000_000
MAX_FILES = 10000
MIN_EPOCH = 1262304000.0  # 2010; reject unset/implausible device clocks.
MAX_EPOCH = 4102444800.0
_SCHEMA_CACHE = None


def log_name(name):
  return name in NAMES or re.fullmatch(r'[0-9]+\.rlog', name) is not None


def identity(path: Path):
  """Recognize logger segment names without using a host directory as route ID."""
  parent = path.parent.name
  if re.fullmatch(r'[0-9]+\.rlog', path.name):
    return parent, int(path.stem)
  match = re.fullmatch(r'(.+)--([0-9]+)', parent)
  if match:
    return match[1], int(match[2])
  if parent.isdecimal() and path.parent.parent.name:
    return path.parent.parent.name, int(parent)
  raise ValueError('unrecognized route/segment directory')


def _local_path(value):
  text = os.fspath(value)
  if '://' in text or text.startswith(('//', '\\\\')):
    raise ValueError('only local filesystem paths are accepted')
  path = Path(text).absolute()
  if any(part.is_symlink() for part in [path, *path.parents]):
    raise ValueError('symbolic link paths are not accepted')
  return path


def discover(inputs):
  found = set()
  for value in inputs:
    path = _local_path(value)
    if path.is_file():
      if not log_name(path.name):
        raise ValueError('explicit file must be an rlog/qlog')
      found.add(path)
    elif path.is_dir():
      for root, directories, files in os.walk(path, followlinks=False):
        directories[:] = sorted(d for d in directories if not (Path(root) / d).is_symlink())
        for name in files:
          candidate = Path(root) / name
          if log_name(name) and not candidate.is_symlink():
            found.add(candidate)
        if len(found) > MAX_FILES:
          raise ValueError('too many log files')
    else:
      raise ValueError('local input does not exist')
  if len(found) > MAX_FILES:
    raise ValueError('too many log files')
  return sorted(found)


def fingerprint(path):
  descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
  try:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_BYTES:
      raise ValueError('log must be a nonempty bounded regular file')
    digest = hashlib.sha256()
    remaining = before.st_size
    while remaining:
      block = os.read(descriptor, min(1024 * 1024, remaining))
      if not block:
        raise ValueError('log changed while hashing')
      digest.update(block)
      remaining -= len(block)
    after = os.fstat(descriptor)
    attrs = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    if attrs(before) != attrs(after) or attrs(after) != attrs(os.stat(path, follow_symlinks=False)):
      raise ValueError('log changed while hashing')
    return digest.hexdigest(), attrs(after)
  finally:
    os.close(descriptor)


def diagnostic_schema():
  global _SCHEMA_CACHE
  if _SCHEMA_CACHE is None:
    import capnp
    root = Path(__file__).resolve().parents[2]
    temporary = tempfile.TemporaryDirectory(prefix='trip-stats-schema-')
    target = Path(temporary.name)
    (target / 'include').mkdir()
    for source in (root / 'cereal').glob('*.capnp'):
      text = source.read_text(encoding='utf-8').strip()
      resolved = (source.parent / text).resolve() if len(text.splitlines()) == 1 and text.endswith('.capnp') else source
      shutil.copyfile(resolved, target / source.name)
    shutil.copyfile(root / 'cereal/include/c++.capnp', target / 'include/c++.capnp')
    _SCHEMA_CACHE = (capnp.load(str(target / 'log.capnp')), temporary)
  return _SCHEMA_CACHE[0]


def local_events(path):
  if re.fullmatch(r'[0-9]+\.rlog', path.name):
    # Recorder's uncompressed .rlog suffix is rejected by public LogReader.
    # Resolve Windows text-symlink schemas in temporary files, never the source tree.
    yield from diagnostic_schema().Event.read_multiple_bytes(path.read_bytes())
    return
  # Empty source list prevents remote fallback; path has already been checked.
  from openpilot.tools.lib.logreader import LogReader
  yield from LogReader(str(path), sources=[], sort_by_time=False)


def normalized_samples(events, inherited_real_car=None, epoch_anchors=()):
  states, anchors, samples = [], list(epoch_anchors), []
  real_car = inherited_real_car
  previous_car_mono = None
  duplicates = 0
  started_mono_times = set()
  seen = set()
  for count, event in enumerate(events, 1):
    if count > MAX_EVENTS:
      raise ValueError('event bound exceeded')
    kind = event.which()
    if kind not in {'carState', 'deviceState', 'carParams', 'initData', 'clocks'}:
      continue
    mono = int(event.logMonoTime)
    if mono < 0:
      raise ValueError('invalid monotonic timestamp')
    data = getattr(event, kind)
    valid = bool(getattr(event, 'valid', False))
    if kind == 'carParams' and valid:
      evidence = hasattr(data, 'notCar')
      candidate = evidence and not bool(data.notCar)
      for field in ['brand', 'carName']:
        if hasattr(data, field) and str(getattr(data, field)).lower() == 'mock':
          candidate = False
      if hasattr(data, 'carFingerprint') and not str(data.carFingerprint).strip():
        candidate = False
      real_car = candidate if real_car is None else real_car and candidate
    elif kind == 'deviceState':
      states.append((mono, valid and bool(data.started)))
      started_at = int(getattr(data, 'startedMonoTime', 0))
      if valid and data.started and started_at > 0:
        started_mono_times.add(started_at)
    elif kind in {'initData', 'clocks'} and valid:
      wall = float(getattr(data, 'wallTimeNanos', 0)) / 1e9
      if math.isfinite(wall) and MIN_EPOCH <= wall < MAX_EPOCH:
        anchors.append((mono, wall))
    elif kind == 'carState':
      if previous_car_mono is not None and mono < previous_car_mono:
        raise ValueError('carState monotonic reset/out-of-order data')
      previous_car_mono = mono
      if mono in seen:
        duplicates += 1
        continue
      seen.add(mono)
      speed = float(data.vEgo)
      valid = valid and math.isfinite(speed) and 0 <= speed <= 100
      samples.append({'mono_ns': mono, 'speed_m_s': speed if valid else 0.0, 'valid': valid})
  states.sort()
  anchors.sort()
  state_times = [v[0] for v in states]
  anchor_times = [v[0] for v in anchors]
  clock_discontinuity = any(abs((b[1] - a[1]) - (b[0] - a[0]) / 1e9) > 5 for a, b in zip(anchors, anchors[1:]))
  for sample in samples:
    mono = sample['mono_ns']
    index = bisect.bisect_right(state_times, mono) - 1
    sample['onroad'] = index >= 0 and mono - states[index][0] <= 2_000_000_000 and states[index][1]
    sample['real_car'] = real_car is True
    sample['wall_time'] = None
    if anchors and not clock_discontinuity:
      near = bisect.bisect_left(anchor_times, mono)
      candidates = anchors[max(0, near - 1):min(len(anchors), near + 1)]
      anchor = min(candidates, key=lambda value: abs(value[0] - mono))
      if abs(anchor[0] - mono) <= 120_000_000_000:
        sample['wall_time'] = anchor[1] + (mono - anchor[0]) / 1e9
  return samples, real_car, {'duplicate_samples': duplicates, 'clock_discontinuity': clock_discontinuity,
                             'epoch_anchors': len(anchors), 'vehicle_evidence': real_car,
                             'started_mono_times': sorted(started_mono_times)}


def recorder_anchor(path):
  sidecar = path.parent / 'metadata.json'
  if not re.fullmatch(r'[0-9]+\.rlog', path.name) or not sidecar.exists():
    return [], None
  _local_path(sidecar)
  if sidecar.stat().st_size > 1024 * 1024:
    raise ValueError('recorder metadata exceeds bound')
  raw = sidecar.read_bytes()
  data = json.loads(raw)
  if not str(data.get('format', '')).startswith('uncompressed concatenated cereal.log.Event'):
    raise ValueError('unrecognized recorder metadata format')
  mono = data.get('started_monotonic_ns')
  stamp = datetime.datetime.fromisoformat(data['started_utc'])
  if stamp.tzinfo is None:
    raise ValueError('recorder UTC anchor lacks timezone')
  wall = stamp.timestamp()
  if type(mono) is not int or mono < 0 or not MIN_EPOCH <= wall < MAX_EPOCH:
    raise ValueError('invalid recorder epoch anchor')
  return [(mono, wall)], hashlib.sha256(raw).hexdigest()


def recover(inputs, event_reader=local_events):
  groups = {}
  errors = []
  for path in discover(inputs):
    try:
      route, segment = identity(path)
      groups.setdefault((route, segment), []).append(path)
    except ValueError as error:
      errors.append({'path': str(path), 'error': str(error)})
  bundles = []
  vehicle = {}
  for (route, segment), paths in sorted(groups.items()):
    # Never add qlog and rlog measurements for the same segment. Prefer rlog;
    # among copies prefer the largest stable file, with deterministic tie-break.
    choices = sorted(paths, key=lambda p: (p.name.startswith('qlog'), -p.stat().st_size, str(p)))
    failures = []
    for path in choices:
      try:
        digest, signature = fingerprint(path)
        anchors, metadata_sha = recorder_anchor(path)
        with warnings.catch_warnings(record=True) as caught:
          warnings.simplefilter('always')
          samples, evidence, details = normalized_samples(event_reader(path), vehicle.get(route), anchors)
        if fingerprint(path) != (digest, signature):
          raise ValueError('source changed while decoding; retry after log closes')
        if not samples:
          raise ValueError('no measured carState samples')
        vehicle[route] = evidence
        known_route = re.fullmatch(r'[0-9]+\.rlog', path.name) is None
        bundles.append({'source_id': f'{route}/{segment}/sha256:{digest}', 'route_id': route,
                        'segment_id': segment, 'source_sha256': digest,
                        'known_route': known_route, 'count_route': known_route,
                        'source_kind': 'qlog' if path.name.startswith('qlog') else 'rlog',
                        'samples': samples, 'provenance': {'file': f'{route}/{segment}/{path.name}', 'size': signature[2],
                        'alternative_files_omitted': len(paths) - 1, 'decode_warnings': [str(w.message) for w in caught],
                        'metadata_sha256': metadata_sha,
                        'partial': bool(caught), 'coverage': 'available measured samples only; not lifetime totals',
                        'routes_incomplete': not known_route,
                        **details}})
        break
      except Exception as error:
        failures.append({'path': str(path), 'error': f'{type(error).__name__}: {error}'})
    else:
      errors.extend(failures)
  return bundles, errors


def export_new(path, bundles):
  path = _local_path(path)
  descriptor, temporary = tempfile.mkstemp(prefix='.trip-recovery-', dir=path.parent)
  try:
    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as output:
      for bundle in bundles:
        output.write(json.dumps(bundle, ensure_ascii=False, allow_nan=False) + '\n')
      output.flush()
      os.fsync(output.fileno())
    # Atomic no-overwrite publication: never replace an existing recovery file.
    os.link(temporary, path)
  finally:
    os.unlink(temporary)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('paths', type=Path, nargs='+', help='Explicit local log files/directories; no default device path')
  parser.add_argument('--output', type=Path, required=True, help='New JSONL export; existing files are refused')
  args = parser.parse_args()
  bundles, errors = recover(args.paths)
  export_new(args.output, bundles)
  print(json.dumps({'status': 'OFFLINE_MEASURED_EXPORT', 'segments': len(bundles),
                    'samples': sum(len(b['samples']) for b in bundles), 'errors': errors,
                    'production_ledger_modified': False, 'historical_all_time_complete': False}))
  return 1 if errors else 0


if __name__ == '__main__':
  raise SystemExit(main())
