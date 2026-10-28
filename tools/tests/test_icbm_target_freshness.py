"""Exercise production publishing statements with real Cereal wire messages.

The retained source remains a valid button target while upstream liveness,
validity or frequency fails. A fresh downstream envelope must carry no action.
"""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from cereal import custom, log
from opendbc.car.structs import IntelligentCruiseButtonManagement

ROOT = Path(__file__).resolve().parents[2]


def message(service):
  event = log.Event.new_message(valid=True)
  event.init(service)
  return event


class SourceState(dict):
  def __init__(self, **values):
    super().__init__(values)
    self.alive = {}
    self.valid = {}
    self.freq_ok = {}

  def all_checks(self, services):
    return all(table.get(service, True) for table in (self.alive, self.valid, self.freq_ok) for service in services)


class Publisher:
  def send(self, service, event):
    self.service = service
    # Force a serialization boundary rather than retaining an alias to source.
    self.data = event.to_bytes()


def production_method(path, method, namespace, sp_only=False):
  tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
  node = next(n for cls in tree.body if isinstance(cls, ast.ClassDef)
              for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == method)
  node.decorator_list = []
  for arg in node.args.args:
    arg.annotation = None
  node.returns = None
  if sp_only:
    start = next(i for i, n in enumerate(node.body) if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'ss_sp_msg' for t in n.targets))
    end = next(i for i in range(start, len(node.body)) if isinstance(node.body[i], ast.Expr)
               and isinstance(node.body[i].value, ast.Call)
               and any(isinstance(a, ast.Constant) and a.value == 'selfdriveStateSP'
                       for a in node.body[i].value.args))
    node.body = node.body[start:end + 1]
  exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(ROOT / path), 'exec'), namespace)
  return namespace[method]


def publish_state(sm, can_valid=True):
  publisher = Publisher()
  context = NS(sm=sm, pm=publisher,
               mads=NS(state_machine=NS(state='disabled'), enabled=False, active=False, enabled_toggle=True),
               icbm=NS(state='decreasing', cruise_button='decrease', v_target=79., is_metric=True))
  publish = production_method('selfdrive/selfdrived/selfdrived.py', 'publish_selfdriveState', {
    'messaging': NS(new_message=message), 'IntelligentCruiseButtonManagementStruct': IntelligentCruiseButtonManagement,
  }, sp_only=True)
  publish(context, NS(canValid=can_valid))
  return publisher.data


def forward_state(serialized, state_healthy=True):
  with log.Event.from_bytes(serialized) as incoming:
    control = custom.CarControlSP.new_message()
    control.mads = incoming.selfdriveStateSP.mads
    control.intelligentCruiseButtonManagement = incoming.selfdriveStateSP.intelligentCruiseButtonManagement
    publisher = Publisher()
    sm = SourceState(carState=NS(canValid=True))
    sm.alive['selfdriveStateSP'] = state_healthy
    publish = production_method('sunnypilot/selfdrive/controls/controlsd_ext.py', 'publish_ext', {
      'messaging': NS(new_message=message), 'custom': custom,
    })
    publish(control, sm, publisher)
    return publisher.data


@pytest.mark.parametrize('service', ['longitudinalPlanSP', 'carControl'])
@pytest.mark.parametrize('failed_check', ['alive', 'valid', 'freq_ok'])
def test_stale_or_invalid_upstream_target_cannot_be_restamped_fresh(service, failed_check):
  sm = SourceState()
  getattr(sm, failed_check)[service] = False
  data = forward_state(publish_state(sm))
  with log.Event.from_bytes(data) as outgoing:
    target = outgoing.carControlSP.intelligentCruiseButtonManagement
    assert outgoing.valid  # Other SP consumers retain independent semantics.
    assert target.state == 'inactive'
    assert target.sendButton == 'none'
    assert target.speedUnit == 'unknown'
    assert target.vTarget == 0
    assert outgoing.carControlSP.mads.available


def test_stalled_state_publisher_cannot_be_restamped_by_healthy_controlsd():
  data = forward_state(publish_state(SourceState()), state_healthy=False)
  with log.Event.from_bytes(data) as outgoing:
    assert outgoing.valid
    assert outgoing.carControlSP.intelligentCruiseButtonManagement.sendButton == 'none'
    assert outgoing.carControlSP.intelligentCruiseButtonManagement.speedUnit == 'unknown'
    assert outgoing.carControlSP.mads.available


def test_invalid_can_does_not_publish_action_even_with_healthy_plan():
  with log.Event.from_bytes(publish_state(SourceState(), can_valid=False)) as outgoing:
    assert outgoing.selfdriveStateSP.intelligentCruiseButtonManagement.sendButton == 'none'


def test_fresh_sources_preserve_target_through_both_publishers():
  with log.Event.from_bytes(forward_state(publish_state(SourceState()))) as outgoing:
    target = outgoing.carControlSP.intelligentCruiseButtonManagement
    assert target.state == 'decreasing'
    assert target.sendButton == 'decrease'
    assert target.vTarget == 79
    assert target.speedUnit == 'kph'
