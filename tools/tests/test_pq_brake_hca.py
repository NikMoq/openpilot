"""Stock VW steering follows CC.latActive; MADS owns the brake decision.

AST loading avoids native CAN dependencies; only packer transport is replaced.
No ECU model or claim of physical recovery is made.
"""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_function(relative, name, namespace):
  path = ROOT / relative
  tree = ast.parse(path.read_text(encoding='utf-8'))
  node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
  exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(path), 'exec'), namespace)
  return namespace[name]


def controller(pq, torque):
  namespace = {'np': np, 'DT_CTRL': .01, 'VolkswagenFlags': NS(PQ=1, STOCK_HCA_PRESENT=2)}
  namespace['apply_driver_steer_torque_limits'] = load_function(
    'opendbc_repo/opendbc/car/lateral.py', 'apply_driver_steer_torque_limits', {'np': np})
  path = ROOT / 'opendbc_repo/opendbc/car/volkswagen/carcontroller.py'
  tree = ast.parse(path.read_text(encoding='utf-8'))
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'CarController')
  update = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'update')
  module = ast.Module(body=[ast.ClassDef(name='Controller', bases=[], keywords=[], body=[update], decorator_list=[])], type_ignores=[])
  exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), namespace)
  c = namespace['Controller']()
  c.frame = 2  # Steering scheduled; unrelated HUD/ACC paths off cadence.
  c.CP = NS(flags=1 if pq else 0, pcmCruise=False, openpilotLongitudinalControl=False, alternativeExperience=0)
  c.CCP = NS(STEER_STEP=2, LDW_STEP=10000, ACC_HUD_STEP=10000, STEER_MAX=300,
             STEER_DRIVER_ALLOWANCE=80, STEER_DRIVER_FACTOR=1, STEER_DRIVER_MULTIPLIER=3,
             STEER_DELTA_UP=6, STEER_DELTA_DOWN=10, STEER_TIME_STUCK_TORQUE=6, STEER_TIME_ALERT=350)
  c.apply_torque_last = int(torque * 300)
  c.hca_frame_timer_running, c.hca_frame_same_torque = 40, 0
  c.gra_acc_counter_last, c.icbm_coordinator = None, None
  c.CAN = NS(pt=0)
  packer = NS(make_can_msg=lambda name, bus, values: (name, bus, values.copy()))
  c.packer_pt = packer
  family = 'pqcan' if pq else 'mqbcan'
  c.CCS = NS(create_steering_control=load_function(
    f'opendbc_repo/opendbc/car/volkswagen/{family}.py', 'create_steering_control', {}))
  cc = NS(latActive=True, actuators=NS(torque=torque, as_builder=lambda: NS()), hudControl=NS())
  cs = NS(out=NS(steeringTorque=0, brakePressed=False), gra_stock_values={'COUNTER': 1})
  return c, cc, cs


@pytest.mark.parametrize('torque', [.5, -.5])
@pytest.mark.parametrize('mask', [0, 1024, 3072, 5120])
def test_pq_uses_upstream_lateral_request_without_its_own_brake_override(torque, mask):
  c, cc, cs = controller(True, torque)
  c.CP.alternativeExperience = mask
  _, frames = c.update(cc, NS(), cs, 0)
  assert frames[0][2]['HCA_Status'] == 7
  assert frames[0][2]['LM_Offset'] > 0
  cs.out.brakePressed = True
  _, frames = c.update(cc, NS(), cs, 0)
  if not frames:
    _, frames = c.update(cc, NS(), cs, 0)
  assert frames[0][2]['HCA_Status'] == 7
  assert frames[0][2]['LM_Offset'] > 0
  # The configured upstream MADS state, rather than this controller,
  # supplies the inactive request for pause/disengage.
  cc.latActive = False
  for _ in range(3):
    # Execute every update, including off-cadence frames.
    output, frames = c.update(cc, NS(), cs, 0)
    assert not cc.latActive
    if frames:
      assert frames[0][2]['HCA_Status'] == 3
      assert frames[0][2]['LM_Offset'] == 0
      assert output.torqueOutputCan == c.apply_torque_last == 0
      assert c.hca_frame_timer_running == 0
  cs.out.brakePressed = False
  cc.latActive = True
  _, frames = c.update(cc, NS(), cs, 0)
  if not frames:
    _, frames = c.update(cc, NS(), cs, 0)
  assert frames[0][2]['HCA_Status'] == 7
  assert frames[0][2]['LM_Offset'] == c.CCP.STEER_DELTA_UP
  assert frames[0][2]['LM_OffSign'] == int(torque < 0)


@pytest.mark.parametrize('torque', [.5, -.5])
def test_pq_mads_remain_active_policy_keeps_steering_on_brake(torque):
  c, cc, cs = controller(True, torque)
  c.CP.alternativeExperience = 1024
  cs.out.brakePressed = True
  output, frames = c.update(cc, NS(), cs, 0)
  assert frames[0][2]['HCA_Status'] == 7
  assert frames[0][2]['LM_Offset'] > 0
  assert output.torqueOutputCan != 0


@pytest.mark.parametrize('pq', [True, False])
def test_no_brake_retains_active_output(pq):
  c, cc, cs = controller(pq, .5)
  _, frames = c.update(cc, NS(), cs, 0)
  assert frames[0][2]['LM_Offset' if pq else 'HCA_01_LM_Offset'] > 0


def test_mqb_brake_behavior_unchanged():
  c, cc, cs = controller(False, .5)
  cs.out.brakePressed = True
  _, frames = c.update(cc, NS(), cs, 0)
  assert frames[0][2]['HCA_01_Status_HCA'] == 5
  assert frames[0][2]['HCA_01_LM_Offset'] > 0
