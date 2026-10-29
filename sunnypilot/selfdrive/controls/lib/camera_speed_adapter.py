"""Temporary camera limit lifecycle. No Params, messaging, CAN, or button output.

Map matching owns route evidence; this adapter owns each event's control authority.
All freshness ages are supplied by the caller using the matching clock epoch.
"""
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class CameraLimit:
  event_id: str = ""
  state: str = "IDLE"
  reason: str = "idle"
  raw_limit: float = 0.0
  distance: float = 0.0
  countdown_valid: bool = False
  automatic: bool = False
  manual: bool = False


class CameraSpeedAdapter:
  def __init__(self):
    self.output = CameraLimit()
    self._event_id = ""
    self._raw_limit = 0.0
    self._distance = 0.0
    self._active = False
    self._manual = False
    self._suppressed: set[str] = set()
    self._manual_pending = False
    self._feedback_pending = False
    self._feedback_baseline = None
    self._pass_pending = False
    self._feedback_direction = 0
    self._speed_buttons_held = set()
    self._release_seen = set()
    self._resume_session = False
    self._acc_engaged_prev = False
    self._feedback_gesture = False
    self._feedback_kind = None
    self._feedback_changed_held = False
    self._feedback_follow = False
    self._automatic_target = None
    self.maximum_driver_speed_mps = None
    self._unsupported_driver_speed = False

  def set_automatic_target(self, target):
    self._automatic_target = target
    if self._feedback_follow and not self._feedback_gesture and not self._feedback_pending and self._feedback_baseline is not None:
      if math.isfinite(target) and (target - self._feedback_baseline) * self._feedback_direction > .01:
        self._feedback_follow = False

  def observe_car_state(self, cs, automatic_target=None):
    # The upstream vehicle interface owns event attribution. Never infer an
    # action merely from stock speed changing: that can be our own ICBM output.
    was_engaged = self._acc_engaged_prev
    if automatic_target is not None:
      self.set_automatic_target(automatic_target)
    engaged = bool(cs.cruiseState.enabled and not getattr(cs, "brakePressed", False))
    self._acc_engaged_prev = engaged
    if not engaged:
      self._feedback_follow = False
    if self._active and any(str(b.type) in ("accelCruise", "decelCruise", "setCruise", "cancel", "mainCruise")
                            for b in cs.buttonEvents):
      self._manual_pending = True
    action = None
    released = None
    if not cs.buttonEvents:
      self._release_seen.clear()
    for b in cs.buttonEvents:
      kind = str(b.type)
      if kind in ("cancel", "mainCruise"):
        self._feedback_pending = False  # Explicit driver escape, never a timeout.
        self._feedback_gesture = False
        self._feedback_follow = False
      if kind not in ("accelCruise", "decelCruise", "setCruise", "resumeCruise"):
        continue
      if kind == "resumeCruise" and b.pressed and kind not in self._speed_buttons_held:
        self._resume_session = not (was_engaged and engaged)
      session_resume = kind == "resumeCruise" and (self._resume_session or not (was_engaged and engaged))
      if b.pressed:
        if kind not in self._speed_buttons_held:
          action = None if session_resume else kind
        self._speed_buttons_held.add(kind)
        self._release_seen.discard(kind)
      else:
        if kind in self._speed_buttons_held and not session_resume:
          released = kind
        if kind not in self._speed_buttons_held and kind not in self._release_seen:
          action = None if session_resume else kind  # Preserve release-only events.
        self._speed_buttons_held.discard(kind)
        self._release_seen.add(kind)
        if kind == "resumeCruise":
          self._resume_session = False
    stock = float(cs.cruiseState.speedCluster)
    if action is not None and not self._active:
      self._feedback_follow = False
    if action is not None and (self._active or self._feedback_pending):
      self._manual_pending = True
      self._feedback_baseline = stock
      self._feedback_pending = True
      self._feedback_kind = action
      self._feedback_gesture = action in self._speed_buttons_held
      self._feedback_changed_held = self._feedback_follow = False
      self._feedback_direction = 1 if action in ("accelCruise", "resumeCruise") else -1
    if released == self._feedback_kind and self._feedback_gesture:
      self._feedback_gesture = False
      self._feedback_pending = not self._feedback_changed_held
      self._feedback_follow = self._feedback_changed_held
    supported = (self.maximum_driver_speed_mps is not None and math.isfinite(stock) and
                 0 < stock <= self.maximum_driver_speed_mps)
    self._unsupported_driver_speed = bool(self.maximum_driver_speed_mps is not None and math.isfinite(stock) and
                                          stock > self.maximum_driver_speed_mps)
    if (self._feedback_pending or self._feedback_gesture or self._feedback_follow) and not supported:
      # Native ACC may support values beyond the virtual planner's original
      # maximum. Preserve manual authority instead of restoring an old base.
      self._feedback_pending = True
    baseline_valid = self._feedback_baseline is not None and math.isfinite(self._feedback_baseline) and self._feedback_baseline > 0
    if (self._feedback_pending or self._feedback_gesture or self._feedback_follow) and supported and baseline_valid:
      delta = stock - self._feedback_baseline
      if abs(delta) > .01 and (self._feedback_direction == 0 or delta * self._feedback_direction > 0):
        self._feedback_pending = False
        self._feedback_changed_held |= self._feedback_gesture
        self._feedback_baseline = stock

  def update(self, lmd, *, enabled, reliable, engaged, reaction_distance):
    if not enabled:
      self._event_id = ""
      self._active = self._manual = self._manual_pending = False
      self._feedback_pending = self._pass_pending = False
      self._feedback_gesture = False
      self._feedback_follow = False
      self._feedback_baseline = None
      self._speed_buttons_held.clear()
      self._release_seen.clear()
      self.output = CameraLimit(reason="disabled")
      return self.output

    event_id = str(getattr(lmd, "speedCameraControlEventId", ""))
    version_ok = getattr(lmd, "speedCameraControlVersion", 0) == 1
    source_state = str(getattr(lmd, "speedCameraControlState", ""))
    distance = float(getattr(lmd, "speedCameraControlDistance", 0.0))
    raw = float(getattr(lmd, "speedCameraControlSpeedLimit", 0.0))
    valid = bool(reliable and version_ok and source_state in ("APPROACHING", "ACTIVE", "DRIVER_OVERRIDE") and
                 getattr(lmd, "speedCameraControlValid", False) and
                 getattr(lmd, "speedCameraControlAutoControlAllowed", False) and event_id and
                 math.isfinite(distance) and distance >= 0 and math.isfinite(raw) and 0 < raw <= 300 / 3.6)
    same = event_id == self._event_id
    if self._active and self._manual_pending:
      self._manual = True
    self._manual_pending = False

    # A missing candidate, stale position, or selection change is never proof
    # that the retained event has ended. Passage is explicitly tied to its ID.
    passed = bool(reliable and version_ok and source_state == "PASSED" and same and
                  getattr(lmd, "speedCameraControlPassProven", False))
    self._pass_pending |= self._active and passed
    if self._active and self._pass_pending and not self._feedback_pending and not self._feedback_gesture:
      self._suppressed.add(self._event_id)
      self.output = CameraLimit(event_id=self._event_id, state="PASSED", reason="route_pass_proven")
      self._event_id = ""
      self._active = self._manual = False
      self._pass_pending = False
      return self.output

    if not self._active and self._feedback_pending:
      reason = "driver_speed_outside_virtual_range" if self._unsupported_driver_speed else "awaiting_driver_feedback"
      self.output = CameraLimit(state="DRIVER_OVERRIDE", reason=reason, manual=True)
      return self.output
    if not self._active and valid and event_id not in self._suppressed:
      self._event_id, self._raw_limit, self._distance = event_id, raw, distance
      if distance <= reaction_distance and engaged:
        self._active = True
        same = True
      else:
        self.output = CameraLimit(event_id=event_id, state="APPROACHING", reason="outside_reaction_zone",
                                  raw_limit=raw, distance=distance)
        return self.output
    elif not self._active:
      self.output = CameraLimit(reason=getattr(lmd, "speedCameraControlReason", "no_camera") or "no_camera")
      return self.output

    if same and valid:
      self._distance = min(self._distance, distance)
      # A later increase or offset change must not release an active ceiling.
      self._raw_limit = min(self._raw_limit, raw)
    countdown = bool(same and valid and self._distance <= reaction_distance and not self._pass_pending)
    state = "DRIVER_OVERRIDE" if self._manual else "ACTIVE"
    reason = ("driver_speed_outside_virtual_range" if self._manual and self._feedback_pending and self._unsupported_driver_speed else
              "awaiting_driver_feedback" if self._manual and self._feedback_pending else "button_event" if self._manual else
              "camera_limit" if same and valid else "unreliable_hold")
    self.output = CameraLimit(self._event_id, state, reason, 0.0 if self._pass_pending else self._raw_limit, self._distance,
                              countdown, automatic=not self._manual, manual=self._manual)
    return self.output
