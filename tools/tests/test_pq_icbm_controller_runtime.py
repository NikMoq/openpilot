"""Production VW update body + real PQ coordinator; synthetic CAN/firmware input.

Only unrelated steering/HUD packer paths are bypassed by their frame cadence.
No native imports, hardware, or ECU response model is required.
"""
import ast
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = '_pq_controller_binding_probe'
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT / 'opendbc_repo/opendbc/sunnypilot/car/volkswagen')]
sys.modules[PACKAGE] = package
pq = importlib.import_module(f'{PACKAGE}.pq_icbm_coordinator')
BUTTON = NS(none=0, increase=1, decrease=2)


def payload(counter, pressed=False):
  raw = (1 << 8) | (1 << 15) | (1 << 18) | ((counter & 15) << 20)
  raw |= (1 << 16) if pressed else 0
  data = bytearray(raw.to_bytes(4, 'little'))
  data[0] = data[1] ^ data[2] ^ data[3]
  return bytes(data)


@pytest.fixture
def bound():
  path = ROOT / 'opendbc_repo/opendbc/car/volkswagen/carcontroller.py'
  tree = ast.parse(path.read_text(encoding='utf-8'))
  cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'CarController')
  # Execute the complete production update method, preserving all branches.
  body = [node for node in cls.body if isinstance(node, ast.Assign) or
          isinstance(node, ast.FunctionDef) and node.name == 'update']
  module = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(
    name='BoundController', bases=[], keywords=[], body=body, decorator_list=[])], type_ignores=[]))
  namespace = {'StockFeedback': pq.StockFeedback,
               'structs': NS(IntelligentCruiseButtonManagement=NS(SendButtonState=BUTTON))}
  exec(compile(module, str(path), 'exec'), namespace)
  controller = namespace['BoundController']()
  controller.frame = 1
  controller.CCP = NS(STEER_STEP=10000, LDW_STEP=10000, ACC_HUD_STEP=10000, STEER_MAX=300)
  controller.CP = NS(pcmCruise=True, openpilotLongitudinalControl=False)
  controller.apply_torque_last = 0
  controller.gra_acc_counter_last = None
  controller.icbm_coordinator = pq.PQICBMCoordinator(
    transaction_timeout_s=1, feedback_max_age_s=.12, release_max_age_s=.12,
    settle_s=.08, echo_max_age_s=.5, native_bus=0, returned_bus=130)
  cc = NS(actuators=NS(as_builder=lambda: NS()), hudControl=NS(),
          cruiseControl=NS(cancel=False, resume=False))
  sp = NS(intelligentCruiseButtonManagement=NS(vTarget=79, speedUnit='kph', sendButton=BUTTON.decrease),
          icbm_status_valid=True, icbm_signature_matched=True, icbm_protocol_valid=True,
          icbm_panda_count=1, icbm_firmware_request_id=0, icbm_firmware_state=0,
          icbm_firmware_button=0, icbm_input_valid=True)
  cs = NS(gra_stock_values={'COUNTER': 1}, out=NS(
    cruiseState=NS(enabled=True, available=True), accFaulted=False,
    gasPressed=False, brakePressed=False, buttonEvents=[]), icbm_feedback_snapshot={})

  def step(now=10.002, speed=80):
    ns = round(now * 1e9)
    sp.icbm_input_timestamp_ns = ns - getattr(sp, 'test_input_age_ns', 0)
    cs.icbm_feedback_snapshot = {
      'ACC_GRA_Anzeige': {'ACA_V_Wunsch': speed, 'ACA_kmh_mph': 0, 'timestamp_nanos': ns},
      'ACC_System': {'ACS_Sta_ADR': 1, 'ACS_FreigSollB': 1, 'timestamp_nanos': ns},
      'readiness_timestamps': {name: ns for name in ('GRA_Neu', 'Motor_2', 'Motor_3', 'Motor_5')}}
    cs.gra_stock_values['COUNTER'] = (cs.gra_stock_values['COUNTER'] + 1) % 16
    return controller.update(cc, sp, cs, ns)

  # Real passive native/returned idle evidence, required before first issue.
  controller.icbm_coordinator.observe(10_000_000_000, 0x38a, payload(14), 0)
  controller.icbm_coordinator.observe(10_001_000_000, 0x38a, payload(14), 130)
  return NS(controller=controller, cc=cc, sp=sp, cs=cs, step=step)


def test_fresh_kph_emits_correlated_set_command(bound):
  bound.step()
  press = bound.controller.icbm_coordinator.transaction.pending
  assert press.button == 'SET'
  assert bound.controller.icbm_command == (press.sequence, 3)


@pytest.mark.parametrize('target', [0, 5, 15, 29])
def test_below_minimum_plan_decreases_to_native_minimum(bound, target):
  bound.sp.intelligentCruiseButtonManagement.vTarget = target
  bound.step(speed=80)
  press = bound.controller.icbm_coordinator.transaction.pending
  assert press is not None
  assert press.target == 30
  assert press.button == 'DOWN'


@pytest.mark.parametrize('target', [0, 5, 15, 29])
def test_below_minimum_plan_does_not_keep_pressing_at_native_minimum(bound, target):
  bound.sp.intelligentCruiseButtonManagement.vTarget = target
  bound.step(speed=30)
  assert bound.controller.icbm_command is None
  assert bound.controller.icbm_coordinator.transaction.pending is None


@pytest.mark.parametrize('guard', ['stale_target', 'invalid_target', 'unit', 'gas', 'brake', 'manual', 'pairing'])
def test_production_guards_inhibit_new_request(bound, guard):
  if guard == 'stale_target':
    bound.sp.test_input_age_ns = 121_000_000
  elif guard == 'invalid_target':
    bound.sp.icbm_input_valid = False
  elif guard == 'unit':
    bound.sp.intelligentCruiseButtonManagement.speedUnit = 'mph'
  elif guard in ('gas', 'brake'):
    setattr(bound.cs.out, guard + 'Pressed', True)
  elif guard == 'manual':
    bound.cs.out.buttonEvents = [NS(pressed=True)]
  else:
    bound.sp.icbm_signature_matched = False
  bound.step()
  assert bound.controller.icbm_command is None
  assert bound.controller.icbm_coordinator.transaction.pending is None


def test_target_attainment_keeps_pending_release_and_firmware_ownership(bound):
  bound.step()
  coordinator = bound.controller.icbm_coordinator
  press = coordinator.transaction.pending
  bound.sp.icbm_firmware_request_id = press.sequence
  bound.sp.icbm_firmware_state = 1
  bound.sp.icbm_firmware_button = 3
  coordinator.observe(10_020_000_000, 0x38a, payload(15), 0)
  coordinator.observe(10_021_000_000, 0x38a, payload(15, True), 130)
  bound.sp.intelligentCruiseButtonManagement.sendButton = BUTTON.none
  bound.step(10.022, speed=79)
  assert coordinator.transaction.pending is press
  assert coordinator.transaction.state == pq.TransactionState.WAIT_RELEASE
  assert coordinator.blocked_reason is None
  assert bound.controller.icbm_command is None  # firmware already owns the ID
  coordinator.observe(10_040_000_000, 0x38a, payload(0), 0)
  coordinator.observe(10_041_000_000, 0x38a, payload(0), 130)
  bound.sp.icbm_firmware_state = 3
  bound.step(10.042, speed=79)
  assert coordinator.blocked_reason is None
  assert coordinator.transaction.state == pq.TransactionState.SETTLING
  coordinator.observe(10_140_000_000, 0x38a, payload(1), 0)
  coordinator.observe(10_141_000_000, 0x38a, payload(1), 130)
  bound.step(10.142, speed=79)
  assert coordinator.transaction.pending is None
  assert coordinator.transaction.state == pq.TransactionState.IDLE
  assert bound.controller.icbm_command is None
