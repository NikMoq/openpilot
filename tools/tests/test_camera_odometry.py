"""Actual CAN distance accounting has no GPS/new-camera/timeout authority."""
import sys
from pathlib import Path
from types import ModuleType,SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[2]
for name,relative in [('openpilot','.'),('openpilot.sunnypilot','sunnypilot'),('openpilot.sunnypilot.mapd','sunnypilot/mapd')]:
  if name not in sys.modules:
    module = ModuleType(name)
    module.__path__ = [str(ROOT/relative)]
    sys.modules[name] = module

from openpilot.sunnypilot.mapd.camera_odometry import CameraOdometry,car_speed_sample


def test_stop_and_actual_speed_trapezoids_not_wall_time():
  tracker = CameraOdometry()
  tracker.anchor('camera',25.,(10.,0.))
  assert tracker.advance('camera',(11.,0.)) == 25.
  assert tracker.advance('camera',(12.,10.)) == 20.
  assert tracker.advance('camera',(13.,10.)) == 10.
  assert tracker.advance('camera',(14.,10.)) == 0.
  assert tracker.advance('new-camera',(15.,10.)) is None


@pytest.mark.parametrize('bad',[None,(11.,float('nan')),(11.,-1.),(11.,1000.)])
def test_unknown_sample_gap_never_spends_unobserved_distance(bad):
  tracker = CameraOdometry()
  tracker.anchor('camera',100.,(10.,10.))
  assert tracker.advance('camera',bad) is None
  assert tracker.advance('camera',(12.,10.)) is None
  assert tracker.advance('camera',(13.,10.)) == 90.


def test_duplicate_reverse_and_long_clock_gap_do_not_bridge_distance():
  tracker = CameraOdometry()
  tracker.anchor('camera',100.,(10.,10.))
  assert tracker.advance('camera',(10.,10.)) is None
  assert tracker.advance('camera',(9.,10.)) is None
  assert tracker.advance('camera',(20.,10.)) is None
  assert tracker.advance('camera',(21.,10.)) == 90.


@pytest.mark.parametrize('field,value',[
  ('canValid',False),('gearShifter','reverse'),('vEgo',-1.),('vEgo',1000.),('vEgo',float('nan')),('stamp',9.7),('stamp',10.1),('alive',False),('valid',False)])
def test_car_sample_requires_fresh_valid_forward_can(field,value):
  class SM(dict):
    pass
  sm = SM(carState=NS(canValid=True,gearShifter='drive',vEgo=10.))
  sm.valid,sm.alive,sm.logMonoTime = {'carState':True},{'carState':True},{'carState':10_000_000_000}
  assert car_speed_sample(sm,10.) == (10.,10.)
  if field == 'stamp':
    sm.logMonoTime['carState'] = value*1e9
  elif field in ('alive','valid'):
    getattr(sm,field)['carState'] = value
  else:
    setattr(sm['carState'],field,value)
  assert car_speed_sample(sm,10.) is None
