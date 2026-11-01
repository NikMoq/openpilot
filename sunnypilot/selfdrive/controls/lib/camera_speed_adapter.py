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
  """Own one camera event; physical input never creates a persistent override.

  The existing ICBM readiness gate pauses transmission for physical buttons,
  pedals and disengaged ACC. Camera authority resumes after that temporary
  pause, while the cruise helper preserves the driver's pre-camera target.
  """

  def __init__(self):
    self.output = CameraLimit()
    self._event_id = ""
    self._raw_limit = 0.0
    self._distance = 0.0
    self._active = False
    self._suppressed: set[str] = set()
    # Kept as interface compatibility for the planner. The existing ICBM
    # target-validity and native minimum/maximum checks remain authoritative.
    self.maximum_driver_speed_mps = None

  def set_automatic_target(self, target):
    # No native speed movement is attributed to a physical camera override.
    # The planner still calls this hook after ordinary target arbitration.
    pass

  def observe_car_state(self, cs, automatic_target=None):
    # Physical input is handled by the existing ICBM readiness state machine.
    # In-zone buttons must not change the camera event's lifetime/authority.
    pass

  def _clear_event(self):
    self._event_id = ""
    self._raw_limit = self._distance = 0.0
    self._active = False

  def update(self, lmd, *, enabled, reliable, engaged, reaction_distance, unreliable_reason="unreliable_hold",
             odometry_reliable=False, odometry_pass_reliable=False):
    if not enabled:
      self._clear_event()
      self.output = CameraLimit(reason="disabled")
      return self.output

    event_id = str(getattr(lmd, "speedCameraControlEventId", ""))
    version_ok = getattr(lmd, "speedCameraControlVersion", 0) == 1
    source_reason = str(getattr(lmd, "speedCameraControlReason", "no_camera") or "no_camera")
    diagnostic = (unreliable_reason if not reliable else "camera_contract_invalid" if not version_ok else source_reason)
    source_state = str(getattr(lmd, "speedCameraControlState", ""))
    distance = float(getattr(lmd, "speedCameraControlDistance", 0.0))
    raw = float(getattr(lmd, "speedCameraControlSpeedLimit", 0.0))
    same = bool(self._event_id and event_id == self._event_id)
    odometry_message = source_reason in ("odometry_tracking", "odometry_passed")
    metadata_valid = bool(version_ok and same and getattr(lmd, "speedCameraControlAutoControlAllowed", False) and
                          math.isfinite(raw) and 0 < raw <= 300 / 3.6)
    odometry_tracking = bool(odometry_reliable and metadata_valid and source_reason == "odometry_tracking" and
                             source_state in ("APPROACHING", "ACTIVE") and math.isfinite(distance) and distance >= 0 and
                             getattr(lmd, "speedCameraControlValid", False) and not getattr(lmd, "speedCameraControlPassProven", False))
    odometry_passed = bool(odometry_pass_reliable and metadata_valid and source_reason == "odometry_passed" and
                           source_state == "PASSED" and math.isfinite(distance) and distance <= 0 and
                           not getattr(lmd, "speedCameraControlValid", False) and getattr(lmd, "speedCameraControlPassProven", False))
    if odometry_message and metadata_valid and not (odometry_tracking or odometry_passed):
      diagnostic = unreliable_reason
    gps_authority = reliable and not odometry_message
    valid = bool((gps_authority or odometry_tracking) and version_ok and source_state in ("APPROACHING", "ACTIVE", "DRIVER_OVERRIDE") and
                 getattr(lmd, "speedCameraControlValid", False) and
                 getattr(lmd, "speedCameraControlAutoControlAllowed", False) and event_id and
                 math.isfinite(distance) and distance >= 0 and math.isfinite(raw) and 0 < raw <= 300 / 3.6)
    passed = bool(odometry_passed or (gps_authority and version_ok and source_state == "PASSED" and same and
                                     getattr(lmd, "speedCameraControlPassProven", False)))
    if (self._active or odometry_passed) and passed:
      # Passage completes this camera even if a physical speed button is held.
      # Any temporary button/pedal transmission pause belongs to ICBM, not an
      # expired camera or a waiting-for-stock-feedback latch.
      self._suppressed.add(self._event_id)
      self.output = CameraLimit(event_id=self._event_id, state="PASSED",
                                reason="odometry_passed" if odometry_passed else "route_pass_proven")
      self._clear_event()
      return self.output

    if not self._active and valid and event_id not in self._suppressed:
      self._event_id, self._raw_limit, self._distance = event_id, raw if gps_authority else self._raw_limit, distance
      if distance <= reaction_distance and engaged:
        self._active = True
        same = True
      else:
        self.output = CameraLimit(event_id=event_id, state="APPROACHING", reason="outside_reaction_zone",
                                  raw_limit=raw, distance=distance)
        return self.output
    elif not self._active:
      self.output = CameraLimit(reason=diagnostic)
      return self.output

    # An uncertain fix or a different candidate is not proof of passage. Keep
    # the established ceiling, hide untrustworthy distance, and await the source
    # event's explicit route-backed end rather than inventing a timeout release.
    if same and valid:
      self._distance = min(self._distance, distance)
      if gps_authority:
        self._raw_limit = min(self._raw_limit, raw)
    countdown = bool(same and valid and self._distance <= reaction_distance)
    hold_reason = diagnostic if diagnostic not in ("no_camera", "matched", "offline_approach", "route_associated") else "unreliable_hold"
    active_reason = "odometry_tracking" if odometry_tracking else "camera_limit"
    self.output = CameraLimit(self._event_id, "ACTIVE", active_reason if same and valid else hold_reason,
                              self._raw_limit, self._distance, countdown, automatic=True)
    return self.output
