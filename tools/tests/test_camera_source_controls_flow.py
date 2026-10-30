"""Actual OSM producer -> real wire -> resolver/SLA -> plan -> helper/ICBM.

Native IO, Params and model/SCC inputs are explicit fixture boundaries only.
"""
import time

import pytest
from test_camera_control_selection import map_pipeline, bkm_row, latitude
from test_camera_resolver_integration import control, update, real_wire_controls
from types import SimpleNamespace as NS


@pytest.mark.parametrize('offset,target', [(15, 35), (5, 25)])
def test_raw20_camera_uses_native_offset_before_stock_target_minimum(map_pipeline, control, monkeypatch, offset, target):
  from test_camera_resolver_integration import Params
  instance, _, publish = map_pipeline
  c = control
  runtime = real_wire_controls(c, monkeypatch)
  monkeypatch.setitem(Params.values, 'SpeedLimitValueOffset', offset)
  c.resolver.frame = 0
  c.sm['gps'] = instance.sm['gpsLocationExternal']
  c.sm.recv_time['liveMapDataSP'] = time.monotonic()
  lmd, _ = publish(bkm_row(18059, 250, speed=20))
  assert lmd.speedCameraControlValid and lmd.speedCameraControlSpeedLimit == pytest.approx(20/3.6)
  c.sm['liveMapDataSP'] = lmd
  runtime.cs.cruiseState.speed = runtime.cs.cruiseState.speedCluster = 100/3.6
  assert update(c) == pytest.approx(target/3.6)
  plan = runtime.publish()
  assert plan.speedCameraControl.rawLimit == pytest.approx(20/3.6)
  assert plan.speedCameraControl.appliedOffset == pytest.approx(offset/3.6)
  assert plan.speedCameraControl.speedCap == pytest.approx(target/3.6)
  cc = NS(enabled=True, cruiseControl=NS(override=False, cancel=False, resume=False))
  for _ in range(50):
    runtime.icbm.run(runtime.cs, cc, plan, True)
  assert runtime.icbm.v_target == target
  assert runtime.icbm.v_cruise_min == 30  # The existing native minimum is untouched.
  assert runtime.icbm.cruise_button == runtime.icbm_mod.SendButtonState.decrease
  runtime.cs.cruiseState.speed = runtime.cs.cruiseState.speedCluster = max(30, target)/3.6
  runtime.icbm.run(runtime.cs, cc, plan, True)
  assert runtime.icbm.cruise_button == runtime.icbm_mod.SendButtonState.none
  assert ('below_stock_acc_minimum' in plan.speedCameraControl.reason) == (target < 30)
  assert runtime.helper.v_cruise_kph == 100


def test_source_wire_controls_pass_and_new_equal_limit(map_pipeline, control, monkeypatch):
  instance, _, publish = map_pipeline
  c = control
  runtime = real_wire_controls(c, monkeypatch)
  clock = [time.time()]
  monkeypatch.setattr(time, 'time', lambda: clock[0])
  gps = instance.sm['gpsLocationExternal']
  # Simulated native map Params contains a real competing road limit; the
  # production publisher includes it, while camera mode must ignore it.
  instance.mem_params.get = lambda key: 20/3.6 if key == 'MapSpeedLimit' else None
  c.sm['gps'] = gps
  c.sm.recv_time['liveMapDataSP'] = time.monotonic()
  lmd, _ = publish(bkm_row(18059, 250) + bkm_row(18059, 500))
  first_id = lmd.speedCameraControlEventId
  assert first_id and lmd.speedCameraControlState == 'ACTIVE'
  assert lmd.speedLimitValid and lmd.speedLimit == pytest.approx(20/3.6)
  c.sm['liveMapDataSP'] = lmd
  assert update(c) == pytest.approx(75/3.6)
  plan = runtime.publish()
  resolver = plan.speedLimit.resolver
  assert plan.longitudinalPlanSource == 'speedLimitAssist'
  assert resolver.speedLimit == pytest.approx(60/3.6)
  assert resolver.speedLimitOffset == pytest.approx(15/3.6)
  assert resolver.speedLimitFinal == plan.speedCameraControl.speedCap == pytest.approx(75/3.6)
  assert plan.speedCameraControl.eventId == first_id
  assert plan.speedCameraControl.rawLimit == pytest.approx(60/3.6)
  assert plan.speedCameraControl.appliedOffset == pytest.approx(15/3.6)
  assert plan.speedCameraControl.countdownValid
  assert plan.speedCameraControl.distance == pytest.approx(250)
  assert runtime.helper.camera_episode_active
  cc = type('CC', (), dict(enabled=True, cruiseControl=type('Cruise', (), dict(override=False, cancel=False, resume=False))()))()
  for _ in range(50):
    runtime.icbm.run(c.cs, cc, plan, True)
  # Already at75 produces hold rather than a new increase.
  assert runtime.icbm.cruise_button == runtime.icbm_mod.SendButtonState.none

  # Drive the synthetic GPS only through forward connected50m-or-smaller steps.
  # Neither source state nor private tracking variables are injected.
  for meters in (50, 100, 150, 200, 240, 280, 300):
    clock[0] += 1
    gps.latitude = latitude(meters)
    gps.unixTimestampMillis = int(clock[0]*1000)
    instance.last_position.latitude = gps.latitude
    lmd, _ = publish(bkm_row(18059, 250) + bkm_row(18059, 500))
    c.sm['liveMapDataSP'] = lmd
    c.sm.recv_time['liveMapDataSP'] = time.monotonic()
    update(c)
  assert lmd.speedCameraControlPassProven
  assert lmd.speedCameraControlEventId == first_id
  plan = runtime.publish()
  assert not plan.speedLimit.resolver.speedLimitValid
  assert not plan.speedLimit.resolver.speedLimitLastValid
  assert plan.speedLimit.resolver.speedLimitFinalLast == 0
  assert plan.vTarget == pytest.approx(100/3.6)
  assert plan.speedCameraControl.state == 'passed'
  assert not plan.speedCameraControl.countdownValid
  assert plan.speedCameraControl.rawLimit == plan.speedCameraControl.appliedOffset == 0

  clock[0] += 1
  gps.latitude = latitude(310)
  gps.unixTimestampMillis = int(clock[0]*1000)
  instance.last_position.latitude = gps.latitude
  lmd, _ = publish(bkm_row(18059, 250) + bkm_row(18059, 500))
  c.sm['liveMapDataSP'] = lmd
  c.sm.recv_time['liveMapDataSP'] = time.monotonic()
  assert not lmd.speedCameraControlValid
  assert update(c) == pytest.approx(100/3.6)
  # Each camera, including an equal-limit successor, needs its own measured
  # two-fix approach. Do not treat a single new fix as confirmed selection.
  clock[0] += 1
  gps.latitude = latitude(320)
  gps.unixTimestampMillis = int(clock[0]*1000)
  instance.last_position.latitude = gps.latitude
  lmd, _ = publish(bkm_row(18059, 250) + bkm_row(18059, 500))
  c.sm['liveMapDataSP'] = lmd
  c.sm.recv_time['liveMapDataSP'] = time.monotonic()
  assert lmd.speedCameraControlValid and lmd.speedCameraControlEventId != first_id
  assert update(c) == pytest.approx(75/3.6)
  plan = runtime.publish()
  assert plan.speedCameraControl.eventId == lmd.speedCameraControlEventId
  assert plan.speedCameraControl.countdownValid
