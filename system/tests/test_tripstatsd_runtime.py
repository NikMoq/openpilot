"""Actual tripstatsd.run wiring, synthetic messages only; no IPC publishers or device."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = None


def schemas():
  global SCHEMAS
  if SCHEMAS is None:
    try:
      import capnp
    except ImportError as exc:
      raise unittest.SkipTest('Real pycapnp unavailable; runtime parser test requires pycapnp') from exc
    temporary = tempfile.TemporaryDirectory(prefix='tripstats-runtime-schema-')
    target = Path(temporary.name)
    (target / 'include').mkdir()
    try:
      for source in (ROOT / 'cereal').glob('*.capnp'):
        text = source.read_text(encoding='utf8').strip()
        resolved = (source.parent / text).resolve() if len(text.splitlines()) == 1 and text.endswith('.capnp') else source
        shutil.copyfile(resolved, target / source.name)
      shutil.copyfile(ROOT / 'cereal/include/c++.capnp', target / 'include/c++.capnp')
      parser = capnp.SchemaParser()
      car = parser.load(str(target / 'car.capnp'))
      log = parser.load(str(target / 'log.capnp'))
    except Exception:
      temporary.cleanup()
      raise
    SCHEMAS = (car, log, temporary, parser)
  return SCHEMAS[:2]


def load_file(name, path):
  spec = importlib.util.spec_from_file_location(name, path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


STATS = load_file('runtime_trip_stats', ROOT / 'system/trip_stats.py')


def injected_modules(params_class, messaging=None):
  cereal = types.ModuleType('cereal')
  cereal.car = schemas()[0]
  cereal.messaging = messaging or types.SimpleNamespace()
  params = types.ModuleType('openpilot.common.params')
  params.Params = params_class
  return {'cereal': cereal, 'openpilot': types.ModuleType('openpilot'),
          'openpilot.system': types.ModuleType('openpilot.system'),
          'openpilot.system.trip_stats': STATS, 'openpilot.common': types.ModuleType('openpilot.common'),
          'openpilot.common.params': params}


def car_bytes(brand='volkswagen', fingerprint='VOLKSWAGEN_GOLF_MK7', not_car=False):
  car, _ = schemas()
  cp = car.CarParams.new_message()
  cp.brand = brand
  cp.carFingerprint = fingerprint
  cp.notCar = not_car
  return cp.to_bytes()


class MetadataTest(unittest.TestCase):
  def test_actual_car_unpatched_real_serialized_schema(self):
    class Params:
      raw = None
      def get(self, key, **kwargs):
        self.assert_key = key
        return self.raw
    with patch.dict(sys.modules, injected_modules(Params)):
      daemon = load_file('metadata_tripstatsd', ROOT / 'system/tripstatsd.py')
      for raw, expected in [(car_bytes(), True), (car_bytes('mock'), False),
                            (car_bytes('body'), False), (car_bytes(not_car=True), False),
                            (car_bytes(fingerprint=''), False), (None, False), (b'broken', False)]:
        with self.subTest(expected=expected, raw_bytes=len(raw or b'')):
          params = Params(); params.raw = raw
          self.assertIs(daemon.actual_car(params), expected)
          self.assertEqual(params.assert_key, 'CarParams')

  def test_unknown_current_route_never_blocks_measurement(self):
    class Params:
      def get(self, key, **kwargs):
        raise KeyError('Existing optional key unavailable')
    with patch.dict(sys.modules, injected_modules(Params)):
      daemon = load_file('optional_tripstatsd', ROOT / 'system/tripstatsd.py')
      self.assertIsNone(daemon.current_route(Params()))


@unittest.skipUnless(os.name == 'posix', 'Actual durable runtime requires POSIX flock/CLOCK_BOOTTIME; run on Linux')
class RuntimeTest(unittest.TestCase):
  def run_daemon(self, directory, rows, metadata=None):
    """Run production loop to its captured SIGTERM handler with real capnp readers."""
    car, log = schemas()
    handlers = {}
    state = {'now': 0.0}
    writes = []
    if metadata is None:
      metadata = [car_bytes()]
    class Params:
      reads = 0
      def get(self, key, encoding=None):
        if key == 'CarParams':
          result = metadata[min(Params.reads, len(metadata) - 1)]
          Params.reads += 1
          return result
        if key == 'CurrentRoute':
          return 'synthetic-route'
        raise AssertionError('Unexpected Params read: ' + key)
    class FakeSM:
      def __init__(self, services, poll):
        if services != ['carState', 'deviceState'] or poll != 'carState':
          raise AssertionError('Unexpected subscriber services')
        self.index = 0
        self.valid = {}; self.alive = {}; self.updated = {}; self.logMonoTime = {}
        self.events = {}
      def update(self, timeout):
        if timeout != 1000 or self.index >= len(rows):
          raise AssertionError('Runtime did not stop within finite synthetic input')
        row = rows[self.index]; self.index += 1
        state['now'] = row.get('received', row['t'])
        for name in ['carState', 'deviceState']:
          event = log.Event.new_message()
          event.logMonoTime = int(row['t'] * 1e9)
          event.valid = row.get('valid', True) and name != row.get('invalid_service')
          body = event.init(name)
          if name == 'carState':
            body.vEgo = row.get('speed', 10.0)
          else:
            body.started = row.get('onroad', True)
            body.startedMonoTime = 500_000_000
          # Use an actual capnp Event reader, rather than a substitute message struct.
          self.events[name] = event.as_reader()
          self.valid[name] = bool(event.valid)
          self.alive[name] = row.get('alive', True) and name != row.get('dead_service')
          self.updated[name] = row.get('updated', True) if name == 'carState' else True
          self.logMonoTime[name] = event.logMonoTime
        if self.index == len(rows):
          handlers[signal.SIGTERM](signal.SIGTERM, None)
      def __getitem__(self, name):
        return getattr(self.events[name], name)
    class ObservedStore(STATS.DurableStore):
      def save(self, data):
        super().save(data)
        writes.append(json.loads(self.path.read_text()))
    modules = injected_modules(Params, types.SimpleNamespace(SubMaster=FakeSM))
    with patch.dict(sys.modules, modules):
      daemon = load_file('runtime_tripstatsd', ROOT / 'system/tripstatsd.py')
      def boot_clock(clock):
        self.assertEqual(clock, daemon.time.CLOCK_BOOTTIME)
        return int(state['now'] * 1e9)
      real_actual_car = daemon.actual_car
      self.assertEqual(real_actual_car.__module__, 'runtime_tripstatsd')
      with patch.object(daemon, 'DurableStore', ObservedStore), \
           patch.object(daemon.signal, 'signal', side_effect=lambda sig, handler: handlers.__setitem__(sig, handler)), \
           patch.object(daemon.time, 'monotonic', side_effect=lambda: state['now']), \
           patch.object(daemon.time, 'clock_gettime_ns', side_effect=boot_clock), \
           patch.object(daemon.time, 'time', side_effect=lambda: 1793016000 + state['now']):
        daemon.run(directory)
    # Reopen the actual fsync/replace ledger after the producer has released its lock.
    store = STATS.DurableStore(directory)
    try:
      data = store.load()
    finally:
      store.close()
    return data, writes, Params.reads

  def test_real_runtime_checkpoint_restart_dedup_and_offroad_flush(self):
    with tempfile.TemporaryDirectory() as tmp:
      data, _, _ = self.run_daemon(tmp, [{'t': 1}, {'t': 2}, {'t': 3}])
      self.assertEqual(data['all'], {'routes': 1, 'distance_m': 20.0, 'duration_s': 2.0})
      self.assertIsNotNone(data['checkpoint'])
      data, writes, _ = self.run_daemon(tmp, [{'t': 2}, {'t': 3}, {'t': 4}, {'t': 5, 'onroad': False}])
      self.assertEqual(data['all'], {'routes': 1, 'distance_m': 30.0, 'duration_s': 3.0})
      self.assertEqual(len(writes), 2)  # Confirmed offroad, then finite SIGTERM shutdown.
      self.assertIsNone(writes[0]['checkpoint'])
      self.assertFalse(data['runtime']['active'])

  def test_real_runtime_valid_alive_updated_and_timestamp_gates(self):
    for gate in ['valid', 'alive']:
      with self.subTest(gate=gate), tempfile.TemporaryDirectory() as tmp:
        data, _, _ = self.run_daemon(tmp, [{'t': 1}, {'t': 2, gate: False}, {'t': 3}, {'t': 4}])
        self.assertEqual(data['all']['distance_m'], 10)
    for service in ['carState', 'deviceState']:
      for gate in ['invalid_service', 'dead_service']:
        with self.subTest(service=service, gate=gate), tempfile.TemporaryDirectory() as tmp:
          data, _, _ = self.run_daemon(tmp, [{'t': 1}, {'t': 2, gate: service}, {'t': 3}, {'t': 4}])
          self.assertEqual(data['all']['distance_m'], 10)
    with tempfile.TemporaryDirectory() as tmp:
      data, _, _ = self.run_daemon(tmp, [{'t': 1}, {'t': 2, 'updated': False}, {'t': 4}])
      self.assertEqual(data['all']['distance_m'], 0)  # Missing event cannot extend beyond 2s gap.
    with tempfile.TemporaryDirectory() as tmp:
      data, _, _ = self.run_daemon(tmp, [{'t': 1}, {'t': 2, 'received': 4}, {'t': 5}])
      self.assertEqual(data['all']['distance_m'], 0)  # Stale event breaks the interval.

  def test_mock_unknown_metadata_rejected_and_late_metadata_retried(self):
    for metadata in [[None], [b'broken'], [car_bytes('mock')], [car_bytes(not_car=True)]]:
      with self.subTest(metadata_length=len(metadata[0] or b'')), tempfile.TemporaryDirectory() as tmp:
        data, _, _ = self.run_daemon(tmp, [{'t': 1}, {'t': 2}, {'t': 3}], metadata)
        self.assertEqual(data['all']['distance_m'], 0)
        self.assertEqual(data['all']['routes'], 0)
    with tempfile.TemporaryDirectory() as tmp:
      data, _, reads = self.run_daemon(tmp, [{'t': 1}, {'t': 1.5}, {'t': 2}, {'t': 3}], [None, car_bytes()])
      self.assertEqual(reads, 2)
      self.assertEqual(data['all']['distance_m'], 10)
      self.assertEqual(data['all']['routes'], 1)

  def test_periodic_flush_uses_thirty_second_bound(self):
    with tempfile.TemporaryDirectory() as tmp:
      data, writes, _ = self.run_daemon(tmp, [{'t': n} for n in range(1, 32)])
      self.assertEqual(len(writes), 2)  # One periodic write at30s and final shutdown.
      self.assertEqual(writes[0]['all']['duration_s'], 29)
      self.assertEqual(data['all']['distance_m'], 300)


if __name__ == '__main__':
  unittest.main()
