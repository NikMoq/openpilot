"""Pure, inactive, one-press PQ scheduler; no backend or firmware activation.

All seconds use one caller-supplied monotonic clock. Timing arguments have no
defaults: they are caller policy, not established ECU timing constants. Targets
are current displayed values; remembered user/base targets remain outside this
class. A returned Press is conservatively considered issued immediately.

The caller supplies fresh target/readiness/active-ACC observations and timestamped
stock feedback and native release. Release means an owned press has ended and
the eligible native no-button frame has actually been released/queued downstream;
raw pre-transform idle GRA while a firmware lease continues is NOT this evidence.
These observations do not authenticate ECU responses or cancel a firmware lease.

Invalidation/timeout abandons the local pending transaction and latches BLOCKED,
retaining unresolved ownership. Rearming requires explicit external transport/
lease-idle confirmation plus fresh observations after the block. The current
adapter cancellation/ownership protocol is unresolved; this module supplies no
fake acknowledgement, cancel command, retry, or physical-readiness guarantee.
"""
from dataclasses import dataclass
from enum import Enum
import math

from .pq_button_selection import PQButton, _valid_display_speed, select_pq_button


class TransactionState(str, Enum):
  IDLE = "idle"
  WAIT_FEEDBACK = "wait_feedback"
  WAIT_RELEASE = "wait_release"
  SETTLING = "settling"
  BLOCKED = "blocked"


@dataclass(frozen=True)
class StockFeedback:
  speed: int | float
  observed_at: float
  fresh: bool


@dataclass(frozen=True)
class NativeRelease:
  released: bool
  observed_at: float
  fresh: bool


@dataclass(frozen=True)
class Press:
  sequence: int
  button: PQButton
  target: int
  speed_unit: str
  issued_at: float
  deadline: float
  baseline_speed: int
  baseline_feedback_at: float


def _finite_number(value):
  return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class PQButtonTransaction:
  def __init__(self, *, transaction_timeout_s: float, feedback_max_age_s: float,
               release_max_age_s: float, settle_s: float):
    for name, value in (("transaction_timeout_s", transaction_timeout_s),
                        ("feedback_max_age_s", feedback_max_age_s),
                        ("release_max_age_s", release_max_age_s), ("settle_s", settle_s)):
      if not _finite_number(value) or value < 0 or (name != "settle_s" and value == 0):
        raise ValueError(f"invalid timing policy: {name}")
    if settle_s >= transaction_timeout_s:
      raise ValueError("settle_s must be shorter than transaction_timeout_s")
    self.transaction_timeout_s = transaction_timeout_s
    self.feedback_max_age_s = feedback_max_age_s
    self.release_max_age_s = release_max_age_s
    self.settle_s = settle_s
    self.state = TransactionState.IDLE
    self.pending: Press | None = None
    self.unresolved: Press | None = None
    self.blocked_reason: str | None = None
    self._blocked_at = None
    self._last_now = None
    self._sequence = 0
    self._feedback_at = None
    self._feedback_speed = None
    self._stable_since = None
    self._release_at = None
    self._release_observed_at = None
    self._release_observed_value = None

  def synchronize_request_sequence(self, request_id: int) -> bool:
    """Align host IDs to the live firmware lease counter before a new press."""
    if (type(request_id) is not int or not 0 <= request_id <= 0xffff
        or self.state != TransactionState.IDLE or self.pending is not None or self.unresolved is not None):
      return False
    self._sequence = request_id
    return True

  def _clock(self, now):
    if not _finite_number(now) or now < 0 or (self._last_now is not None and now < self._last_now):
      if self.pending is not None:
        self._block("invalid_clock", self._last_now)
      raise ValueError("now must be finite, nonnegative and monotonic")
    self._last_now = now

  @staticmethod
  def _fresh(observation, now, max_age):
    return (observation is not None and observation.fresh is True
            and _finite_number(observation.observed_at)
            and 0 <= now - observation.observed_at <= max_age)

  def _block(self, reason, now):
    self.unresolved = self.pending
    self.pending = None
    self.blocked_reason, self._blocked_at = reason, now
    self.state = TransactionState.BLOCKED

  def update(self, *, now: float, target: int | float, speed_unit: str,
             ready: bool, active: bool, target_fresh: bool,
             feedback: StockFeedback | None, release: NativeRelease | None) -> Press | None:
    """Advance observations; return at most one new press, never a blind retry.

    ready includes caller-observed brake/gas/manual priority. active must mean
    positively active stock ACC, not merely availability. Both are required
    even if the high-level controller is still requesting a button.
    """
    self._clock(now)
    if self.state == TransactionState.BLOCKED:
      return None
    eligible = (ready is True and active is True and target_fresh is True
                and speed_unit == "kph" and _valid_display_speed(target))
    if self.pending is not None:
      if not eligible:
        self._block("readiness_or_input_lost", now)
        return None
      if (target, speed_unit) != (self.pending.target, self.pending.speed_unit):
        self._block("target_or_unit_replaced", now)
        return None
      if now >= self.pending.deadline:
        self._block("deadline_elapsed", now)
        return None
    elif not eligible:
      return None

    stock_ok = self._fresh(feedback, now, self.feedback_max_age_s) and _valid_display_speed(feedback.speed)
    release_ok = self._fresh(release, now, self.release_max_age_s) and release.released is True
    if self.pending is None:
      # Target eligibility controls creation of a new press only. Once issued,
      # this transaction must run to observed release even if the planner has
      # already cleared its requested button after reaching the target.
      if not eligible:
        return None
      # Begin only from a caller-confirmed released baseline with fresh stock.
      if not stock_ok or not release_ok:
        return None
      action = select_pq_button(target, feedback.speed, speed_unit)
      if action is None:
        return None
      self._sequence = 1 if self._sequence >= 0xffff else self._sequence + 1
      self.pending = Press(self._sequence, action, int(target), speed_unit, now,
                           now + self.transaction_timeout_s, int(feedback.speed), feedback.observed_at)
      self._feedback_at = feedback.observed_at
      self._feedback_speed = int(feedback.speed)
      self._stable_since = self._release_at = None
      self._release_observed_at = release.observed_at
      self._release_observed_value = release.released
      self.state = TransactionState.WAIT_FEEDBACK
      return self.pending

    press = self.pending
    # Only newer feedback after issuance can acknowledge progress. A new
    # timestamp with the unchanged baseline is freshness, not an ACK.
    if stock_ok and feedback.observed_at > press.issued_at:
      if feedback.observed_at > self._feedback_at:
        speed = int(feedback.speed)
        if speed != press.baseline_speed:
          if not min(press.baseline_speed, press.target) <= speed <= max(press.baseline_speed, press.target):
            self._block("unexpected_feedback", now)
            return None
          if (speed != self._feedback_speed or self._stable_since is None
              or feedback.observed_at - self._feedback_at > self.feedback_max_age_s):
            self._stable_since = feedback.observed_at
        else:
          self._stable_since = None
        self._feedback_speed, self._feedback_at = speed, feedback.observed_at
      elif feedback.observed_at == self._feedback_at and int(feedback.speed) != self._feedback_speed:
        self._block("conflicting_feedback_timestamp", now)
        return None

    release_current = (self._fresh(release, now, self.release_max_age_s)
                       and isinstance(release.released, bool))
    if release_current and release.observed_at > press.issued_at:
      if release.observed_at > self._release_observed_at:
        observation_gap = release.observed_at - self._release_observed_at
        self._release_observed_at = release.observed_at
        self._release_observed_value = release.released
        if release.released:
          # Latch the start of this continuous released run, not every idle
          # frame. Moving this timestamp forward would postpone settling forever.
          if self._release_at is None or observation_gap > self.release_max_age_s:
            self._release_at = release.observed_at
        else:
          self._release_at = None
      elif (release.observed_at == self._release_observed_at
            and release.released != self._release_observed_value):
        self._block("conflicting_release_timestamp", now)
        return None

    # Completion needs CURRENT fresh observations, not just remembered positives.
    progress = (stock_ok and feedback.observed_at == self._feedback_at
                and self._stable_since is not None and self._feedback_speed != press.baseline_speed)
    released = (release_ok and self._release_at is not None
                and release.observed_at == self._release_observed_at)
    if not progress:
      self.state = TransactionState.WAIT_FEEDBACK
    elif not released:
      self.state = TransactionState.WAIT_RELEASE
    else:
      self.state = TransactionState.SETTLING
      # Require a fresh stable feedback observation at/after release and settle
      # time; wall-clock passage alone never completes a transaction.
      settled_after = max(self._stable_since, self._release_at) + self.settle_s
      if feedback.observed_at >= settled_after:
        self.pending = None
        self.state = TransactionState.IDLE
    return None  # A completion never also issues a second press in this call.

  def clear_after_external_confirmation(self, *, now: float, transport_idle_confirmed: bool,
                                        feedback: StockFeedback | None, release: NativeRelease | None) -> bool:
    """Explicit clearance only; this call cannot cancel or inspect a real lease.

    The caller must independently establish no queued/in-flight host requests
    and no running firmware press. A fresh raw idle GRA is insufficient.
    """
    self._clock(now)
    if self.state != TransactionState.BLOCKED or transport_idle_confirmed is not True:
      return False
    if not (self._fresh(feedback, now, self.feedback_max_age_s) and _valid_display_speed(feedback.speed)
            and self._fresh(release, now, self.release_max_age_s) and release.released is True
            and feedback.observed_at > self._blocked_at and release.observed_at > self._blocked_at):
      return False
    self.unresolved = None
    self.blocked_reason = self._blocked_at = None
    self.state = TransactionState.IDLE
    return True
