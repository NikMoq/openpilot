"""Serialize the production Card command publisher with the real Cereal envelope."""
import ast
from pathlib import Path
from types import SimpleNamespace

import cereal.messaging as messaging
from cereal import log
import pytest


def publisher_method():
  source = Path(__file__).resolve().parents[2] / "selfdrive/car/card.py"
  tree = ast.parse(source.read_text(encoding="utf-8"))
  cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Car")
  method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "publish_icbm_command")
  namespace = {"messaging": messaging}
  exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
  return namespace["publish_icbm_command"]


@pytest.mark.parametrize("request_id,button", [(1, 1), (2, 2), (3, 3), (65535, 4)])
def test_production_command_envelope_passes_pandad_validity_gate(request_id, button):
  sent = []
  card = SimpleNamespace(pm=SimpleNamespace(send=lambda service, message: sent.append((service, message.to_bytes()))))
  publisher_method()(card, (request_id, button))
  assert len(sent) == 1 and sent[0][0] == "customReserved10"
  with log.Event.from_bytes(sent[0][1]) as event:
    request = event.customReserved10
    assert event.logMonoTime > 0
    assert event.valid and request.valid  # Both are required by pandad.cc.
    assert (request.requestId, request.button) == (request_id, button)


def test_no_command_clears_both_serialized_validity_flags_after_active_command():
  sent = []
  card = SimpleNamespace(pm=SimpleNamespace(send=lambda service, message: sent.append(message.to_bytes())))
  publish = publisher_method()
  publish(card, (12, 2))
  publish(card)
  with log.Event.from_bytes(sent[0]) as active:
    assert active.valid and active.customReserved10.valid
  with log.Event.from_bytes(sent[1]) as cleared:
    assert not cleared.valid and not cleared.customReserved10.valid
    assert (cleared.customReserved10.requestId, cleared.customReserved10.button) == (0, 0)
