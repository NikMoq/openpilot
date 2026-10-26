#!/usr/bin/env python3
"""Independent onroad measurement daemon. Never changes Params or vehicle control."""
import argparse
import json
import logging
from pathlib import Path
import signal
import time
import uuid

from openpilot.system.trip_stats import DurableStore, TripStats, RealCarGate, import_measured


def actual_car(params):
  # Existing metadata only. Missing/unparseable/mock metadata fails closed.
  try:
    from cereal import car
    raw = params.get('CarParams')
    if not raw:
      return False
    with car.CarParams.from_bytes(raw) as cp:
      return bool(cp.carFingerprint) and cp.brand not in ('mock', 'body', '') and not cp.notCar
  except Exception:
    return False


def current_route(params):
  try:
    route = params.get('CurrentRoute', encoding='utf8')
    return route if isinstance(route, str) and 0 < len(route) <= 512 else None
  except Exception:
    return None  # Optional existing metadata must never stop measured accumulation.


def run(directory='/data/trip_stats'):
  from cereal import messaging
  from openpilot.common.params import Params
  params = Params()
  store = DurableStore(directory)
  engine = TripStats(store.load())  # Corrupt ledger raises before any write/subscription.
  boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
  sm = messaging.SubMaster(['carState', 'deviceState'], poll='carState')
  stopping = False
  def stop(signum, frame):
    nonlocal stopping
    stopping = True
  signal.signal(signal.SIGTERM, stop)
  signal.signal(signal.SIGINT, stop)
  last_flush = time.monotonic()
  was_onroad = False
  car_gate = RealCarGate(lambda: actual_car(params))
  runtime = engine.data.get('runtime', {})
  session = runtime.get('session') if runtime.get('boot') == boot and runtime.get('active') else None
  try:
    while not stopping:
      sm.update(1000)
      valid = all(sm.valid[name] and sm.alive[name] for name in ('carState', 'deviceState'))
      confirmed_offroad = bool(sm.valid['deviceState'] and sm.alive['deviceState'] and not sm['deviceState'].started)
      became_offroad = False
      onroad = bool(valid and sm['deviceState'].started)
      if onroad and not was_onroad:
        start_ns = int(sm['deviceState'].startedMonoTime)
        session = str(start_ns) if start_ns > 0 else (session or 'fallback-' + uuid.uuid4().hex)
        engine.data['runtime'] = {'boot': boot, 'session': session, 'active': True}
        route = current_route(params)
        if route:
          engine.data['runtime']['route_id'] = route[:512]
      real_car = car_gate.check(time.monotonic(), onroad)
      if onroad and sm.updated['carState']:
        engine.sample(boot=boot, session=session, mono_ns=int(sm.logMonoTime['carState']),
                      received_ns=time.clock_gettime_ns(time.CLOCK_BOOTTIME), speed=float(sm['carState'].vEgo),
                      valid=valid, real_car=real_car, onroad=True, wall_time=time.time(),
                      route_id=engine.data.get('runtime', {}).get('route_id'))
      elif not onroad:
        engine.break_interval()
        if confirmed_offroad:
          became_offroad = bool(engine.data.get('runtime', {}).get('active'))
          engine.data['runtime'] = {'boot': boot, 'active': False}
          session = None
      now = time.monotonic()
      if now - last_flush >= 30 or became_offroad:
        store.save(engine.snapshot(time.time()))
        last_flush = now
      was_onroad = onroad
  finally:
    # Preserve checkpoint on daemon restart; subsequent stale/replayed samples are rejected.
    try:
      store.save(engine.snapshot(time.time()))
    finally:
      store.close()


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--directory', default='/data/trip_stats')
  parser.add_argument('--import-measured', type=Path, help='Explicit OFFLINE opt-in normalized measured JSON; no directory-count recovery')
  args = parser.parse_args()
  if args.import_measured:
    store = DurableStore(args.directory)
    try:
      with args.import_measured.open('rb') as source:
        raw = source.read(16 * 1024 * 1024 + 1)
      if len(raw) > 16 * 1024 * 1024:
        raise ValueError('Recovery bundle exceeds bounded size')
      data = import_measured(store.load(), json.loads(raw))
      store.save(TripStats(data).snapshot(time.time()))
    finally:
      store.close()
  else:
    run(args.directory)


if __name__ == '__main__':
  logging.basicConfig(level=logging.INFO)
  main()
