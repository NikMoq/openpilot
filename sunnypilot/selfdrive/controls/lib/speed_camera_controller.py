"""Pure point-camera speed ceilings; no messaging, Params, CAN, or file access.

The caller owns the driver's cruise setpoint and combines this ceiling with
other planner constraints using min(). Never write the ceiling back into that
setpoint. On manual_takeover, stop this episode's automatic button authority;
do not restore a saved setpoint. Only restore_allowed permits a post-pass
return, still bounded by user_cruise_mps and all other planner constraints.

All timestamps are seconds on the SAME Unix clock. LMD time is its reception
time recorded on that clock, not SubMaster's monotonic recv_time. Acquisition
requires fresh, alive, valid GPS and LMD. An existing ceiling survives their
loss, including a missing camera observation. There is no timeout release.

Geometric association uses a straight approach corridor, not a road graph.
The caller must supply an eligible point camera, not an average-speed section.
Nearby parallel roads inside that corridor remain ambiguous without road data.
Two distinct fresh GPS fixes behind the saved camera confirm passage; fixes
beyond the configured passage window cannot automatically release the ceiling.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum


EARTH_RADIUS_M = 6_371_000.0


class CameraControlState(StrEnum):
  inactive = "inactive"
  approach = "approach"
  hold = "hold"
  cancelled = "cancelled"
  passed = "passed"


@dataclass(frozen=True, kw_only=True)
class CameraObservation:
  latitude: float
  longitude: float
  speed_limit_mps: float | None
  camera_id: str | None = None
  direction_deg: float | None = None
  direction_type: int = 0  # 0: any, 1: travel course, 2: travel course and its reverse
  is_average_speed_section: bool = False

  @property
  def key(self) -> str:
    return self.camera_id or f"{self.latitude:.7f},{self.longitude:.7f}"


@dataclass(frozen=True, kw_only=True)
class CameraControlInput:
  # Fail closed until the caller explicitly provides every eligibility flag.
  enabled: bool = False
  acc_engaged: bool = False
  gps_valid: bool = False
  gps_alive: bool = False
  lmd_valid: bool = False
  lmd_alive: bool = False
  now_s: float = 0.0
  gps_fix_time_s: float | None = None
  lmd_time_s: float | None = None
  latitude: float | None = None
  longitude: float | None = None
  heading_deg: float | None = None
  user_cruise_mps: float = 0.0
  camera: CameraObservation | None = None
  manual_button: bool = False
  gas_pressed: bool = False
  brake_pressed: bool = False
  override: bool = False


@dataclass(frozen=True, kw_only=True)
class CameraControlOutput:
  state: CameraControlState
  speed_cap_mps: float | None = None
  camera_id: str | None = None
  restore_allowed: bool = False
  manual_takeover: bool = False
  reason: str = ""


@dataclass(frozen=True, kw_only=True)
class CameraControlConfig:
  gps_max_age_s: float = 2.5
  lmd_max_age_s: float = 2.5
  corridor_half_width_m: float = 20.0
  heading_tolerance_deg: float = 30.0
  lookahead_m: float = 5_000.0
  pass_margin_m: float = 15.0
  pass_window_m: float = 250.0

  def __post_init__(self) -> None:
    values = (self.gps_max_age_s, self.lmd_max_age_s, self.corridor_half_width_m,
              self.heading_tolerance_deg, self.lookahead_m, self.pass_margin_m, self.pass_window_m)
    if not all(_finite(value) and value > 0 for value in values):
      raise ValueError("camera geometry and freshness bounds must be finite and positive")
    if self.heading_tolerance_deg >= 90 or self.pass_margin_m >= self.pass_window_m:
      raise ValueError("invalid heading tolerance or passage window")


def _finite(value: float | None) -> bool:
  return value is not None and math.isfinite(value)


def _position_valid(latitude: float | None, longitude: float | None) -> bool:
  return (_finite(latitude) and _finite(longitude) and
          -90 <= latitude <= 90 and -180 <= longitude <= 180)


def _heading_delta(first: float, second: float) -> float:
  return abs((first - second + 180.0) % 360.0 - 180.0)


def _fresh(now_s: float, timestamp_s: float | None, max_age_s: float) -> bool:
  return _finite(now_s) and _finite(timestamp_s) and 0 <= now_s - timestamp_s <= max_age_s


def _camera_geometry(camera: CameraObservation, latitude: float, longitude: float, heading_deg: float) -> tuple[float, float]:
  """Signed camera distance ahead and cross-track offset in the saved corridor."""
  latitude_radians = math.radians(camera.latitude)
  north = EARTH_RADIUS_M * math.radians(camera.latitude - latitude)
  longitude_delta = (camera.longitude - longitude + 180.0) % 360.0 - 180.0
  east = EARTH_RADIUS_M * math.cos(latitude_radians) * math.radians(longitude_delta)
  heading = math.radians(heading_deg)
  return north * math.cos(heading) + east * math.sin(heading), east * math.cos(heading) - north * math.sin(heading)


class SpeedCameraController:
  def __init__(self, config: CameraControlConfig | None = None):
    self.config = config or CameraControlConfig()
    self._camera: CameraObservation | None = None
    self._heading_deg = 0.0
    self._limit_mps = 0.0
    self._cap_mps: float | None = None
    self._last_fix_time_s: float | None = None
    self._behind_fixes = 0
    self._suppressed_ids: set[str] = set()
    self._manual_takeover = False

  def _clear_track(self) -> None:
    self._camera = None
    self._cap_mps = None
    self._last_fix_time_s = None
    self._behind_fixes = 0

  def _cancel(self, data: CameraControlInput, reason: str) -> CameraControlOutput:
    camera = self._camera or data.camera
    if camera is not None and _position_valid(camera.latitude, camera.longitude):
      self._suppressed_ids.add(camera.key)
    camera_id = camera.key if camera is not None and _position_valid(camera.latitude, camera.longitude) else None
    self._clear_track()
    self._manual_takeover = True
    return CameraControlOutput(state=CameraControlState.cancelled, camera_id=camera_id,
                               manual_takeover=True, reason=reason)

  def _idle(self, reason: str) -> CameraControlOutput:
    return CameraControlOutput(state=CameraControlState.cancelled if self._manual_takeover else CameraControlState.inactive,
                               manual_takeover=self._manual_takeover, reason=reason)

  def _hold(self, reason: str) -> CameraControlOutput:
    return CameraControlOutput(state=CameraControlState.hold, speed_cap_mps=self._cap_mps,
                               camera_id=self._camera.key, reason=reason)

  def update(self, data: CameraControlInput, *, adapt_accel_mps2: float) -> CameraControlOutput:
    """Return a ceiling, never an acceleration command.

    Pass the existing planner LIMIT_ADAPT_ACC unchanged as adapt_accel_mps2
    (finite and negative). Caller-provided manual actions cancel this camera
    for this controller's lifetime; a different camera may start a new episode.
    Construct a new controller for a new trip. A cleared camera observation,
    loss of GPS/LMD, or elapsed time alone never completes an active episode.
    """
    for action, reason in ((data.manual_button, "manual_button"), (data.gas_pressed, "gas"),
                           (data.brake_pressed, "brake"), (data.override, "override")):
      if action:
        return self._cancel(data, reason)

    if not data.enabled or not data.acc_engaged:
      reason = "disabled" if not data.enabled else "acc_disengaged"
      return self._cancel(data, reason) if self._camera is not None else self._idle(reason)

    if not _finite(data.user_cruise_mps) or data.user_cruise_mps <= 0:
      return self._cancel(data, "user_cruise_invalid") if self._camera is not None else self._idle("user_cruise_invalid")

    if self._cap_mps is not None:
      self._cap_mps = min(self._cap_mps, data.user_cruise_mps)

    gps_ok = (data.gps_valid and data.gps_alive and
              _fresh(data.now_s, data.gps_fix_time_s, self.config.gps_max_age_s) and
              _position_valid(data.latitude, data.longitude) and _finite(data.heading_deg))
    lmd_ok = data.lmd_valid and data.lmd_alive and _fresh(data.now_s, data.lmd_time_s, self.config.lmd_max_age_s)
    if not gps_ok or not lmd_ok:
      # A stale interval breaks consecutive passage confirmations, not the cap.
      self._behind_fixes = 0
      reason = "gps_unreliable" if not gps_ok else "lmd_unreliable"
      return self._hold(reason) if self._camera is not None else self._idle(reason)

    if not _finite(adapt_accel_mps2) or adapt_accel_mps2 >= 0:
      return self._hold("adapt_accel_invalid") if self._camera is not None else self._idle("adapt_accel_invalid")

    camera = data.camera
    if self._camera is None:
      if camera is None or not _position_valid(camera.latitude, camera.longitude):
        return self._idle("camera_missing")
      if camera.key in self._suppressed_ids:
        return self._idle("camera_suppressed")
      if camera.is_average_speed_section:
        return self._idle("average_speed_unsupported")
      if not _finite(camera.speed_limit_mps) or camera.speed_limit_mps <= 0:
        return self._idle("limit_unknown")
      if camera.speed_limit_mps >= data.user_cruise_mps:
        return self._idle("below_camera_limit")
      if camera.direction_type not in (0, 1, 2):
        return self._idle("direction_unknown")
      if camera.direction_type:
        if not _finite(camera.direction_deg):
          return self._idle("direction_unknown")
        delta = _heading_delta(data.heading_deg, camera.direction_deg)
        if camera.direction_type == 2:
          delta = min(delta, abs(180.0 - delta))
        if delta > self.config.heading_tolerance_deg:
          return self._idle("opposite_direction")
      along, cross = _camera_geometry(camera, data.latitude, data.longitude, data.heading_deg)
      if along <= 0 or math.hypot(along, cross) > self.config.lookahead_m:
        return self._idle("camera_not_ahead")
      if abs(cross) > self.config.corridor_half_width_m:
        return self._idle("outside_corridor")
      self._camera = camera
      self._limit_mps = camera.speed_limit_mps
      self._heading_deg = data.heading_deg % 360.0
      self._manual_takeover = False

    # Keep the original coordinates and approach axis until passage. A nearest
    # camera change or new heading cannot turn an ahead point into a passed one.
    along, cross = _camera_geometry(self._camera, data.latitude, data.longitude, self._heading_deg)
    if (abs(cross) > self.config.corridor_half_width_m or
        _heading_delta(data.heading_deg, self._heading_deg) > self.config.heading_tolerance_deg):
      self._behind_fixes = 0
      return self._hold("path_ambiguous")

    if self._last_fix_time_s is not None and data.gps_fix_time_s < self._last_fix_time_s:
      self._behind_fixes = 0
      return self._hold("gps_out_of_order")

    if data.gps_fix_time_s != self._last_fix_time_s:
      self._last_fix_time_s = data.gps_fix_time_s
      behind = self.config.pass_margin_m <= -along <= self.config.pass_window_m
      self._behind_fixes = self._behind_fixes + 1 if behind else 0

    if self._behind_fixes >= 2:
      camera_id = self._camera.key
      self._suppressed_ids.add(camera_id)
      self._clear_track()
      return CameraControlOutput(state=CameraControlState.passed, camera_id=camera_id,
                                 restore_allowed=True, reason="geometric_pass")

    if (camera is not None and _position_valid(camera.latitude, camera.longitude) and camera.key == self._camera.key and
        _finite(camera.speed_limit_mps) and camera.speed_limit_mps > 0):
      if camera.is_average_speed_section:
        return self._cancel(data, "average_speed_unsupported")
      self._limit_mps = min(self._limit_mps, camera.speed_limit_mps)

    allowed_mps = math.sqrt(self._limit_mps ** 2 - 2.0 * adapt_accel_mps2 * max(0.0, along))
    previous_cap = self._cap_mps if self._cap_mps is not None else data.user_cruise_mps
    self._cap_mps = min(previous_cap, allowed_mps, data.user_cruise_mps)
    state = CameraControlState.approach if along > 0 else CameraControlState.hold
    return CameraControlOutput(state=state, speed_cap_mps=self._cap_mps, camera_id=self._camera.key,
                               reason="kinematic_ceiling" if along > 0 else "pass_unconfirmed")
