"""Characterize a pure selector against an explicit hypothetical ECU model.

No controller, adapter, lease, CAN, or physical ECU is activated. Successful
tests establish the stated integer-kph model contract, not physical response
or an integrated fix for continued seven-frame leases after feedback.
"""
import importlib.util
import math
from pathlib import Path

import pytest


# Load the actual isolated module without importing vehicle/runtime dependencies.
_SOURCE = (Path(__file__).resolve().parents[2] /
           "opendbc_repo/opendbc/sunnypilot/car/volkswagen/pq_button_selection.py")
_SPEC = importlib.util.spec_from_file_location("pq_button_selection_under_test", _SOURCE)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
select_pq_button = _MODULE.select_pq_button


def modeled_response(current, action):
  """Explicit supported ECU model; not a measured response to generated CAN."""
  if action == "RES":
    return current + 1
  if action == "SET":
    return current - 1
  if action == "UP":
    return 10 * math.floor(current / 10) + 10
  if action == "DOWN":
    return 10 * math.ceil(current / 10) - 10
  raise AssertionError(f"unknown action {action!r}")


@pytest.mark.parametrize(("current", "target", "expected"), [
  (80, 79, "SET"), (80, 70, "DOWN"), (56, 60, "UP"), (79, 80, "UP"),
  (80, 81, "RES"), (80, 89, "RES"), (80, 90, "UP"),
  (81, 80, "DOWN"), (79, 78, "SET"), (56, 59, "RES"),
  (56, 50, "DOWN"), (56, 51, "SET"), (80, 75, "SET"),
  (1, 2, "RES"), (2, 1, "SET"), (9, 10, "UP"), (10, 9, "SET"),
  (11, 10, "DOWN"), (249, 250, "UP"), (250, 249, "SET"),
  (1, 1, None), (80, 80, None), (250, 250, None),
])
def test_boundary_examples(current, target, expected):
  assert select_pq_button(target, current, "kph") == expected


@pytest.mark.parametrize("unit", ["mph", "unknown", "", "KPH", "km/h", None, 0, True])
def test_unsupported_units_do_not_guess(unit):
  assert select_pq_button(79, 80, unit) is None
  assert select_pq_button(130, 80, unit) is None


@pytest.mark.parametrize("invalid", [
  math.nan, math.inf, -math.inf, 79.5, 0, -1, 251, 254, 255, 256,
  10**1000, -(10**1000), "80", "nan", None, True, False, [], {}, complex(80),
])
@pytest.mark.parametrize("position", ["target", "current"])
def test_invalid_speeds_produce_no_action(invalid, position):
  target, current = (invalid, 80) if position == "target" else (79, invalid)
  assert select_pq_button(target, current, "kph") is None


def test_integral_transport_floats_preserve_selection():
  assert select_pq_button(79.0, 80.0, "kph") == "SET"
  assert select_pq_button(60.0, 56.0, "kph") == "UP"
  assert select_pq_button(80.0, 79.0, "kph") == "UP"
  assert select_pq_button(80.0, 80.0, "kph") is None


def test_complete_80_to_79_then_restore_130():
  current, speeds, actions = 80, [80], []
  for target in (79, 130):
    while current != target:
      action = select_pq_button(target, current, "kph")
      current = modeled_response(current, action)
      speeds.append(current)
      actions.append(action)
      assert len(actions) <= 250
    assert select_pq_button(target, current, "kph") is None
  assert speeds == [80, 79, 80, 90, 100, 110, 120, 130]
  assert actions == ["SET"] + ["UP"] * 6


def test_every_integer_pair_is_monotonic_and_cannot_overshoot_in_supported_model():
  # All 62,500 pairs include every decade boundary and both range endpoints.
  for current in range(1, 251):
    for target in range(1, 251):
      action = select_pq_button(target, current, "kph")
      if target == current:
        assert action is None
        continue
      next_speed = modeled_response(current, action)
      assert min(current, target) <= next_speed <= max(current, target), (current, target, action, next_speed)
      assert abs(target - next_speed) < abs(target - current), (current, target, action, next_speed)
      assert 1 <= next_speed <= 250


def test_every_integer_pair_converges_in_supported_model():
  for start in range(1, 251):
    for target in range(1, 251):
      current = start
      # Each action must reduce integer error by at least one.
      for _ in range(abs(target - start)):
        if current == target:
          break
        current = modeled_response(current, select_pq_button(target, current, "kph"))
      assert current == target, (start, target, current)
      assert select_pq_button(target, current, "kph") is None
