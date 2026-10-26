import copy
import datetime as dt
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('trip_stats', Path(__file__).resolve().parents[1] / 'trip_stats.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
NOW = dt.datetime(2026, 10, 26, 12, tzinfo=dt.timezone.utc).timestamp()


def feed(engine, n, speed=10, wall=NOW, **kwargs):
  return engine.sample(boot='boot', session='route', mono_ns=n * 1_000_000_000, speed=speed, wall_time=None if wall is None else wall + n, **kwargs)


def bundle(source='r/0/sha:one', route='route', start=1, end=3):
  return {'source_id': source, 'route_id': route, 'samples': [
    {'mono_ns': n * 1_000_000_000, 'speed_m_s': 10.0, 'wall_time': NOW + n,
     'valid': True, 'real_car': True, 'onroad': True} for n in range(start, end + 1)]}


class MeasuredTripTest(unittest.TestCase):
  def test_trapezoid_units_no_acc_dependency(self):
    e = m.TripStats(); feed(e, 1, 10); feed(e, 2, 20)
    self.assertEqual(e.snapshot(NOW + 10)['all'], {'routes': 1, 'distance_m': 15.0, 'duration_s': 1.0})

  def test_restart_checkpoint_and_duplicate(self):
    e = m.TripStats(); feed(e, 1); feed(e, 2)
    e = m.TripStats(json.loads(json.dumps(e.snapshot(NOW))))
    self.assertFalse(feed(e, 2)); feed(e, 3)
    self.assertEqual(e.snapshot(NOW)['all']['distance_m'], 20)
    self.assertEqual(e.snapshot(NOW)['all']['routes'], 1)

  def test_new_boot_cannot_bridge_monotonic_clock(self):
    e = m.TripStats(); feed(e, 1); feed(e, 2)
    e.sample(boot='newboot', session='route', mono_ns=1_000_000_000, speed=10)
    self.assertEqual(e.snapshot(NOW)['all']['distance_m'], 10)

  def test_invalid_speed_and_critical_timestamps(self):
    for bad in [float('nan'), float('inf'), -1, 101, True, '10', 10**400]:
      e = m.TripStats(); feed(e, 1); self.assertFalse(feed(e, 2, bad)); feed(e, 3)
      self.assertEqual(e.snapshot(NOW)['all']['distance_m'], 0)
    for stamp in [0, -1, True, 1.5, '1000', 2**64, 10**400]:
      e = m.TripStats();self.assertFalse(e.sample(boot='b',session='r',mono_ns=stamp,speed=10))

  def test_valid_alive_onroad_and_real_car_gate_breaks_interval(self):
    for gate in ['valid', 'onroad', 'real_car']:
      e = m.TripStats();feed(e,1);feed(e,2,**{gate: False});feed(e,3)
      self.assertEqual(e.snapshot(NOW)['all']['distance_m'],0)

  def test_gap_jump_and_out_of_order_do_not_invent_distance(self):
    e=m.TripStats();feed(e,1);feed(e,10);self.assertEqual(e.snapshot(NOW)['all']['distance_m'],0)
    feed(e,11,90);self.assertEqual(e.snapshot(NOW)['all']['distance_m'],0)
    feed(e,9,90);self.assertEqual(e.snapshot(NOW)['all']['distance_m'],0)
    feed(e,12,90);self.assertEqual(e.snapshot(NOW)['all']['distance_m'],90)

  def test_stale_future_bus_event_rejected(self):
    for received in [0, 4_000_000_000]:
      e=m.TripStats();self.assertFalse(feed(e,2,received_ns=received));self.assertIsNone(e.data['checkpoint'])

  def test_midnight_split_and_unique_route_count(self):
    midnight=dt.datetime(2026,10,26,tzinfo=dt.timezone.utc).timestamp()
    e=m.TripStats();feed(e,1,wall=midnight-1.5);feed(e,2,wall=midnight-1.5)
    days=next(iter(e.data['sessions'].values()))['days']
    self.assertAlmostEqual(days['2026-10-25']['distance_m'],5)
    self.assertAlmostEqual(days['2026-10-26']['distance_m'],5)
    self.assertEqual(e.snapshot(midnight+10)['week']['routes'],1)

  def test_rolling_seven_utc_days_excludes_old_and_future(self):
    e=m.TripStats()
    for index,day in enumerate([-7,-6,0,1]):
      for n in [1,2]:e.sample(boot='b',session=str(index),mono_ns=(index*10+n)*1_000_000_000,speed=10,wall_time=NOW+day*86400+n)
    view=e.snapshot(NOW+10)
    self.assertEqual(view['all']['distance_m'],40)
    self.assertEqual(view['week']['distance_m'],20)
    self.assertEqual(view['week']['routes'],2)

  def test_unknown_wall_time_or_jump_keeps_all_time_only(self):
    e=m.TripStats();feed(e,1,wall=None);feed(e,2,wall=None)
    self.assertEqual(e.snapshot(NOW)['all']['distance_m'],10)
    self.assertEqual(e.snapshot(NOW)['week']['distance_m'],0)
    e=m.TripStats();feed(e,1);feed(e,2,wall=NOW+86400)
    self.assertEqual(e.snapshot(NOW)['week']['distance_m'],0)
    self.assertEqual(e.snapshot(NOW)['all']['distance_m'],10)
    for invalid in [None,False,float('nan'),-1,1e100]:self.assertIsNone(m.utc_date(invalid))

  def test_corrupt_totals_checkpoint_and_dates_fail_closed(self):
    e=m.TripStats();feed(e,1);feed(e,2)
    for mutate in [lambda x:x.update(version=2),lambda x:next(iter(x['sessions'].values())).update(distance_m=-1),lambda x:x.update(checkpoint={'mono_ns':1}),lambda x:next(iter(x['sessions'].values()))['days'].update({'bad':{}})]:
      d=copy.deepcopy(e.data);mutate(d)
      with self.assertRaises(ValueError):m.TripStats(d)

  def test_recovery_idempotent_overlap_and_conflict(self):
    d=m.import_measured(m.empty_ledger(),bundle());d2=m.import_measured(d,bundle())
    relocated=bundle();relocated['provenance']={'file':'another/local/root.rlog'}
    self.assertEqual(d,m.import_measured(d,relocated))
    self.assertEqual(d,d2);self.assertEqual(m.TripStats(d).snapshot(NOW+20)['all']['distance_m'],20)
    overlap=m.import_measured(d,bundle('r/1/sha:two',start=2,end=4))
    self.assertEqual(m.TripStats(overlap).snapshot(NOW+20)['all']['distance_m'],20)
    with self.assertRaises(ValueError):m.import_measured(d,bundle(end=4))
    bad=bundle();bad['samples'].reverse()
    with self.assertRaises(ValueError):m.import_measured(m.empty_ledger(),bad)

  def test_real_car_metadata_retries_without_new_session(self):
    calls=[]
    def read():
      calls.append(1)
      return len(calls)>1
    gate=m.RealCarGate(read)
    self.assertFalse(gate.check(0,True));self.assertFalse(gate.check(.5,True))
    self.assertTrue(gate.check(1,True));self.assertTrue(gate.check(2,True))
    self.assertEqual(len(calls),2)
    self.assertFalse(gate.check(3,False));self.assertTrue(gate.check(4,True))
    self.assertEqual(len(calls),3)

  def test_known_live_route_cannot_be_imported_twice(self):
    e=m.TripStats()
    for n in [1,2]:e.sample(boot='liveboot',session='ignition',mono_ns=n*1_000_000_000,speed=10,route_id='route')
    with self.assertRaises(ValueError):m.import_measured(e.data,bundle())
    # CurrentRoute is metadata: its later change never interrupts measured integration.
    e.sample(boot='liveboot',session='ignition',mono_ns=3_000_000_000,speed=10,route_id='another-route')
    self.assertEqual(e.snapshot(NOW)['all']['distance_m'],20)
    with self.assertRaises(ValueError):m.import_measured(e.data,bundle(route='another-route'))

  def test_recovered_recordings_do_not_invent_trip_counts(self):
    data=m.import_measured(m.empty_ledger(),bundle())
    view=m.TripStats(data).snapshot(NOW+10)
    self.assertEqual(view['all']['distance_m'],20)
    self.assertEqual(view['all']['routes'],0)
    self.assertTrue(view['coverage']['routes_incomplete'])
    self.assertTrue(view['coverage']['week_routes_incomplete'])
    later=m.TripStats(data).snapshot(NOW+30*86400)
    self.assertTrue(later['coverage']['routes_incomplete'])
    self.assertFalse(later['coverage']['week_routes_incomplete'])
    known=bundle();known.update(known_route=True,count_route=True)
    view=m.TripStats(m.import_measured(m.empty_ledger(),known)).snapshot(NOW+10)
    self.assertEqual(view['all']['routes'],1)
    self.assertFalse(view['coverage']['routes_incomplete'])

  def test_no_interval_from_only_one_observation(self):
    e=m.TripStats();feed(e,1)
    self.assertEqual(e.snapshot(NOW)['all']['routes'],0)


@unittest.skipUnless(__import__('os').name == 'posix','POSIX durability/flock tested on Linux')
class DurableTest(unittest.TestCase):
  def test_atomic_restart_and_single_writer(self):
    with tempfile.TemporaryDirectory() as tmp:
      s=m.DurableStore(tmp);e=m.TripStats(s.load());feed(e,1);feed(e,2);s.save(e.snapshot(NOW))
      with self.assertRaises(RuntimeError):m.DurableStore(tmp)
      s.close();s=m.DurableStore(tmp);self.assertEqual(m.TripStats(s.load()).snapshot(NOW)['all']['distance_m'],10);s.close()

  def test_corrupt_file_preserved(self):
    with tempfile.TemporaryDirectory() as tmp:
      p=Path(tmp)/'stats.json';p.write_bytes(b'{broken original totals')
      s=m.DurableStore(tmp)
      with self.assertRaises(ValueError):s.load()
      self.assertEqual(p.read_bytes(),b'{broken original totals');s.close()

  def test_failed_replace_preserves_prior_ledger(self):
    with tempfile.TemporaryDirectory() as tmp:
      s=m.DurableStore(tmp);s.save(m.TripStats().snapshot(NOW));prior=s.path.read_bytes()
      with patch.object(m.os,'replace',side_effect=OSError('disk failure')):
        with self.assertRaises(OSError):s.save(m.TripStats().snapshot(NOW))
      self.assertEqual(s.path.read_bytes(),prior);self.assertEqual(list(Path(tmp).glob('.stats-*')),[]);s.close()


if __name__ == '__main__':unittest.main()
