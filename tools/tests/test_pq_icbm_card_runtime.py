"""Execute Card's real control method with SubMaster's inner-struct contract."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest


def control_method():
  source = Path(__file__).resolve().parents[2] / "selfdrive/car/card.py"
  tree = ast.parse(source.read_text(encoding="utf-8"))
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Car")
  method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "controls_update")
  for arg in method.args.args:
    arg.annotation = None
  namespace = {"REPLAY": True, "convert_carControlSP": lambda x: x,
               "can_list_to_can_capnp": lambda *a, **kw: b"can"}
  exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), str(source), "exec"), namespace)
  return namespace["controls_update"]


class SM(dict):
  def all_alive(self, services):
    return self.alive

  def all_checks(self, services):
    return True


@pytest.mark.parametrize("age,valid", [(0, True), (300_000_000, True), (300_000_001, False), (-1, False)])
def test_status_inner_struct_and_freshness(age, valid):
  now = 1_000_000_000
  status = NS(signatureMatched=True, protocolValid=True, pandaCount=1, state=3,
              button=2, reason=0, requestId=19, txCounter=7, statusMonoTimeNs=800_000_000)
  sm = SM(customReserved11=status)
  sm.alive = True
  sm.valid = {"customReserved11": True}
  sm.logMonoTime = {"customReserved11": now - age, "carControlSP": now}
  ci = NS(apply=Mock(return_value=(None, [])), CC=NS(icbm_command=(20, 2)))
  card = NS(initialized_prev=True, sm=sm, can_log_mono_time=now, CI=ci,
            pm=Mock(), publish_icbm_command=Mock())
  control = NS()
  control_method()(card, NS(canValid=True), NS(), control)
  assert control.icbm_status_valid is valid
  assert control.icbm_firmware_request_id == 19
  assert control.icbm_firmware_status_mono_time_ns == 800_000_000
  ci.apply.assert_called_once()
  card.publish_icbm_command.assert_called_once_with((20, 2))


def test_dead_controls_immediately_invalidate_command():
  sm = SM()
  sm.alive = False
  card = NS(initialized_prev=True, sm=sm, publish_icbm_command=Mock())
  control_method()(card, None, None, None)
  card.publish_icbm_command.assert_called_once_with()
