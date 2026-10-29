"""Pure joining of PQ selection transactions and passive raw TX-return evidence.

No CAN, network, firmware capability discovery, signing, or backend activation.
paired_firmware is external evidence and defaults false on every update. All
update/clear times use caller monotonic seconds; constructor timing values are
policy inputs, not proven ECU constants. Attach THIS coordinator, not .observer,
as CS.icbm_echo_observer: observe(timestamp_ns, address, payload, src) converts
raw interface nanoseconds to seconds. snapshot() returns that seconds-based echo
snapshot. .observer is internal seconds-based evidence; only this coordinator
should arm/disarm it.

ready/readiness_fresh describe active ACC, pedals and manual priority, NOT whether
sendButton is non-none. Target attainment must not interrupt a pending release.
target_eligible is the caller's minimum/range decision; targets are never clamped
here. Remembered driver/base speed belongs outside this class.

Local blocking cannot cancel an already issued firmware lease. External clearance
means independent confirmation of transport idle/lease end and actual fresh
release evidence. A future caller may verify that automatically after gas release
or driver reengagement; no manual UI reset is required or implemented here.
"""
import math

from .pq_button_echo import EchoState, PQButtonEchoObserver
from .pq_button_selection import PQButton
from .pq_button_transaction import NativeRelease, PQButtonTransaction, StockFeedback, TransactionState


class PQICBMCoordinator:
  def __init__(self, *, transaction_timeout_s: float, feedback_max_age_s: float,
               release_max_age_s: float, settle_s: float, echo_max_age_s: float,
               native_bus: int, returned_bus: int):
    self.transaction = PQButtonTransaction(transaction_timeout_s=transaction_timeout_s,
                                          feedback_max_age_s=feedback_max_age_s,
                                          release_max_age_s=release_max_age_s, settle_s=settle_s)
    self.observer = PQButtonEchoObserver(max_age=echo_max_age_s, native_bus=native_bus, returned_bus=returned_bus)
    self._blocked_reason = None
    self._blocked_at = None
    self._last_now = None
    self._last_firmware_request_id = None
    self._confirmed_idle_release = None
    self._no_press_clearance_id = None
    self._terminal_clearance_id = None
    self._terminal_clearance_at = None

  @property
  def state(self):
    return TransactionState.BLOCKED if self.blocked_reason else self.transaction.state

  @property
  def blocked_reason(self):
    return self._blocked_reason or self.transaction.blocked_reason

  def observe(self, timestamp_ns: int, address, payload, src):
    """Passive interface hook: integer nanoseconds in, echo seconds out."""
    if type(timestamp_ns) is not int or timestamp_ns < 0:
      if self._last_now is not None:
        self._block("invalid_observation_timestamp", self._last_now)
      raise ValueError("raw observation timestamp must be nonnegative integer nanoseconds")
    return self.observer.observe(timestamp_ns * 1e-9, address, payload, src)

  def snapshot(self):
    """Forward current echo evidence; all timestamp fields are seconds."""
    return self.observer.snapshot()

  def synchronize_request_sequence(self, request_id: int) -> bool:
    """Adopt the firmware's last ID only while both host leases are idle."""
    if self.observer.snapshot().state != EchoState.DISARMED:
      return False
    return self.transaction.synchronize_request_sequence(request_id)

  def _block(self, reason, now):
    if self._blocked_reason is None:
      self._blocked_reason, self._blocked_at = reason, now
    press = self.transaction.pending
    if press is not None:
      self.transaction.update(now=now, target=press.target, speed_unit=press.speed_unit,
                              ready=False, active=False, target_fresh=False, feedback=None, release=None)

  def _clock(self, now):
    if (not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now)
        or now < 0 or (self._last_now is not None and now < self._last_now)):
      if self._last_now is not None:
        self._block("invalid_clock", self._last_now)
      raise ValueError("now must be finite, nonnegative and monotonic")
    self._last_now = now

  def _native_release(self, snapshot, now):
    press = self.transaction.pending
    baseline = press is None and snapshot.state == EchoState.DISARMED
    owned = (press is not None and snapshot.state == EchoState.RELEASED
             and snapshot.expected_button == press.button and snapshot.issued_timestamp == press.issued_at
             and snapshot.release_return_timestamp is not None
             and snapshot.release_return_timestamp > press.issued_at)
    released = baseline or owned
    observed_at = snapshot.last_idle_return_timestamp if released else snapshot.pressed_return_timestamp
    if observed_at is None:
      return None
    fresh = 0 <= now - observed_at <= self.transaction.release_max_age_s
    # Use actual correlated idle-return delivery time, never now or the first
    # latched release forever. Continuing real idle echoes keep settling fresh.
    return NativeRelease(released, observed_at, fresh)

  def _clear_from_firmware_release(self, *, now, feedback, firmware_status):
    unresolved = self.transaction.unresolved
    if unresolved is None or firmware_status is None:
      return False
    # A guard refusal (reason 2) occurs before gra_request can queue a press.
    # Sequence/protocol rejection remains blocked; it is not a transient pedal
    # interruption and must not be converted into a blind retry.
    no_press = (firmware_status.get("state") == 7 or
                (firmware_status.get("state") == 4 and firmware_status.get("reason") == 2))
    if not (firmware_status.get("valid") is True
            and firmware_status.get("signature_matched") is True
            and firmware_status.get("protocol_valid") is True
            and firmware_status.get("panda_count") == 1
            and firmware_status.get("request_id") == unresolved.sequence
            and (firmware_status.get("state") in (3, 6) or no_press)
            and type(firmware_status.get("tx_counter")) is int
            and 0 <= firmware_status["tx_counter"] <= 15
            and type(firmware_status.get("status_mono_time_ns")) is int
            and firmware_status["status_mono_time_ns"] > 0):
      return False
    observed_at = firmware_status["status_mono_time_ns"] * 1e-9
    if no_press:
      # Firmware proves this lease never queued a pressed frame. Reset the
      # owned observer once, then require NEW matched native idle TX returns;
      # the status polling time itself is not a release observation.
      if self._no_press_clearance_id != unresolved.sequence:
        self.observer.disarm()
        self._no_press_clearance_id = unresolved.sequence
        return False
      snapshot = self.observer.snapshot()
      observed_at = snapshot.last_idle_return_timestamp
      if snapshot.state != EchoState.DISARMED or observed_at is None or observed_at <= self._blocked_at:
        return False
    elif observed_at <= self._blocked_at:
      snapshot = self.observer.snapshot()
      observer_owns_release = (snapshot.state == EchoState.RELEASED
                               and snapshot.expected_button == unresolved.button
                               and snapshot.issued_timestamp == unresolved.issued_at
                               and snapshot.release_return_timestamp is not None
                               and snapshot.last_idle_return_timestamp is not None
                               and snapshot.last_idle_return_timestamp > self._blocked_at)
      if not observer_owns_release:
        # A manual press can invalidate the owned observer just after firmware
        # COMPLETE, while the host is still settling. That older terminal
        # timestamp must not become a new release merely because it is polled.
        # Matching terminal lease proof permits abandoning owned observation;
        # NEW correlated native idle/TXOK evidence must then establish release.
        expected_button = {"UP": 1, "DOWN": 2, "SET": 3, "RES": 4}[unresolved.button]
        if firmware_status.get("button") != expected_button:
          return False
        if self._terminal_clearance_id != unresolved.sequence:
          self.observer.disarm()
          self._terminal_clearance_id = unresolved.sequence
          self._terminal_clearance_at = now
          return False
        snapshot = self.observer.snapshot()
        observed_at = snapshot.last_idle_return_timestamp
        if (snapshot.state != EchoState.DISARMED or observed_at is None
            or observed_at <= max(self._blocked_at, self._terminal_clearance_at)):
          return False
      else:
        observed_at = snapshot.last_idle_return_timestamp
    release = NativeRelease(True, observed_at, 0 <= now-observed_at <= self.transaction.release_max_age_s)
    cleared = self.clear_after_external_confirmation(
      now=now, transport_idle_confirmed=True, feedback=feedback, release=release)
    if cleared:
      self._confirmed_idle_release = release
      self._no_press_clearance_id = None
      self._terminal_clearance_id = None
      self._terminal_clearance_at = None
    return cleared

  def update(self, *, now: float, target: int | float, speed_unit: str,
             ready: bool, readiness_fresh: bool, active: bool, radar_fresh: bool,
             target_fresh: bool, target_eligible: bool, feedback: StockFeedback | None,
             paired_firmware: bool = False, firmware_status: dict | None = None) -> PQButton | None:
    """Return one named button only after arming its actual echo observer."""
    self._clock(now)
    if self.blocked_reason:
      if not self._clear_from_firmware_release(now=now, feedback=feedback, firmware_status=firmware_status):
        return None
    snapshot = self.observer.snapshot()
    if self.transaction.pending is None and firmware_status is not None:
      # On host restart, an older firmware lease can still own the bus. Wait
      # for its terminal status instead of issuing an impossible next ID.
      terminal = (firmware_status.get("valid") is True
                  and firmware_status.get("signature_matched") is True
                  and firmware_status.get("protocol_valid") is True
                  and firmware_status.get("panda_count") == 1
                  and firmware_status.get("state") in (0, 3, 4, 6, 7))
      if not terminal:
        return None
      self.synchronize_request_sequence(firmware_status.get("request_id"))
    if snapshot.state == EchoState.INVALID:
      self._block("echo_invalid:" + str(snapshot.reason), now)
      return None
    if (self.transaction.pending is None and firmware_status is not None
        and firmware_status.get("valid") is True and type(firmware_status.get("request_id")) is int):
      self._last_firmware_request_id = firmware_status["request_id"]
    press = self.transaction.pending
    if press is not None and (snapshot.expected_button != press.button or snapshot.issued_timestamp != press.issued_at
                              or snapshot.state == EchoState.DISARMED):
      self._block("echo_ownership_lost", now)
      return None
    if press is not None and firmware_status is not None:
      status_valid = (firmware_status.get("valid") is True
                      and firmware_status.get("signature_matched") is True
                      and firmware_status.get("protocol_valid") is True
                      and firmware_status.get("panda_count") == 1)
      status_id = firmware_status.get("request_id")
      status_state = firmware_status.get("state")
      status_button = firmware_status.get("button")
      if not status_valid:
        self._block("firmware_status_lost", now)
        return None
      if (type(status_id) is int and status_id == press.sequence and status_state in (4, 5, 6, 7)):
        self._block("firmware_transaction_rejected_or_aborted", now)
        return None
      if type(status_id) is int and status_id != press.sequence:
        if status_id != self._last_firmware_request_id or status_state in (1, 2, 5):
          self._block("firmware_request_ownership_mismatch", now)
          return None
      expected_button_id = {"UP": 1, "DOWN": 2, "SET": 3, "RES": 4}[press.button]
      if status_id == press.sequence and status_state in (1, 2, 3) and status_button != expected_button_id:
        self._block("firmware_button_mismatch", now)
        return None
    # Eligibility authorizes a new press only. Once this coordinator owns a
    # press, keep servicing its release transaction even if the planner drops
    # sendButton after reaching the target.
    transport_allowed = (paired_firmware is True and ready is True and readiness_fresh is True
                         and active is True and radar_fresh is True and target_fresh is True)
    if press is not None:
      if (target, speed_unit) != (press.target, press.speed_unit):
        self._block("target_or_unit_replaced", now)
        return None
      if not transport_allowed:
        self._block("pairing_readiness_or_eligibility_lost", now)
        return None
    allowed = transport_allowed and (press is not None or target_eligible is True)
    release = self._native_release(snapshot, now)
    if press is None and self._confirmed_idle_release is not None:
      confirmed = self._confirmed_idle_release
      fresh = (confirmed.fresh is True and 0 <= now-confirmed.observed_at <= self.transaction.release_max_age_s)
      if fresh:
        release = confirmed
      else:
        self._confirmed_idle_release = None
    if press is not None and firmware_status is not None:
      firmware_release_confirmed = (firmware_status.get("valid") is True
                                    and firmware_status.get("signature_matched") is True
                                    and firmware_status.get("protocol_valid") is True
                                    and firmware_status.get("panda_count") == 1
                                    and firmware_status.get("request_id") == press.sequence
                                    and firmware_status.get("state") == 3)
      if not firmware_release_confirmed and release is not None:
        release = NativeRelease(False, release.observed_at, release.fresh)
    issued = self.transaction.update(now=now, target=target, speed_unit=speed_unit,
                                     ready=allowed, active=allowed, target_fresh=target_fresh,
                                     feedback=feedback, release=release)
    if self.transaction.state == TransactionState.BLOCKED:
      self._block(self.transaction.blocked_reason, now)
      return None
    if issued is not None:
      try:
        self.observer.arm(issued.button, issued.issued_at)
      except (ValueError, RuntimeError):
        self._block("echo_arm_failed", now)
        return None
      return issued.button
    if press is not None and self.transaction.pending is None:
      # Successful feedback/release/settling completion; no new press this call.
      self.observer.disarm()
    return None

  def clear_after_external_confirmation(self, *, now: float, transport_idle_confirmed: bool,
                                        feedback: StockFeedback | None, release: NativeRelease | None) -> bool:
    """Allow independently verified automatic rearm; not a firmware cancel ACK.

    Caller must prove no outstanding queued/in-flight request or firmware lease.
    Supplied fresh release/feedback must be actual observations after blocking;
    a still-PRESSED/INVALID observer alone cannot supply that proof.
    """
    self._clock(now)
    if not self.blocked_reason or transport_idle_confirmed is not True:
      return False
    if self.transaction.state == TransactionState.BLOCKED:
      cleared = self.transaction.clear_after_external_confirmation(
        now=now, transport_idle_confirmed=transport_idle_confirmed, feedback=feedback, release=release)
    else:
      cleared = (feedback is not None and release is not None and feedback.fresh is True and release.fresh is True
                 and release.released is True and feedback.observed_at > self._blocked_at
                 and release.observed_at > self._blocked_at
                 and 0 <= now-feedback.observed_at <= self.transaction.feedback_max_age_s
                 and 0 <= now-release.observed_at <= self.transaction.release_max_age_s)
    if not cleared:
      return False
    self._blocked_reason = self._blocked_at = None
    self.observer.disarm()  # Explicit caller clearance; never an automatic INVALID reset.
    self._terminal_clearance_id = self._terminal_clearance_at = None
    return True
