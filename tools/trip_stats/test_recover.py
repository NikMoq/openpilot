"""Stdlib-only tests using fake log events, never a device/network/production ledger."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

SPEC = importlib.util.spec_from_file_location('trip_recover', Path(__file__).with_name('recover.py'))
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)


def event(kind, mono, valid=True, **fields):
  value = SimpleNamespace(logMonoTime=int(mono * 1e9), valid=valid)
  setattr(value, kind, SimpleNamespace(**fields))
  value.which = lambda: kind
  return value


def measured():
  return [event('initData', 10, wallTimeNanos=1_700_000_000_000_000_000),
          event('carParams', 10, notCar=False, brand='volkswagen', carFingerprint='VW_TEST'),
          event('deviceState', 10, started=True), event('carState', 10, vEgo=10),
          event('carState', 11, vEgo=12)]


class RecoveryTest(unittest.TestCase):
  def test_measured_mapping_and_unknown_evidence(self):
    samples, real, metadata = recovery.normalized_samples(measured())
    self.assertTrue(real)
    self.assertEqual(samples[1], {'mono_ns': 11_000_000_000, 'speed_m_s': 12.0, 'valid': True,
                                 'real_car': True, 'onroad': True, 'wall_time': 1_700_000_001.0})
    self.assertFalse(metadata['clock_discontinuity'])
    unknown, real, _ = recovery.normalized_samples([event('carState', 1, vEgo=10)])
    self.assertIsNone(real)
    self.assertFalse(unknown[0]['real_car'])
    self.assertFalse(unknown[0]['onroad'])
    self.assertIsNone(unknown[0]['wall_time'])

  def test_invalid_speed_mock_and_stale_started(self):
    events = measured() + [event('carState', 13, vEgo=float('nan')), event('carState', 14, vEgo=-1),
                           event('carParams', 14, notCar=False, brand='mock', carFingerprint='SIM'),
                           event('carParams', 14, notCar=False, brand='volkswagen', carFingerprint='VW_TEST')]
    samples, real, _ = recovery.normalized_samples(events)
    self.assertFalse(real)  # Later valid-looking parameters cannot rehabilitate a mixed mock route.
    self.assertFalse(samples[-1]['valid'])
    self.assertEqual(samples[-1]['speed_m_s'], 0)
    self.assertFalse(samples[-1]['onroad'])
    self.assertTrue(all(not sample['real_car'] for sample in samples))

  def test_clock_jump_duplicate_and_reset(self):
    events = measured() + [event('carState', 11, vEgo=12),
                           event('clocks', 12, wallTimeNanos=1_800_000_000_000_000_000)]
    samples, _, metadata = recovery.normalized_samples(events)
    self.assertEqual(len(samples), 2)
    self.assertEqual(metadata['duplicate_samples'], 1)
    self.assertTrue(metadata['clock_discontinuity'])
    self.assertTrue(all(s['wall_time'] is None for s in samples))
    with self.assertRaisesRegex(ValueError, 'monotonic reset'):
      recovery.normalized_samples(measured() + [event('carState', 1, vEgo=1)])

  def test_canonical_identity_and_rlog_qlog_dedup(self):
    with tempfile.TemporaryDirectory() as folder:
      root = Path(folder)
      paths = []
      for name in ['internal/route--0/rlog.zst', 'external/route--0/rlog.zst', 'internal/route--0/qlog.bz2']:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'local-log-copy')
        paths.append(p)
      calls = []
      def reader(p):
        calls.append(p)
        return measured()
      a, errors = recovery.recover([root], reader)
      b, _ = recovery.recover(list(reversed(paths)), reader)
      self.assertFalse(errors)
      self.assertEqual(len(a), 1)
      self.assertEqual(a[0]['source_kind'], 'rlog')
      self.assertEqual(a[0]['source_id'], b[0]['source_id'])
      self.assertEqual(a[0]['provenance']['alternative_files_omitted'], 2)
      self.assertEqual(len(calls), 2)
      self.assertEqual(recovery.identity(root / 'route' / '0' / 'qlog'), ('route', 0))

  def test_corrupt_rlog_fallback_and_source_mutation(self):
    with tempfile.TemporaryDirectory() as folder:
      root = Path(folder)
      route = root / 'route--0'
      route.mkdir()
      (route / 'rlog').write_bytes(b'rlog')
      (route / 'qlog').write_bytes(b'qlog')
      def fallback(path):
        if path.name == 'rlog':
          raise ValueError('corrupt compressed input')
        return measured()
      bundles, errors = recovery.recover([root], fallback)
      self.assertFalse(errors)
      self.assertEqual(bundles[0]['source_kind'], 'qlog')
      def mutate(path):
        path.write_bytes(b'changed')
        return measured()
      bundles, errors = recovery.recover([root], mutate)
      self.assertFalse(bundles)
      self.assertEqual(len(errors), 2)

  def test_local_only_symlink_and_atomic_no_overwrite_export(self):
    for path in ['https://example.test/log', '//host/share/log']:
      with self.assertRaises(ValueError):
        recovery.discover([path])
    with tempfile.TemporaryDirectory() as folder:
      output = Path(folder) / 'measured.jsonl'
      recovery.export_new(output, [{'samples': [], 'test': 'measured only'}])
      original = output.read_bytes()
      with self.assertRaises(FileExistsError):
        recovery.export_new(output, [])
      self.assertEqual(output.read_bytes(), original)
      self.assertEqual(json.loads(original)['test'], 'measured only')
      self.assertFalse(list(Path(folder).glob('.trip-recovery-*')))
      link = Path(folder) / 'link'
      try:
        link.symlink_to(output)
      except OSError:
        return  # Windows may disable symlink creation; other guards still exercised.
      with self.assertRaises(ValueError):
        recovery.discover([link])

  def test_binary_hash_and_numbered_recording_metadata(self):
    with tempfile.TemporaryDirectory() as folder:
      root = Path(folder) / 'recording-session'
      root.mkdir()
      path = root / '0000.rlog'
      raw = b'bytes\r\nthat\r\nWindows must not translate\x1a'
      path.write_bytes(raw)
      import hashlib
      self.assertEqual(recovery.fingerprint(path)[0], hashlib.sha256(raw).hexdigest())
      metadata = {'format': 'uncompressed concatenated cereal.log.Event',
                  'started_utc': '2023-11-14T22:13:20+00:00', 'started_monotonic_ns': 10_000_000_000}
      (root / 'metadata.json').write_text(json.dumps(metadata), encoding='utf-8')
      bundles, errors = recovery.recover([root], lambda p: measured())
      self.assertFalse(errors)
      self.assertFalse(bundles[0]['known_route'])
      self.assertFalse(bundles[0]['count_route'])
      self.assertTrue(bundles[0]['provenance']['routes_incomplete'])
      self.assertFalse(bundles[0]['provenance']['file'].startswith(str(Path(folder))))


if __name__ == '__main__':
  unittest.main()
