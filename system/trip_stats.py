"""Measured trip statistics. Standard-library only; no UI, cereal or control imports."""
import copy
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

VERSION = 1
MAX_GAP_NS = 2_000_000_000
MAX_SPEED = 100.0  # m/s; reject impossible/invalid observations, never alter vehicle control.
MAX_ACCEL = 12.0
MAX_STORE_BYTES = 16 * 1024 * 1024
UTC = dt.timezone.utc


def finite(value):
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    return False
  try:
    return math.isfinite(value)
  except OverflowError:
    return False


def utc_date(timestamp):
  if not finite(timestamp):
    return None
  try:
    value = dt.datetime.fromtimestamp(timestamp, UTC)
    return value.date() if 2020 <= value.year < 2100 else None
  except (ValueError, OverflowError, OSError):
    return None


def empty_ledger():
  return {'version': VERSION, 'sessions': {}, 'imports': {}, 'checkpoint': None,
          'coverage': {'measured_only': True, 'started_utc': None}}


def validate(data):
  if not isinstance(data, dict) or type(data.get('version')) is not int or data.get('version') != VERSION or not isinstance(data.get('sessions'), dict) or not isinstance(data.get('imports'), dict):
    raise ValueError('Invalid trip statistics schema; existing ledger must be preserved')
  if not isinstance(data.get('coverage'), dict):
    raise ValueError('Invalid coverage')
  for session in data['sessions'].values():
    if not isinstance(session, dict) or not isinstance(session.get('days'), dict):
      raise ValueError('Invalid trip session')
    if 'count_route' in session and type(session['count_route']) is not bool:
      raise ValueError('Invalid route-count provenance')
    if 'route_id' in session and (not isinstance(session['route_id'], str) or not session['route_id'] or len(session['route_id']) > 512):
      raise ValueError('Invalid route provenance')
    if 'route_ids' in session and (not isinstance(session['route_ids'], list) or len(session['route_ids']) > 16 or any(not isinstance(v, str) or not v or len(v) > 512 for v in session['route_ids'])):
      raise ValueError('Invalid route aliases')
    if 'last_mono_ns' in session and (type(session['last_mono_ns']) is not int or not 0 < session['last_mono_ns'] < 2**63):
      raise ValueError('Invalid session timestamp')
    for name in ('distance_m', 'duration_s'):
      if not finite(session.get(name)) or session[name] < 0:
        raise ValueError('Invalid measured totals')
    for date, bucket in session['days'].items():
      try:
        parsed = dt.date.fromisoformat(date)
      except (ValueError, TypeError):
        raise ValueError('Invalid trip date') from None
      if not 2020 <= parsed.year < 2100 or not isinstance(bucket, dict):
        raise ValueError('Invalid dated totals')
      for name in ('distance_m', 'duration_s'):
        if not finite(bucket.get(name)) or bucket[name] < 0:
          raise ValueError('Invalid dated measurement')
    if sum(x['distance_m'] for x in session['days'].values()) > session['distance_m'] + 1e-6 or sum(x['duration_s'] for x in session['days'].values()) > session['duration_s'] + 1e-6:
      raise ValueError('Dated measurements exceed all-time totals')
  for source, digest in data['imports'].items():
    if not isinstance(source, str) or not isinstance(digest, str) or len(digest) != 64:
      raise ValueError('Invalid recovery provenance')
  if 'runtime' in data and not isinstance(data['runtime'], dict):
    raise ValueError('Invalid runtime state')
  cp = data.get('checkpoint')
  if cp is not None:
    if not isinstance(cp, dict) or cp.get('session') not in data['sessions'] or not isinstance(cp.get('boot'), str) or not isinstance(cp.get('mono_ns'), int) or isinstance(cp['mono_ns'], bool) or not 0 < cp['mono_ns'] < 2**63 or not finite(cp.get('speed')) or not 0 <= cp['speed'] <= MAX_SPEED:
      raise ValueError('Invalid measurement checkpoint')
    if cp['mono_ns'] != data['sessions'][cp['session']].get('last_mono_ns', cp['mono_ns']):
      raise ValueError('Checkpoint differs from session watermark')
    if cp.get('wall_time') is not None and utc_date(cp['wall_time']) is None:
      raise ValueError('Invalid checkpoint wall clock')
  return data


class TripStats:
  def __init__(self, data=None):
    self.data = validate(copy.deepcopy(data)) if data is not None else empty_ledger()

  def break_interval(self):
    self.data['checkpoint'] = None

  def sample(self, *, boot, session, mono_ns, speed, onroad=True, valid=True, real_car=True, wall_time=None, received_ns=None, route_id=None, origin='live', count_route=True):
    """Integrate adjacent accepted samples only. Timestamps are bus event monotonic ns."""
    if not (onroad and valid and real_car) or not isinstance(boot, str) or not boot or not isinstance(session, str) or not session or len(session) > 512:
      self.break_interval()
      return False
    if isinstance(mono_ns, bool) or not isinstance(mono_ns, int) or not 0 < mono_ns < 2**63 or not finite(speed) or not 0 <= speed <= MAX_SPEED:
      self.break_interval()
      return False
    if received_ns is not None and (type(received_ns) is not int or not 0 < received_ns < 2**63 or not -100_000_000 <= received_ns - mono_ns <= 1_000_000_000):
      self.break_interval()
      return False
    previous = self.data['checkpoint']
    if previous and previous['boot'] == boot and mono_ns <= previous['mono_ns']:
      return False  # Duplicate/replayed/out-of-order samples cannot advance a persisted checkpoint.
    key = boot + ':' + session
    record = self.data['sessions'].setdefault(key, {'distance_m': 0.0, 'duration_s': 0.0, 'days': {}})
    if isinstance(route_id, str) and route_id and len(route_id) <= 512:
      existing_route = record.get('route_id')
      if existing_route and existing_route != route_id:
        aliases = record.setdefault('route_ids', [])
        if route_id not in aliases and len(aliases) < 16:
          aliases.append(route_id)
      else:
        record['route_id'] = route_id
    record['origin'] = origin
    record['count_route'] = record.get('count_route', False) or bool(count_route)
    if mono_ns <= record.get('last_mono_ns', 0):
      return False
    record['last_mono_ns'] = mono_ns
    wall = wall_time if utc_date(wall_time) else None
    current = {'boot': boot, 'session': key, 'mono_ns': mono_ns, 'speed': float(speed), 'wall_time': wall}
    self.data['checkpoint'] = current
    if not previous or previous['boot'] != boot or previous['session'] != key:
      return False
    gap = mono_ns - previous['mono_ns']
    elapsed = gap / 1e9
    if gap > MAX_GAP_NS or abs(speed - previous['speed']) > MAX_ACCEL * elapsed:
      return False
    distance = (previous['speed'] + speed) * 0.5 * elapsed
    record['distance_m'] += distance
    record['duration_s'] += elapsed
    old_wall = previous.get('wall_time')
    if wall is not None and old_wall is not None and abs((wall - old_wall) - elapsed) <= 0.25 and wall > old_wall:
      # Split midnight intervals proportionally using the same linearly interpolated speed.
      cursor = old_wall
      while cursor < wall:
        date = utc_date(cursor)
        boundary = dt.datetime.combine(date + dt.timedelta(days=1), dt.time(), UTC).timestamp()
        end = min(wall, boundary)
        a = (cursor - old_wall) / (wall - old_wall)
        b = (end - old_wall) / (wall - old_wall)
        duration = elapsed * (b - a)
        v0 = previous['speed'] + (speed - previous['speed']) * a
        v1 = previous['speed'] + (speed - previous['speed']) * b
        bucket = record['days'].setdefault(date.isoformat(), {'distance_m': 0.0, 'duration_s': 0.0})
        bucket['distance_m'] += (v0 + v1) * 0.5 * duration
        bucket['duration_s'] += duration
        cursor = end
      if self.data['coverage']['started_utc'] is None:
        self.data['coverage']['started_utc'] = dt.datetime.fromtimestamp(old_wall, UTC).isoformat()
    return True

  def snapshot(self, now=None):
    today = utc_date(now)
    start = today - dt.timedelta(days=6) if today else None
    total = {'routes': 0, 'distance_m': 0.0, 'duration_s': 0.0}
    week = dict(total)
    dated_distance = 0.0
    week_routes_incomplete = False
    for record in self.data['sessions'].values():
      if record['duration_s'] > 0 and record.get('count_route', True):
        total['routes'] += 1
      for field in ('distance_m', 'duration_s'):
        total[field] += record[field]
      week_route = False
      for date, bucket in record['days'].items():
        day = dt.date.fromisoformat(date)
        # Future clock buckets never appear as dated coverage or weekly totals.
        if today and day <= today:
          dated_distance += bucket['distance_m']
        if today and start <= day <= today:
          week_route |= bucket['duration_s'] > 0
          for field in ('distance_m', 'duration_s'):
            week[field] += bucket[field]
      week['routes'] += int(week_route and record.get('count_route', True))
      week_routes_incomplete |= week_route and not record.get('count_route', True)
    value = copy.deepcopy(self.data)
    value.update(all=total, week=week)
    value['coverage'].update(week_routes_incomplete=bool(week_routes_incomplete), routes_incomplete=any(record['duration_s'] > 0 and not record.get('count_route', True) for record in self.data['sessions'].values()), dated_distance_m=dated_distance, undated_distance_m=max(0.0, total['distance_m'] - dated_distance), week_utc_start=start.isoformat() if start else None, week_utc_end=today.isoformat() if today else None)
    return value


class RealCarGate:
  """Retry unavailable metadata at most once a second without changing session identity."""
  def __init__(self, read_metadata):
    self.read_metadata = read_metadata
    self.real_car = False
    self.next_check = 0.0

  def check(self, now, onroad):
    if not onroad:
      self.real_car = False
      self.next_check = 0.0
      return False
    if not self.real_car and now >= self.next_check:
      self.real_car = bool(self.read_metadata())
      self.next_check = now + 1.0
    return self.real_car


class DurableStore:
  """One exclusive producer. Corruption is fatal and never silently reset."""
  def __init__(self, directory):
    import fcntl
    self.directory = Path(directory)
    self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    self.lock = (self.directory / 'producer.lock').open('a+b')
    try:
      fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
      self.lock.close()
      raise RuntimeError('Another trip statistics producer owns the ledger') from None
    self.path = self.directory / 'stats.json'

  def load(self):
    if not self.path.exists():
      return empty_ledger()
    with self.path.open('rb') as source:
      raw = source.read(MAX_STORE_BYTES + 1)
    if len(raw) > MAX_STORE_BYTES:
      raise ValueError('Trip ledger too large; preserved without modification')
    def unique_pairs(pairs):
      result = {}
      for key, value in pairs:
        if key in result:
          raise ValueError('Duplicate trip ledger JSON field')
        result[key] = value
      return result
    return validate(json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Non-finite JSON'))))

  def save(self, data):
    validate(data)
    raw = (json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode()
    if len(raw) > MAX_STORE_BYTES:
      raise ValueError('Trip ledger size limit; preserve prior durable state')
    fd, temporary = tempfile.mkstemp(prefix='.stats-', dir=self.directory)
    try:
      with os.fdopen(fd, 'wb') as target:
        target.write(raw); target.flush(); os.fsync(target.fileno())
      os.replace(temporary, self.path)
      directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
      try:
        os.fsync(directory_fd)
      finally:
        os.close(directory_fd)
    finally:
      if os.path.exists(temporary):
        os.unlink(temporary)

  def close(self):
    self.lock.close()


def import_measured(data, bundle):
  """Explicit offline opt-in. Caller supplies measured, ordered monotonic samples, not directory counts."""
  source = bundle.get('source_id')
  route = bundle.get('route_id')
  samples = bundle.get('samples')
  if not isinstance(source, str) or not source or not isinstance(route, str) or not route or not isinstance(samples, list):
    raise ValueError('Recovery requires source_id, route_id and measured samples')
  count_route = bundle.get('count_route') is True and bundle.get('known_route') is True
  measurement = {'source_id': source, 'route_id': route, 'samples': samples, 'count_route': count_route}
  digest = hashlib.sha256(json.dumps(measurement, sort_keys=True, allow_nan=False).encode()).hexdigest()
  engine = TripStats(data)
  if source in engine.data['imports']:
    if engine.data['imports'][source] != digest:
      raise ValueError('Recovery source identity reused with different contents')
    return engine.data
  if any((record.get('route_id') == route or route in record.get('route_ids', [])) and record.get('origin') != 'recovered' and record['duration_s'] > 0 for record in engine.data['sessions'].values()):
    raise ValueError('Recovery route overlaps an already measured live route')
  # Do not bridge unrelated live/import sessions. Preserve live checkpoint afterwards.
  checkpoint = engine.data['checkpoint']; engine.break_interval()
  last = 0
  for item in samples:
    stamp = item.get('mono_ns')
    if not isinstance(stamp, int) or isinstance(stamp, bool) or stamp <= last:
      raise ValueError('Recovery samples must have strictly increasing monotonic timestamps')
    last = stamp
    engine.sample(boot='recovered:' + route, session=route, mono_ns=stamp, speed=item.get('speed_m_s'), wall_time=item.get('wall_time'), valid=item.get('valid') is True, real_car=item.get('real_car') is True, onroad=item.get('onroad') is True, route_id=route, origin='recovered', count_route=count_route)
  engine.data['checkpoint'] = checkpoint
  engine.data['imports'][source] = digest
  return engine.data
