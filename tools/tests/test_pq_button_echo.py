"""Synthetic tests for read-only PQ GRA TX-return echo observation."""
import importlib
from pathlib import Path
import sys
from types import ModuleType

import pytest


_PACKAGE = "_pq_button_echo_probe"
_NAMESPACE = ModuleType(_PACKAGE)
_NAMESPACE.__path__ = [str(Path(__file__).resolve().parents[2] /
                           "opendbc_repo/opendbc/sunnypilot/car/volkswagen")]
sys.modules[_PACKAGE] = _NAMESPACE
_MODULE = importlib.import_module(f"{_PACKAGE}.pq_button_echo")
EchoState = _MODULE.EchoState
PQButtonEchoObserver = _MODULE.PQButtonEchoObserver

ADDR = 0x38A
NATIVE = 0
RETURNED = 130
BUTTONS = {"DOWN": 1 << 10, "UP": 1 << 11, "SET": 1 << 16, "RES": 1 << 17}
CONFIG = (1 << 8) | (1 << 15) | (1 << 18) | (3 << 29)


def payload(counter, buttons=0, *, config=CONFIG, extra=0):
  raw = config | ((counter & 15) << 20) | buttons | extra
  data = bytearray(raw.to_bytes(4, "little"))
  data[0] = data[1] ^ data[2] ^ data[3]
  return bytes(data)


def send_native(observer, t, counter, **kw):
  return observer.observe(t, ADDR, payload(counter, **kw), NATIVE)


def send_return(observer, t, counter, buttons=0, **kw):
  return observer.observe(t, ADDR, payload(counter, buttons, **kw), RETURNED)


def observer(**kw):
  return PQButtonEchoObserver(**({"max_age": 100} | kw))


@pytest.mark.parametrize("button", ["DOWN", "UP", "SET", "RES"])
def test_counter_wrap_and_ordered_same_timestamp_press_then_native_release(button):
  o = observer()
  bit = BUTTONS[button]
  o.arm(button, 10)
  # Same-batch timestamps are allowed; stream order carries the event order.
  send_native(o, 10, 14)
  send_return(o, 10, 14)
  send_native(o, 10, 15)
  assert send_return(o, 10, 15, bit).state == EchoState.PRESSED
  send_native(o, 10, 0)
  assert send_return(o, 10, 0, bit).state == EchoState.PRESSED
  send_native(o, 10, 1)
  result = send_return(o, 10, 1)
  assert result.state == EchoState.RELEASED
  assert result.pressed_return_timestamp == result.release_return_timestamp == 10
  assert result.provenance[-1].kind == "returned_native_release_txok"


def test_native_idle_echo_without_a_returned_press_never_confirms_release():
  o = observer()
  o.arm("RES", 0)
  send_native(o, 0, 3)
  result = send_return(o, 0, 3)
  assert result.state == EchoState.WAIT_RETURNED_PRESS
  send_native(o, 1, 4)
  result = send_return(o, 1, 4)
  assert result.state == EchoState.WAIT_RETURNED_PRESS
  assert result.release_return_timestamp is None


def test_rejected_bus_194_never_acknowledges_a_press():
  o = observer()
  o.arm("SET", 0)
  send_native(o, 0, 6)
  result = o.observe(0, ADDR, payload(6, BUTTONS["SET"]), RETURNED + 64)
  assert result.state == EchoState.INVALID
  assert result.reason == "rejected_return"
  assert result.pressed_return_timestamp is None


@pytest.mark.parametrize("kind", ["stale", "out_of_order"])
def test_stale_or_out_of_order_records_invalidate(kind):
  o = observer(max_age=4)
  o.arm("RES", 10)
  if kind == "stale":
    result = send_native(o, 15, 0)
    assert result.reason == "stale_record"
  else:
    send_native(o, 11, 0)
    result = send_return(o, 10, 0)
    assert result.reason == "out_of_order_timestamp"
  assert result.state == EchoState.INVALID


def test_missing_pressed_echo_cannot_be_inferred_from_later_idle_frames():
  o = observer()
  o.arm("SET", 0)
  send_native(o, 0, 8)
  send_return(o, 0, 8)
  send_native(o, 1, 9)  # A press echo is absent.
  send_native(o, 2, 10)
  result = send_return(o, 2, 10)
  assert result.state == EchoState.WAIT_RETURNED_PRESS
  assert result.release_return_timestamp is None


def test_native_or_returned_counter_gap_invalidates():
  o = observer()
  o.arm("SET", 0)
  send_native(o, 0, 1)
  send_return(o, 0, 1)
  result = send_native(o, 1, 3)
  assert result.state == EchoState.INVALID and result.reason == "native_counter_gap"

  o.disarm()
  o.arm("SET", 2)
  send_native(o, 2, 1)
  send_return(o, 2, 1, BUTTONS["SET"])
  send_native(o, 3, 2)
  send_native(o, 4, 3)
  result = send_return(o, 4, 3, BUTTONS["SET"])
  assert result.state == EchoState.INVALID and result.reason == "returned_counter_gap"


def test_config_change_driver_buttons_and_bad_xor_invalidate():
  for bad in ("config", "driver", "xor"):
    o = observer()
    o.arm("RES", 0)
    if bad == "config":
      result = send_native(o, 0, 1)
      result = send_native(o, 1, 2, config=CONFIG ^ (1 << 18))
      assert result.reason == "native_config_changed"
    elif bad == "driver":
      result = send_native(o, 0, 1, extra=(1 << 10))
      assert result.reason == "native_not_idle_or_ineligible"
    else:
      data = bytearray(payload(1))
      data[0] ^= 1
      result = o.observe(0, ADDR, bytes(data), NATIVE)
      assert result.reason == "bad_dlc_or_xor"
    assert result.state == EchoState.INVALID


def test_returned_payload_must_match_corresponding_native_except_button():
  o = observer()
  o.arm("SET", 0)
  send_native(o, 0, 5)
  wrong = bytearray(payload(5, BUTTONS["SET"]))
  wrong[3] ^= 1
  wrong[0] = wrong[1] ^ wrong[2] ^ wrong[3]
  result = o.observe(0, ADDR, bytes(wrong), RETURNED)
  assert result.state == EchoState.INVALID
  assert result.reason == "returned_without_matching_native"


def test_native_dlc_must_be_four_and_provenance_is_bounded():
  o = observer(history_size=4)
  o.arm("RES", 0)
  result = o.observe(0, ADDR, b"\0\1\2", NATIVE)
  assert result.state == EchoState.INVALID and result.reason == "bad_dlc_or_xor"

  o.disarm()
  o.arm("RES", 0)
  for i in range(20):
    counter = i & 15
    send_native(o, i, counter)
    send_return(o, i, counter)
  assert len(o.snapshot().provenance) <= 4


def test_unrelated_addresses_and_sources_do_not_acknowledge():
  o = observer()
  o.arm("SET", 10)
  send_native(o, 9, 1)
  result = o.observe(0, 0x123, payload(1, BUTTONS["SET"]), RETURNED)
  assert result.state == EchoState.WAIT_NATIVE_IDLE
  # Older unrelated traffic is ignored before observer timestamp ownership.
  result = o.observe(-100, 0x123, b"bad", RETURNED)
  assert result.state == EchoState.WAIT_NATIVE_IDLE and result.reason is None


def test_stale_idle_echo_before_owned_press_is_ignored_then_full_cycle_succeeds():
  o = observer()
  o.arm("SET", 10)
  # Delayed native/TX records from before arm do not establish ownership.
  send_native(o, 9, 15)
  send_return(o, 9, 15)
  assert o.snapshot().state == EchoState.WAIT_NATIVE_IDLE
  # A neutral echo delivered after arm but lacking a post-arm native match is
  # also ignored; it cannot seed sequence state or imply release.
  assert send_return(o, 10, 15).state == EchoState.WAIT_NATIVE_IDLE
  send_native(o, 10, 0)
  assert send_return(o, 10, 0).state == EchoState.WAIT_RETURNED_PRESS
  send_native(o, 11, 1)
  assert send_return(o, 11, 1, BUTTONS["SET"]).state == EchoState.PRESSED
  send_native(o, 12, 2)
  assert send_return(o, 12, 2).state == EchoState.RELEASED


def test_disarmed_idle_pair_bootstraps_baseline_but_arm_clears_it():
  o = observer()
  send_native(o, 1, 4)
  baseline = send_return(o, 1, 4)
  assert baseline.state == EchoState.DISARMED
  assert baseline.last_idle_return_timestamp == 1
  assert baseline.release_return_timestamp is None
  assert baseline.pressed_return_timestamp is None

  armed = o.arm("SET", 2)
  assert armed.last_idle_return_timestamp is None
  send_native(o, 2, 5)
  send_return(o, 2, 5)  # neutral TX echo is not an owned press or release
  send_native(o, 3, 6)
  assert send_return(o, 3, 6, BUTTONS["SET"]).state == EchoState.PRESSED
  send_native(o, 4, 7)
  released = send_return(o, 4, 7)
  assert released.state == EchoState.RELEASED
  assert released.release_return_timestamp == 4
  assert released.last_idle_return_timestamp == 4


def _released_observer():
  o = observer()
  o.arm("RES", 0)
  send_native(o, 0, 0)
  send_return(o, 0, 0)
  send_native(o, 1, 1)
  send_return(o, 1, 1, BUTTONS["RES"])
  send_native(o, 2, 2)
  result = send_return(o, 2, 2)
  assert result.state == EchoState.RELEASED
  return o, result


def test_released_stream_refreshes_idle_time_not_first_owned_release():
  o, first = _released_observer()
  send_native(o, 3, 3)
  later = send_return(o, 3, 3)
  assert later.state == EchoState.RELEASED
  assert later.last_idle_return_timestamp == 3
  assert later.release_return_timestamp == first.release_return_timestamp == 2
  send_native(o, 4, 4)
  latest = send_return(o, 4, 4)
  assert latest.last_idle_return_timestamp == 4
  assert latest.release_return_timestamp == 2


def test_native_idle_without_tx_return_does_not_refresh_baseline():
  o, first = _released_observer()
  latest = send_native(o, 3, 3)
  assert latest.state == EchoState.RELEASED
  assert latest.last_idle_return_timestamp == first.last_idle_return_timestamp == 2


@pytest.mark.parametrize("failure", ["physical_button", "out_of_order", "stale_pair"])
def test_post_release_wrong_button_or_timestamp_invalidates_ownership(failure):
  o, _first = _released_observer()
  if failure == "physical_button":
    result = send_native(o, 3, 3, extra=BUTTONS["DOWN"])
    assert result.reason == "native_not_idle_or_ineligible"
  elif failure == "out_of_order":
    result = send_native(o, 1.5, 3)
    assert result.reason == "out_of_order_timestamp"
  else:
    send_native(o, 3, 3)
    result = send_return(o, 3 + o.max_age + 1, 3)
    assert result.reason == "returned_without_matching_native"
  assert result.state == EchoState.INVALID
  assert result.release_return_timestamp == 2


def test_invalid_ownership_requires_explicit_disarm_before_rearming():
  o = observer()
  o.arm("SET", 0)
  result = send_native(o, 0, 0, extra=BUTTONS["UP"])
  assert result.state == EchoState.INVALID
  with pytest.raises(RuntimeError):
    o.arm("RES", 1)
  reset = o.disarm()
  assert reset.state == EchoState.DISARMED
  assert reset.last_idle_return_timestamp is None
  assert o.arm("RES", 2).state == EchoState.WAIT_NATIVE_IDLE


@pytest.mark.parametrize("kwargs", [
  {"max_age": 0}, {"max_age": True}, {"native_bus": True},
  {"returned_bus": 4}, {"history_size": 1},
])
def test_invalid_configuration_rejected(kwargs):
  with pytest.raises(ValueError):
    PQButtonEchoObserver(**({"max_age": 100} | kwargs))
