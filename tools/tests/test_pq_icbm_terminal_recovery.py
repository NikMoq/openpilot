"""Manual input after firmware COMPLETE, before host settling: real echo model."""
import importlib
from pathlib import Path
import sys
from types import ModuleType

import pytest

PACKAGE = '_pq_terminal_recovery_probe'
namespace = ModuleType(PACKAGE)
namespace.__path__ = [str(Path(__file__).resolve().parents[2] /
                         'opendbc_repo/opendbc/sunnypilot/car/volkswagen')]
sys.modules[PACKAGE] = namespace
pq = importlib.import_module(PACKAGE + '.pq_icbm_coordinator')


def payload(counter, button_bit=0):
  data = bytearray(((1 << 8) | (1 << 15) | (1 << 18) | (counter << 20) | button_bit).to_bytes(4, 'little'))
  data[0] = data[1] ^ data[2] ^ data[3]
  return bytes(data)


def pair(c, at, counter, pressed=False):
  c.observe(round(at * 1e9), 906, payload(counter), 0)
  c.observe(round((at + .001) * 1e9), 906, payload(counter, (1 << 16) if pressed else 0), 130)


def step(c, status, now, **changes):
  args = dict(now=now, target=33, speed_unit='kph', ready=True, readiness_fresh=True,
              active=True, radar_fresh=True, target_fresh=True, target_eligible=True,
              feedback=pq.StockFeedback(50, now, True), paired_firmware=True, firmware_status=status)
  return c.update(**(args | changes))


def blocked_after_complete():
  c = pq.PQICBMCoordinator(transaction_timeout_s=1, feedback_max_age_s=.12,
                           release_max_age_s=.12, settle_s=.08, echo_max_age_s=.5,
                           native_bus=0, returned_bus=130)
  status = dict(valid=True, signature_matched=True, protocol_valid=True, panda_count=1,
                request_id=0, state=0, button=0, tx_counter=0, status_mono_time_ns=1)
  pair(c, 10, 0)
  assert step(c, status, 10.002, target=31, feedback=pq.StockFeedback(40, 10.002, True)) == 'SET'
  pair(c, 10.02, 1, pressed=True)
  pair(c, 10.04, 2)
  status.update(request_id=1, state=3, button=3, tx_counter=2, status_mono_time_ns=10_042_000_000)
  assert step(c, status, 10.044, target=31, feedback=pq.StockFeedback(39, 10.044, True)) is None
  assert c.state == pq.TransactionState.SETTLING
  c.observe(10_060_000_000, 906, payload(3, 1 << 11), 0)  # Driver UP.
  assert step(c, status, 10.061, ready=False, active=False) is None
  assert c.blocked_reason == 'echo_invalid:native_not_idle_or_ineligible'
  assert c.snapshot().state == pq.EchoState.INVALID
  return c, status


@pytest.mark.parametrize('terminal', [3, 6])
def test_older_terminal_requires_new_idle_txok_then_rearms_with_next_id(terminal):
  c, status = blocked_after_complete()
  status['state'] = terminal
  original_stamp = status['status_mono_time_ns']
  assert step(c, status, 10.062) is None
  assert c.blocked_reason is not None
  assert c.snapshot().state == pq.EchoState.DISARMED
  assert c.snapshot().last_idle_return_timestamp is None
  pair(c, 10.08, 4)
  assert step(c, status, 10.082) == 'DOWN'
  assert c.blocked_reason is None
  assert c.transaction.pending.sequence == 2
  assert c.transaction.pending.baseline_speed == 50
  assert status['status_mono_time_ns'] == original_stamp  # Polling did not manufacture a release timestamp.


def test_older_terminal_without_new_idle_does_not_clear_or_retry():
  c, status = blocked_after_complete()
  for now in (10.062, 10.08, 10.1, 10.2):
    assert step(c, status, now) is None
    assert c.blocked_reason is not None
    assert c.transaction.pending is None


@pytest.mark.parametrize('changes', [dict(request_id=2), dict(valid=False), dict(signature_matched=False),
                                    dict(protocol_valid=False), dict(panda_count=2), dict(button=1),
                                    dict(state=0), dict(state=1), dict(state=2), dict(state=5),
                                    dict(status_mono_time_ns=0)])
def test_unproven_terminal_cannot_reset_owned_observation(changes):
  c, status = blocked_after_complete()
  status.update(changes)
  assert step(c, status, 10.062) is None
  assert c.snapshot().state == pq.EchoState.INVALID
  assert c.blocked_reason is not None
  assert c.transaction.pending is None


@pytest.mark.parametrize('returned_source', [None, 128, 194])
def test_native_idle_without_matched_successful_forwarding_cannot_clear(returned_source):
  c, status = blocked_after_complete()
  assert step(c, status, 10.062) is None
  c.observe(10_080_000_000, 906, payload(4), 0)
  if returned_source is not None:
    c.observe(10_081_000_000, 906, payload(4), returned_source)
  assert step(c, status, 10.082) is None
  assert c.blocked_reason is not None


def test_unmatched_counter_txok_cannot_clear():
  c, status = blocked_after_complete()
  assert step(c, status, 10.062) is None
  c.observe(10_080_000_000, 906, payload(4), 0)
  c.observe(10_081_000_000, 906, payload(5), 130)
  assert step(c, status, 10.082) is None
  assert c.blocked_reason is not None


@pytest.mark.parametrize('stale', ['feedback', 'idle'])
def test_stale_postblock_evidence_cannot_clear(stale):
  c, status = blocked_after_complete()
  assert step(c, status, 10.062) is None
  pair(c, 10.08, 4)
  if stale == 'feedback':
    result = step(c, status, 10.082, feedback=pq.StockFeedback(50, 9, True))
  else:
    result = step(c, status, 10.3)
  assert result is None
  assert c.blocked_reason is not None


@pytest.mark.parametrize('changes', [dict(ready=False), dict(active=False), dict(readiness_fresh=False),
                                    dict(radar_fresh=False), dict(target_fresh=False), dict(paired_firmware=False),
                                    dict(target_eligible=False)])
def test_verified_idle_clearance_does_not_bypass_new_press_guards(changes):
  c, status = blocked_after_complete()
  assert step(c, status, 10.062) is None
  pair(c, 10.08, 4)
  assert step(c, status, 10.082, **changes) is None
  assert c.transaction.pending is None


def test_pairing_or_id_loss_after_bootstrap_still_cannot_clear():
  c, status = blocked_after_complete()
  assert step(c, status, 10.062) is None
  pair(c, 10.08, 4)
  status['signature_matched'] = False
  assert step(c, status, 10.082) is None
  assert c.blocked_reason is not None
  status.update(signature_matched=True, request_id=2)
  assert step(c, status, 10.083) is None
  assert c.blocked_reason is not None
