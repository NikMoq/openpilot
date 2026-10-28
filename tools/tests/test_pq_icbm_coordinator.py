"""Actual joined pure classes, synthetic raw GRA/echo/stock input, no hardware."""
import importlib
from pathlib import Path
import sys
from types import ModuleType

import pytest


_PACKAGE = "_pq_icbm_coordinator_probe"
_NAMESPACE = ModuleType(_PACKAGE)
_NAMESPACE.__path__ = [str(Path(__file__).resolve().parents[2] /
                          "opendbc_repo/opendbc/sunnypilot/car/volkswagen")]
sys.modules[_PACKAGE] = _NAMESPACE
_MODULE = importlib.import_module(f"{_PACKAGE}.pq_icbm_coordinator")
Coordinator = _MODULE.PQICBMCoordinator
StockFeedback = _MODULE.StockFeedback
NativeRelease = _MODULE.NativeRelease
State = _MODULE.TransactionState
EchoState = _MODULE.EchoState

BITS = {"SET": 1 << 16, "RES": 1 << 17, "UP": 1 << 11, "DOWN": 1 << 10}


def payload(counter, button=None):
  raw = (1 << 8) | (1 << 15) | (1 << 18) | ((counter & 15) << 20) | BITS.get(button, 0)
  data = bytearray(raw.to_bytes(4, "little"))
  data[0] = data[1] ^ data[2] ^ data[3]
  return bytes(data)


def coordinator(**policy):
  # Arbitrary test policies: settle exceeds release freshness, requiring real
  # continuing matched idle echoes, rather than a stale latched RELEASED time.
  return Coordinator(**(dict(transaction_timeout_s=1, feedback_max_age_s=.05,
                              release_max_age_s=.03, settle_s=.06, echo_max_age_s=.5,
                              native_bus=0, returned_bus=130) | policy))


def pair(c, at, counter, button=None, *, returned=True):
  c.observer.observe(at, 0x38a, payload(counter), 0)
  if returned:
    c.observer.observe(at+.001, 0x38a, payload(counter, button), 130)


def update(c, now, speed=80, target=79, **changes):
  args = dict(now=now, target=target, speed_unit="kph", ready=True, readiness_fresh=True,
              active=True, radar_fresh=True, target_fresh=True, target_eligible=True,
              feedback=StockFeedback(speed, now, True), paired_firmware=True)
  return c.update(**(args | changes))


def start(c, now=0, speed=80, target=79):
  pair(c, now, 14)
  button = update(c, now+.002, speed, target)
  assert button is not None
  snapshot = c.observer.snapshot()
  assert snapshot.expected_button == button and snapshot.state == EchoState.WAIT_NATIVE_IDLE
  assert snapshot.last_idle_return_timestamp is None  # arm clears bootstrap.
  return button


def finish(c, start_at, button, next_speed, target):
  pair(c, start_at+.02, 15, button)
  # Target may already be reached: readiness remains true while the owned
  # lease is still pressed, even when high-level sendButton would be none.
  assert update(c, start_at+.022, next_speed, target) is None
  assert c.state == State.WAIT_RELEASE
  pair(c, start_at+.04, 0, button)
  assert update(c, start_at+.042, next_speed, target) is None
  pair(c, start_at+.06, 1)
  first_release = c.observer.snapshot().release_return_timestamp
  for tick in range(3, 8):
    at = start_at + tick*.02
    if tick > 3:
      pair(c, at, tick-2)
    assert update(c, at+.002, next_speed, target) is None
    if c.state == State.IDLE:
      return at+.002, first_release
  raise AssertionError("continuous real idle echoes did not settle")


def test_full_80_79_130_with_actual_observer_raw_native_echo_and_feedback():
  c, now, current, remembered_base = coordinator(), 0, 80, 130
  speeds, actions = [80], []
  for target in (79, remembered_base):
    while current != target:
      button = start(c, now, current, target)
      actions.append(button)
      if button == "SET":
        current -= 1
      elif button == "RES":
        current += 1
      elif button == "UP":
        current = (current//10 + 1)*10
      else:
        current = ((current-1)//10)*10
      speeds.append(current)
      now, _ = finish(c, now, button, current, target)
      now += .02
  assert speeds == [80, 79, 80, 90, 100, 110, 120, 130]
  assert actions == ["SET"] + ["UP"]*6
  assert remembered_base == 130


def test_pairing_defaults_false_even_with_fresh_idle_feedback():
  c = coordinator()
  pair(c, 0, 0)
  assert c.update(now=.002, target=79, speed_unit="kph", ready=True, readiness_fresh=True,
                  active=True, radar_fresh=True, target_fresh=True, target_eligible=True,
                  feedback=StockFeedback(80, .002, True)) is None
  assert c.state == State.IDLE and c.transaction.pending is None


def _firmware_status(request_id, state, button=0):
  return {"valid": True, "signature_matched": True, "protocol_valid": True,
          "panda_count": 1, "request_id": request_id, "state": state, "button": button}


@pytest.mark.parametrize("state", [1, 2, 5])
def test_host_restart_waits_for_existing_firmware_lease(state):
  c = coordinator()
  pair(c, 0, 14)
  assert update(c, .002, firmware_status=_firmware_status(42, state, 3)) is None
  assert c.transaction.pending is None
  assert c.blocked_reason is None
  pair(c, .02, 15)
  assert update(c, .022, firmware_status=_firmware_status(42, 3, 3)) == "SET"
  assert c.transaction.pending.sequence == 43


def test_sequence_rejection_never_automatically_retries():
  c = coordinator()
  pair(c, 0, 14)
  assert update(c, .002, firmware_status=_firmware_status(0, 0)) == "SET"
  status = _firmware_status(1, 4, 3) | {"reason": 1, "tx_counter": 0, "status_mono_time_ns": 10_000_000}
  assert update(c, .012, firmware_status=status) is None
  for i in range(1, 4):
    pair(c, i * .02, (14 + i) % 16)
    assert update(c, i * .02 + .002, firmware_status=status) is None
    assert c.state == State.BLOCKED


@pytest.mark.parametrize("status_time_ns", [5_000_000, 15_000_000])
@pytest.mark.parametrize("state,reason", [(7, 0), (4, 2)])
def test_no_press_abort_requires_new_idle_return_in_both_poll_orders(status_time_ns, state, reason):
  c = coordinator()
  pair(c, 0, 14)
  assert update(c, .002, firmware_status=_firmware_status(0, 0)) == "SET"
  status = _firmware_status(1, state, 3) | {"reason": reason, "tx_counter": 0, "status_mono_time_ns": status_time_ns}
  assert update(c, .01, firmware_status=status) is None
  assert c.state == State.BLOCKED
  # Status by itself never clears: first arm a new idle baseline observer.
  assert update(c, .02, firmware_status=status) is None
  assert update(c, .022, firmware_status=status) is None
  pair(c, .03, 15, returned=False)
  assert update(c, .032, firmware_status=status) is None
  assert c.state == State.BLOCKED
  pair(c, .04, 0)
  assert update(c, .042, firmware_status=status) == "SET"
  assert c.transaction.pending.sequence == 2


def test_live_firmware_sequence_and_release_status_are_correlated_with_raw_echo():
  c = coordinator()
  assert c.synchronize_request_sequence(7)
  pair(c, 0, 14)
  kwargs = dict(now=.002, target=79, speed_unit="kph", ready=True, readiness_fresh=True,
                active=True, radar_fresh=True, target_fresh=True, target_eligible=True,
                feedback=StockFeedback(80, .002, True), paired_firmware=True,
                firmware_status=_firmware_status(7, 3, 1))
  assert c.update(**kwargs) == "SET"
  assert c.transaction.pending.sequence == 8

  pair(c, .02, 15, "SET")
  kwargs.update(now=.022, feedback=StockFeedback(79, .022, True),
                firmware_status=_firmware_status(8, 1, 3))
  assert c.update(**kwargs) is None
  pair(c, .04, 0, "SET")
  kwargs.update(now=.042, firmware_status=_firmware_status(8, 2, 3))
  assert c.update(**kwargs) is None
  pair(c, .06, 1)
  kwargs.update(now=.062, feedback=StockFeedback(79, .062, True),
                firmware_status=_firmware_status(8, 3, 3))
  assert c.update(**kwargs) is None
  assert c.state == State.SETTLING


def test_matching_firmware_rejection_blocks_and_retains_outstanding_ownership():
  c = coordinator()
  c.synchronize_request_sequence(7)
  pair(c, 0, 14)
  status = _firmware_status(7, 3, 1)
  kwargs = dict(now=.002, target=79, speed_unit="kph", ready=True, readiness_fresh=True,
                active=True, radar_fresh=True, target_fresh=True, target_eligible=True,
                feedback=StockFeedback(80, .002, True), paired_firmware=True,
                firmware_status=status)
  assert c.update(**kwargs) == "SET"
  pending = c.transaction.pending
  kwargs.update(now=.01, firmware_status=_firmware_status(8, 4, 3))
  assert c.update(**kwargs) is None
  assert c.state == State.BLOCKED
  assert c.transaction.unresolved == pending


def test_firmware_complete_status_alone_does_not_replace_passive_release_echo():
  c = coordinator()
  c.synchronize_request_sequence(7)
  pair(c, 0, 14)
  status = _firmware_status(7, 3, 1)
  kwargs = dict(now=.002, target=79, speed_unit="kph", ready=True, readiness_fresh=True,
                active=True, radar_fresh=True, target_fresh=True, target_eligible=True,
                feedback=StockFeedback(80, .002, True), paired_firmware=True, firmware_status=status)
  assert c.update(**kwargs) == "SET"
  pair(c, .02, 15, "SET")
  kwargs.update(now=.022, feedback=StockFeedback(79, .022, True),
                firmware_status=_firmware_status(8, 1, 3))
  c.update(**kwargs)
  # Native release is present, but omit its passive returned echo. The Panda
  # lifecycle says COMPLETE; the independent host observer must still agree.
  pair(c, .04, 0, "SET", returned=False)
  kwargs.update(now=.042, firmware_status=_firmware_status(8, 2, 3))
  c.update(**kwargs)
  pair(c, .06, 1, returned=False)
  kwargs.update(now=.062, feedback=StockFeedback(79, .062, True),
                firmware_status=_firmware_status(8, 3, 3))
  c.update(**kwargs)
  assert c.transaction.pending is not None
  assert c.state == State.WAIT_RELEASE


def test_firmware_abort_release_and_fresh_stock_feedback_automatically_rearm():
  c = coordinator()
  pair(c, 0, 14)
  base = dict(speed_unit="kph", ready=True, readiness_fresh=True, active=True, radar_fresh=True,
              target_fresh=True, target_eligible=True, paired_firmware=True)
  assert c.update(now=.002, target=79, feedback=StockFeedback(80, .002, True),
                  firmware_status=_firmware_status(0, 0), **base) == "SET"
  pending = c.transaction.pending
  assert pending.sequence == 1

  assert c.update(now=.01, target=79, speed_unit="kph", ready=False, readiness_fresh=True, active=False,
                  radar_fresh=True, target_fresh=True, target_eligible=True,
                  feedback=StockFeedback(80, .01, True), paired_firmware=True,
                  firmware_status=_firmware_status(1, 1, 3)) is None
  assert c.state == State.BLOCKED and c.transaction.unresolved == pending

  status = _firmware_status(1, 6, 3)
  status["tx_counter"] = 2
  status["status_mono_time_ns"] = 20_000_000
  assert c.update(now=.032, target=79, feedback=StockFeedback(80, .03, True),
                  firmware_status=status, **base) == "SET"
  assert c.transaction.pending.sequence == 2
  assert c.state == State.WAIT_FEEDBACK


@pytest.mark.parametrize("changes", [dict(paired_firmware=False), dict(paired_firmware=1), dict(active=False),
                                     dict(ready=False), dict(readiness_fresh=False), dict(radar_fresh=False),
                                     dict(target_fresh=False), dict(target_eligible=False)])
def test_no_initial_action_without_explicit_fresh_eligibility(changes):
  c = coordinator()
  pair(c, 0, 0)
  assert update(c, .002, **changes) is None
  assert c.transaction.pending is None


def test_no_bootstrap_from_native_without_matched_idle_tx_return():
  c = coordinator()
  pair(c, 0, 0, returned=False)
  assert update(c, .002) is None
  assert c.observer.snapshot().release_return_timestamp is None


def test_early_feedback_is_not_release_and_only_one_button_is_returned():
  c = coordinator()
  assert start(c, target=130) == "UP"
  pair(c, .02, 15, "UP")
  for now in (.022, .03, .04):
    assert update(c, now, 90, 130) is None
    assert c.state == State.WAIT_RELEASE
  assert c.transaction.pending.sequence == 1


def test_latched_release_without_fresh_idle_returns_cannot_complete_settle():
  c = coordinator()
  start(c, target=130)
  pair(c, .02, 15, "UP")
  update(c, .022, 90, 130)
  pair(c, .04, 0)
  update(c, .042, 90, 130)
  released_at = c.observer.snapshot().release_return_timestamp
  for now in (.06, .08, .10, .12):
    assert update(c, now, 90, 130) is None
    assert c.transaction.pending is not None
  assert c.observer.snapshot().last_idle_return_timestamp == released_at == .041
  assert c.state == State.WAIT_RELEASE


@pytest.mark.parametrize("change", [dict(target=130), dict(speed_unit="mph"), dict(ready=False),
                                    dict(readiness_fresh=False), dict(active=False), dict(radar_fresh=False),
                                    dict(paired_firmware=False)])
def test_pending_invalidation_latches_without_fake_lease_cancellation(change):
  c = coordinator()
  start(c)
  issued = c.transaction.pending
  assert update(c, .01, **change) is None
  assert c.state == State.BLOCKED and c.blocked_reason
  assert c.transaction.pending is None and c.transaction.unresolved is issued
  assert update(c, .02) is None


def test_invalid_echo_blocks_and_explicit_independent_clearance_allows_automatic_rearm():
  c = coordinator()
  start(c)
  pair(c, .02, 15, "SET")
  c.observer.observe(.0215, 0x38a, payload(15, "SET"), 194)
  assert update(c, .022) is None
  assert c.state == State.BLOCKED and c.blocked_reason == "echo_invalid:rejected_return"
  feedback, release = StockFeedback(79, .04, True), NativeRelease(True, .04, True)
  assert not c.clear_after_external_confirmation(now=.04, transport_idle_confirmed=False,
                                                  feedback=feedback, release=release)
  # Independent caller verification may be automatic; no user/UI reset exists.
  assert c.clear_after_external_confirmation(now=.05, transport_idle_confirmed=True,
                                              feedback=feedback, release=release)
  assert c.observer.snapshot().state == EchoState.DISARMED
  assert start(c, .06, 79, 130) == "UP"


@pytest.mark.parametrize("reason", ["gas", "brake", "manual"])
def test_readiness_drop_preserves_external_base_and_can_resume_after_verified_clear(reason):
  c, remembered_base = coordinator(), 130
  start(c)
  assert update(c, .01, ready=False) is None
  assert c.state == State.BLOCKED
  assert reason in ("gas", "brake", "manual")
  assert remembered_base == 130
  assert c.clear_after_external_confirmation(now=.04, transport_idle_confirmed=True,
                                              feedback=StockFeedback(79, .04, True),
                                              release=NativeRelease(True, .04, True))
  # Caller supplies resumed readiness after gas release or driver RES/reengagement.
  assert start(c, .06, 79, remembered_base) == "UP"


def test_missing_stock_feedback_never_retries_and_deadline_blocks():
  c = coordinator()
  start(c)
  for now in (.02, .1, .5, 1.01, 2):
    assert update(c, now, feedback=None) is None
  assert c.state == State.BLOCKED and c.blocked_reason == "deadline_elapsed"


def test_target_eligibility_loss_does_not_interrupt_owned_release():
  c = coordinator()
  assert start(c, 0, 80) == "SET"
  # High-level planner may clear sendButton once target is attained. The
  # backend still owns this single press and must observe feedback/release.
  assert update(c, .02, 79, target_eligible=False,
                feedback=StockFeedback(79, .02, True)) is None
  assert c.state == State.WAIT_RELEASE
  assert update(c, .04, 79, target_eligible=False,
                feedback=StockFeedback(79, .04, True)) is None
  assert c.state == State.WAIT_RELEASE


def test_passive_hook_nanoseconds_at_large_boot_time_convert_before_observation():
  c = coordinator()
  boot_ns = 8_123_000_000_000

  def raw_pair(offset_ns, counter, button=None):
    # The actual hook attaches the coordinator itself, never c.observer.
    c.observe(boot_ns+offset_ns, 0x38a, payload(counter), 0)
    return c.observe(boot_ns+offset_ns+1_000_000, 0x38a, payload(counter, button), 130)

  def run(offset_ns, speed):
    now = (boot_ns+offset_ns)*1e-9
    return update(c, now, speed=speed)

  baseline = raw_pair(0, 14)
  assert baseline.state == EchoState.DISARMED
  assert baseline.last_idle_return_timestamp == (boot_ns+1_000_000)*1e-9
  assert baseline.release_return_timestamp is None
  assert run(2_000_000, 80) == "SET"
  assert c.snapshot().issued_timestamp == (boot_ns+2_000_000)*1e-9
  pressed = raw_pair(20_000_000, 15, "SET")
  assert pressed.state == EchoState.PRESSED
  assert run(22_000_000, 79) is None
  assert c.state == State.WAIT_RELEASE
  first_release = None
  for i in range(2, 8):
    released = raw_pair(i*20_000_000, i-2)
    if first_release is None:
      first_release = released.release_return_timestamp
    assert released.release_return_timestamp == first_release
    assert released.last_idle_return_timestamp == (boot_ns+i*20_000_000+1_000_000)*1e-9
    assert run(i*20_000_000+2_000_000, 79) is None
    if c.state == State.IDLE:
      break
  assert c.state == State.IDLE and c.transaction.pending is None


@pytest.mark.parametrize("timestamp_ns", [True, -1, 1.0, None])
def test_invalid_raw_timestamp_units_or_types_latch_pending_block(timestamp_ns):
  c = coordinator()
  start(c)
  issued = c.transaction.pending
  with pytest.raises(ValueError):
    c.observe(timestamp_ns, 0x38a, payload(0), 0)
  assert c.state == State.BLOCKED and c.transaction.unresolved is issued
