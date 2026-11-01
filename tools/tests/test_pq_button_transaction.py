"""Pure scheduler tests with synthetic clock/ECU/release observations only.

Policy times are arbitrary test inputs, not established ECU constants. Release
events model completed owned presses, not raw source GRA during a running lease.
Nothing is sent, connected, enabled, cancelled, or acknowledged by a backend.
"""
import importlib
import math
from pathlib import Path
import sys
from types import ModuleType

import pytest


# A private package loads the actual module and its relative selector dependency
# without importing vehicle runtime dependencies or replacing production modules.
_PACKAGE = "_pq_transaction_probe"
_NAMESPACE = ModuleType(_PACKAGE)
_NAMESPACE.__path__ = [str(Path(__file__).resolve().parents[2] /
                          "opendbc_repo/opendbc/sunnypilot/car/volkswagen")]
sys.modules[_PACKAGE] = _NAMESPACE
_MODULE = importlib.import_module(f"{_PACKAGE}.pq_button_transaction")
PQButtonTransaction = _MODULE.PQButtonTransaction
StockFeedback = _MODULE.StockFeedback
NativeRelease = _MODULE.NativeRelease
State = _MODULE.TransactionState


def scheduler(**changes):
  policy = dict(transaction_timeout_s=10, feedback_max_age_s=2, release_max_age_s=2, settle_s=1)
  return PQButtonTransaction(**(policy | changes))


def update(s, now, speed=80, target=79, *, feedback_at=None, released=True, release_at=None,
           stock_fresh=True, release_fresh=True, ready=True, active=True, target_fresh=True, unit="kph"):
  return s.update(now=now, target=target, speed_unit=unit, ready=ready, active=active, target_fresh=target_fresh,
                  feedback=StockFeedback(speed, now if feedback_at is None else feedback_at, stock_fresh),
                  release=NativeRelease(released, now if release_at is None else release_at, release_fresh))


def test_model_80_to_79_then_restore_130_preserves_external_base():
  s, current, now, remembered_base = scheduler(), 80, 0, 130
  speeds, actions = [current], []
  for target in (79, remembered_base):
    while current != target:
      press = update(s, now, current, target)
      assert press is not None
      actions.append(press.button)
      if press.button == "SET":
        current -= 1
      elif press.button == "RES":
        current += 1
      elif press.button == "UP":
        current = 10 * math.floor(current/10) + 10
      else:
        current = 10 * math.ceil(current/10) - 10
      speeds.append(current)
      assert update(s, now+1, current, target, released=False) is None
      assert s.state == State.WAIT_RELEASE
      assert update(s, now+2, current, target) is None
      assert s.state == State.SETTLING
      assert update(s, now+3, current, target, release_at=now+2) is None
      assert s.state == State.IDLE and s.pending is None
      now += 4
  assert speeds == [80, 79, 80, 90, 100, 110, 120, 130]
  assert actions == ["SET"] + ["UP"]*6
  assert remembered_base == 130
  assert update(s, now, current, remembered_base) is None


def test_early_feedback_cannot_start_another_press_before_owned_release():
  s = scheduler()
  first = update(s, 0, target=130)
  assert first.button == "UP"
  for now in (1, 2, 3, 4):
    assert update(s, now, speed=90, target=130, released=False) is None
    assert s.pending is first and s.state == State.WAIT_RELEASE
  assert update(s, 5, speed=90, target=130) is None
  assert s.state == State.SETTLING
  assert update(s, 6, speed=90, target=130, release_at=5) is None
  assert s.state == State.IDLE
  second = update(s, 7, speed=90, target=130)
  assert second.button == "UP" and second.sequence == first.sequence+1


@pytest.mark.parametrize("kind", ["unchanged", "stale", "missing", "old_timestamp"])
def test_no_blind_retry_for_unusable_feedback_even_after_deadline(kind):
  s = scheduler()
  first = update(s, 0)
  for now in range(1, 16):
    if kind == "missing":
      result = s.update(now=now, target=79, speed_unit="kph", ready=True, active=True, target_fresh=True,
                        feedback=None, release=NativeRelease(True, now, True))
    else:
      result = update(s, now, speed=80 if kind == "unchanged" else 79,
                      stock_fresh=kind != "stale", feedback_at=0 if kind == "old_timestamp" else now)
    assert result is None
  assert s.state == State.BLOCKED and s.pending is None
  assert s.unresolved is first and s.blocked_reason == "deadline_elapsed"
  assert update(s, 16, speed=79, target=130) is None


@pytest.mark.parametrize("change", [dict(target=130), dict(unit="mph"), dict(unit="unknown"),
                                   dict(ready=False), dict(active=False), dict(target_fresh=False)])
def test_invalidation_does_not_fake_firmware_cancellation(change):
  s = scheduler()
  issued = update(s, 0)
  assert update(s, 1, **change) is None
  assert s.pending is None and s.unresolved is issued and s.state == State.BLOCKED
  assert update(s, 2, speed=79, target=130) is None
  assert not s.clear_after_external_confirmation(now=3, transport_idle_confirmed=False,
                                                  feedback=StockFeedback(79, 3, True), release=NativeRelease(True, 3, True))
  assert s.unresolved is issued
  assert s.clear_after_external_confirmation(now=4, transport_idle_confirmed=True,
                                              feedback=StockFeedback(79, 4, True), release=NativeRelease(True, 4, True))
  assert s.state == State.IDLE and s.unresolved is None
  assert update(s, 5, speed=79, target=130).button == "UP"


@pytest.mark.parametrize("reason", ["brake", "manual"])
def test_caller_readiness_drop_from_brake_or_manual_blocks_pending(reason):
  s = scheduler()
  issued = update(s, 0)
  # The caller derives readiness from actual pedal/manual observations.
  assert reason in ("brake", "manual")
  assert update(s, 1, ready=False) is None
  assert s.unresolved is issued and s.pending is None
  assert update(s, 2, ready=True) is None


def test_wall_clock_passage_and_old_release_are_not_settled_feedback():
  s = scheduler()
  update(s, 0)
  assert update(s, 1, speed=79, released=False) is None
  assert update(s, 2, speed=79, release_at=0) is None
  assert s.state == State.WAIT_RELEASE
  assert update(s, 3, speed=79, release_at=3) is None
  assert s.state == State.SETTLING
  assert update(s, 4, speed=79, feedback_at=3, release_at=3) is None
  assert s.state == State.SETTLING
  assert update(s, 5, speed=79, feedback_at=5, release_at=3) is None
  assert s.state == State.IDLE


@pytest.mark.parametrize("changes", [dict(ready=False), dict(active=False), dict(target_fresh=False),
                                     dict(stock_fresh=False), dict(release_fresh=False), dict(released=False),
                                     dict(feedback_at=-3), dict(release_at=-3), dict(feedback_at=1),
                                     dict(release_at=1), dict(unit="mph"), dict(unit="unknown"),
                                     dict(speed=255), dict(target=math.nan)])
def test_no_initial_press_without_valid_fresh_caller_observations(changes):
  s = scheduler()
  assert update(s, 0, **changes) is None
  assert s.state == State.IDLE and s.pending is None


def test_unexpected_overshoot_blocks_instead_of_issuing_reverse_correction():
  s = scheduler()
  issued = update(s, 0)
  assert update(s, 1, speed=78) is None
  assert s.state == State.BLOCKED and s.unresolved is issued
  assert s.blocked_reason == "unexpected_feedback"


@pytest.mark.parametrize("key,bad", [("transaction_timeout_s", 0), ("feedback_max_age_s", -1),
                                    ("release_max_age_s", math.inf), ("settle_s", math.nan),
                                    ("settle_s", 10), ("settle_s", True)])
def test_invalid_timing_configuration_is_rejected(key, bad):
  with pytest.raises(ValueError):
    scheduler(**{key: bad})


def test_clock_rollback_blocks_outstanding_press():
  s = scheduler()
  issued = update(s, 2)
  with pytest.raises(ValueError):
    update(s, 1)
  assert s.state == State.BLOCKED and s.unresolved is issued


def test_external_clearance_cannot_reuse_pre_block_observations():
  s = scheduler()
  update(s, 0)
  update(s, 1, ready=False)
  assert not s.clear_after_external_confirmation(now=2, transport_idle_confirmed=True,
                                                  feedback=StockFeedback(79, 1, True), release=NativeRelease(True, 1, True))
  assert s.state == State.BLOCKED


def test_streaming_20ms_release_and_50hz_feedback_complete_positive_settle():
  # Arbitrary policy input; not a proven ECU settling interval.
  s = scheduler(transaction_timeout_s=2, feedback_max_age_s=.1, release_max_age_s=.1, settle_s=.06)
  issued = update(s, 0, target=130)
  assert issued.button == "UP"
  assert update(s, .02, speed=90, target=130, released=False) is None
  for tick in range(2, 6):
    now = tick / 50
    assert update(s, now, speed=90, target=130) is None
    if tick < 5:
      assert s.pending is issued and s.state == State.SETTLING
  assert s.state == State.IDLE and s.pending is None
  assert update(s, .12, speed=90, target=130).button == "UP"


def test_new_pressed_observation_restarts_continuous_release_settle():
  s = scheduler(settle_s=1)
  issued = update(s, 0, target=130)
  update(s, 1, speed=90, target=130)
  assert s.state == State.SETTLING
  update(s, 1.5, speed=90, target=130, released=False)
  assert s.state == State.WAIT_RELEASE
  update(s, 2, speed=90, target=130)
  update(s, 2.5, speed=90, target=130)
  assert s.pending is issued and s.state == State.SETTLING
  update(s, 3, speed=90, target=130)
  assert s.state == State.IDLE


def test_older_pressed_release_observation_cannot_replace_newer_release_run():
  s = scheduler(settle_s=2)
  issued = update(s, 0, target=130)
  update(s, 1, speed=90, target=130)
  update(s, 2, speed=90, target=130)
  # A delayed pressed observation is still within max age but is older than
  # the latest released observation. It cannot erase the release-run latch.
  update(s, 2.5, speed=90, target=130, released=False, release_at=1.5)
  assert s.pending is issued
  update(s, 3, speed=90, target=130)
  assert s.state == State.IDLE


def test_older_released_observation_cannot_undo_newer_pressed_observation():
  s = scheduler(settle_s=1)
  issued = update(s, 0, target=130)
  update(s, 1, speed=90, target=130)
  update(s, 2, speed=90, target=130, released=False)
  update(s, 2.5, speed=90, target=130, release_at=1.5)
  assert s.pending is issued and s.state == State.WAIT_RELEASE
  update(s, 3, speed=90, target=130)
  update(s, 3.5, speed=90, target=130)
  assert s.pending is issued
  update(s, 4, speed=90, target=130)
  assert s.state == State.IDLE


def test_regressing_stock_feedback_is_not_an_ack_or_a_new_baseline():
  s = scheduler(settle_s=2)
  issued = update(s, 0, target=130)
  update(s, 1, speed=90, target=130)
  update(s, 2, speed=90, target=130)
  update(s, 2.5, speed=80, target=130, feedback_at=1.5)
  assert s.pending is issued and s.state == State.WAIT_FEEDBACK
  update(s, 3, speed=90, target=130)
  assert s.state == State.IDLE


def test_feedback_gap_restarts_stability_observation_window():
  s = scheduler(feedback_max_age_s=.5, settle_s=1)
  issued = update(s, 0, target=130)
  update(s, .2, speed=90, target=130)
  # No observed continuity through a gap exceeding the freshness policy.
  update(s, 1, speed=90, target=130)
  update(s, 1.4, speed=90, target=130)
  assert s.pending is issued
  update(s, 1.8, speed=90, target=130)
  update(s, 2, speed=90, target=130)
  assert s.state == State.IDLE


def test_release_observation_gap_starts_a_new_confirmed_release_run():
  s = scheduler(feedback_max_age_s=.5, release_max_age_s=.5, settle_s=1)
  issued = update(s, 0, target=130)
  update(s, .2, speed=90, target=130)
  update(s, .6, speed=90, target=130, release_at=.2)
  update(s, 1, speed=90, target=130)
  update(s, 1.4, speed=90, target=130)
  assert s.pending is issued
  update(s, 1.8, speed=90, target=130)
  update(s, 2, speed=90, target=130)
  assert s.state == State.IDLE


@pytest.mark.parametrize("missing", [False, True])
def test_unconfirmed_release_times_out_without_another_selection(missing):
  s = scheduler()
  issued = update(s, 0, target=130)
  for now in range(1, 12):
    release = None if missing else NativeRelease(True, now, False)
    assert s.update(now=now, target=130, speed_unit="kph", ready=True, active=True, target_fresh=True,
                    feedback=StockFeedback(90, now, True), release=release) is None
  assert s.state == State.BLOCKED and s.pending is None and s.unresolved is issued
