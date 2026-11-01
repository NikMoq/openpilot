from types import SimpleNamespace
from unittest.mock import patch

import pytest

from opendbc.car import CanData
from opendbc.car.interfaces import CarInterfaceBase
from opendbc.car.volkswagen.interface import CarInterface
from opendbc.car.volkswagen.values import VolkswagenFlags
from opendbc.sunnypilot.car.volkswagen.pq_button_echo import EchoState, PQButtonEchoObserver


ADDRESS = PQButtonEchoObserver.ADDRESS
CONFIG = (1 << 8) | (1 << 15) | (1 << 18) | (3 << 29)
UP_BIT = PQButtonEchoObserver.BUTTON_BITS["UP"]


def _payload(counter, buttons=0):
  raw = CONFIG | ((counter & 0xF) << 20) | buttons
  data = bytearray(raw.to_bytes(4, "little"))
  data[0] = data[1] ^ data[2] ^ data[3]
  return bytes(data)


def _records():
  return [
    (10, [(ADDRESS, _payload(14), 0)]),
    (10, [CanData(ADDRESS, _payload(14), 130)]),
    (11, [CanData(ADDRESS, _payload(15), 0)]),
    (11, [CanData(ADDRESS, _payload(15, UP_BIT), 130)]),
    (12, [CanData(ADDRESS, _payload(0), 0)]),
    (12, [CanData(ADDRESS, _payload(0, UP_BIT), 130)]),
    (13, [CanData(ADDRESS, _payload(1), 0)]),
    (13, [CanData(ADDRESS, _payload(1), 130)]),
  ]


def _interface(flags, observer_marker=...):
  interface = CarInterface.__new__(CarInterface)
  interface.CP = SimpleNamespace(flags=flags)
  interface.CS = SimpleNamespace()
  interface.CC = SimpleNamespace()
  if observer_marker is not ...:
    interface.CC.icbm_coordinator = observer_marker
  return interface


def test_pq_interface_feeds_real_observer_in_order_and_returns_base_outputs_unchanged():
  observer = PQButtonEchoObserver(max_age=100, native_bus=0, returned_bus=130)
  observer.arm("UP", 10)
  interface = _interface(VolkswagenFlags.PQ, observer)
  records = _records()
  original = [(timestamp, list(batch)) for timestamp, batch in records]
  base_result = SimpleNamespace(canValid=False, canTimeout=True)
  calls = []

  def base_update(_self, can_packets):
    calls.append(can_packets)
    return base_result

  with patch.object(CarInterfaceBase, "update", base_update):
    result = interface.update(records)

  assert calls == [records]
  assert calls[0] is records
  assert records == original
  assert result is base_result
  assert (result.canValid, result.canTimeout) == (False, True)
  assert interface.CS.icbm_echo_snapshot == observer.snapshot()
  assert observer.snapshot().state == EchoState.RELEASED
  returned_press_times = [item.timestamp for item in observer.snapshot().provenance
                          if item.kind == "returned_pressed_txok"]
  assert returned_press_times == [11, 12]
  assert observer.snapshot().release_return_timestamp == 13
  kinds = [item.kind for item in observer.snapshot().provenance]
  assert kinds.index("returned_pressed_txok") < kinds.index("returned_native_release_txok")


def test_non_pq_interface_does_not_feed_or_publish_observer():
  observer = PQButtonEchoObserver(max_age=100)
  observer.arm("UP", 10)
  interface = _interface(0, observer)
  base_result = SimpleNamespace(canValid=True, canTimeout=False)
  calls = []

  def base_update(_self, can_packets):
    calls.append(can_packets)
    return base_result

  records = _records()
  with patch.object(CarInterfaceBase, "update", base_update):
    result = interface.update(records)

  assert calls == [records]
  assert result is base_result
  assert observer.snapshot().state == EchoState.WAIT_NATIVE_IDLE
  assert not hasattr(interface.CS, "icbm_echo_snapshot")


@pytest.mark.parametrize("observer_marker", [None, ...])
def test_pq_without_observer_calls_base_once_and_adds_nothing(observer_marker):
  interface = _interface(VolkswagenFlags.PQ, observer_marker)
  base_result = SimpleNamespace(canValid=True, canTimeout=False)
  calls = []

  def base_update(_self, can_packets):
    calls.append(can_packets)
    return base_result

  records = _records()
  with patch.object(CarInterfaceBase, "update", base_update):
    result = interface.update(records)

  assert calls == [records]
  assert result is base_result
  assert not hasattr(interface.CS, "icbm_echo_snapshot")
