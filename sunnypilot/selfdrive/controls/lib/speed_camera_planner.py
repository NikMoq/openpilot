"""Legacy GPS-only camera ceilings; CAN odometry is handled by Resolver's current adapter."""

from __future__ import annotations

import math
import struct
from openpilot.sunnypilot.mapd.camera_gps_quality import camera_accuracy_acceptable

from openpilot.sunnypilot.selfdrive.controls.lib.speed_camera_controller import (
  CameraControlInput, CameraControlOutput, CameraControlState, CameraObservation, SpeedCameraController,
)


class SpeedCameraPlanner:
  def __init__(self):
    self.controller = SpeedCameraController()
    self.output = CameraControlOutput(state=CameraControlState.inactive)
    self._manual_until = 0.0
    self._speed_button_until = 0.0
    self._outside_speed_button_until = 0.0
    self._speed_buttons_held: set[str] = set()
    self._speed_button_camera_origin: dict[str, bool] = {}
    self._speed_button_release_until: dict[str, float] = {}
    self._resume_until = 0.0
    self._gas_until = 0.0
    self._brake_until = 0.0
    self._brake_observed = False
    self._pending_pass: CameraControlOutput | None = None
    self._pass_generation: float | None = None
    self._pending_pass_cap: float | None = None
    self._pending_button_override = False
    self._returning = False

  def observe_car_state(self, cs, now_s: float) -> None:
    # plannerd observes every carState, while model planning runs more slowly.
    # Latch a short physical tap so a model frame cannot miss the override.
    if cs.gasPressed:
      self._gas_until = now_s + 0.5
    if cs.brakePressed:
      self._brake_until = now_s + 0.5
      # Only an existing camera episode needs persistent brake/RES authority.
      # Ordinary braking before acquisition must not block a later SET session.
      if self.controller.episode_active or self._pending_pass is not None:
        self._brake_observed = True
    for button in cs.buttonEvents:
      button_type = str(button.type)
      if button_type in ('accelCruise', 'decelCruise'):
        camera_episode = self.controller.episode_active or self._pending_pass is not None
        if button.pressed:
          if button_type not in self._speed_buttons_held:
            self._speed_button_camera_origin[button_type] = camera_episode
          self._speed_buttons_held.add(button_type)
        else:
          if button_type not in self._speed_buttons_held and now_s > self._speed_button_release_until.get(button_type, 0.0):
            # An unpaired later release keeps the existing release-only policy;
            # an old episode's origin must not leak into a new driver action.
            self._speed_button_camera_origin[button_type] = camera_episode
          self._speed_buttons_held.discard(button_type)
          self._speed_button_release_until[button_type] = now_s + 0.5
        # Retain origin after release too: carState observation and planning
        # can consume the same release. A release is not a new driver decision.
        camera_origin = self._speed_button_camera_origin.get(button_type, camera_episode)
        self._speed_button_until = now_s + 0.5
        if not camera_origin:
          self._outside_speed_button_until = now_s + 0.5
      elif button_type == 'resumeCruise' and (self.controller.waiting_for_resume or self._brake_observed or
                                            now_s <= self._brake_until or now_s <= self._resume_until):
        self._resume_until = now_s + 0.5
      else:
        self._manual_until = now_s + 0.5

  def update(self, cs, cc, gps, lmd, *, enabled: bool, gps_valid: bool, gps_alive: bool,
             lmd_valid: bool, lmd_alive: bool, lmd_age_s: float, user_cruise_mps: float,
             minimum_speed_mps: float, now_s: float, adapt_accel_mps2: float,
             lmd_generation: float | None = None, camera_speed_offset_kph: float = 0) -> CameraControlOutput:
    self.observe_car_state(cs, now_s)
    gas_pressed = bool(cs.gasPressed or now_s <= self._gas_until)
    brake_pressed = bool(cs.brakePressed or self._brake_observed or now_s <= self._brake_until)
    brake_context = brake_pressed or self.controller.waiting_for_resume
    manual_button = now_s <= self._manual_until
    tracking = self.controller.episode_active or self._pending_pass is not None or self._returning
    engaged = bool(cc.enabled and cs.cruiseState.enabled)
    if not engaged and not brake_context and not (manual_button and tracking):
      self.controller = SpeedCameraController()
      self.output = CameraControlOutput(state=CameraControlState.inactive, reason="acc_disengaged")
      self._pending_pass = None
      self._pass_generation = None
      self._pending_pass_cap = None
      self._pending_button_override = False
      self._returning = False
      self._manual_until = self._gas_until = self._brake_until = 0.0
      self._speed_button_until = self._resume_until = 0.0
      self._outside_speed_button_until = 0.0
      self._brake_observed = False
      return self.output

    takeover = self.output.manual_takeover
    if not enabled and not tracking and not takeover:
      self.output = CameraControlOutput(state=CameraControlState.inactive, reason="disabled")
      return self.output

    speed_button = bool(self._speed_buttons_held) or now_s <= self._speed_button_until
    outside_speed_button = (now_s <= self._outside_speed_button_until or
                            any(not self._speed_button_camera_origin[button] for button in self._speed_buttons_held))
    # CC.override accompanies gas; CC.cancel accompanies brake disengagement.
    # Physical cancel/main-off still wins through manual_button. CC.resume is
    # also an automatic standstill command, never evidence of physical RES.
    attributed_override = gas_pressed or brake_pressed or (brake_context and not engaged)
    override = bool((cc.cruiseControl.override and not attributed_override) or
                    (cc.cruiseControl.cancel and not (brake_context and not engaged)) or
                    (self._returning and not enabled))
    manual = bool(manual_button or speed_button or gas_pressed or brake_pressed or override)
    if manual and not tracking and not takeover and not brake_context:
      self.output = CameraControlOutput(state=CameraControlState.inactive, reason="manual_control")
      return self.output

    version = getattr(lmd, "speedCameraControlVersion", 0)
    prefix = "speedCameraControl" if version == 1 else "speedCamera"
    kind = getattr(lmd, prefix + "Type", "")
    allowed = bool(getattr(lmd, prefix + "AutoControlAllowed", False))
    eligible = kind in ("speedcamonline:1", "speedcamonline:2", "speedcamonline:5")
    eligible |= (kind in ("speedcamonline-cg77:18059", "speedcamonline-cg77:18950", "speedcamonline-cg77:18958") and allowed)
    # Version 1 requires explicit validated selection; an empty selection or
    # unknown version must never fall back to the nearest warning. Version 0
    # retains the older iGo/type and CityGuide/parser-flag contract.
    valid = (bool(getattr(lmd, "speedCameraControlValid", False)) and allowed if version == 1 else
             version == 0 and bool(lmd.speedCameraAheadValid))
    raw_limit = getattr(lmd, prefix + "SpeedLimit", 0.0)
    camera = None
    # Compare the unchanged stock minimum in the same Float32 representation
    # as LiveMapDataSP, so a raw 30 km/h limit survives wire round-off.
    wire_minimum = (struct.unpack('<f', struct.pack('<f', minimum_speed_mps))[0]
                    if 0 < minimum_speed_mps <= 300 / 3.6 else math.inf)
    if valid and eligible and math.isfinite(raw_limit) and wire_minimum <= raw_limit <= 300 / 3.6:
      offset = camera_speed_offset_kph
      if (isinstance(offset, bool) or not isinstance(offset, (int, float)) or
          not math.isfinite(offset) or not 0 <= offset <= 30 or offset != int(offset)):
        offset = 0
      target = min(user_cruise_mps, raw_limit + offset / 3.6)
      lat, lon = getattr(lmd, prefix + "Latitude"), getattr(lmd, prefix + "Longitude")
      # Stable identity excludes offset. The controller may lower an active
      # episode's limit, but never raises it before confirmed passage/reset.
      camera = CameraObservation(latitude=lat, longitude=lon, speed_limit_mps=target,
                                 camera_id=f"{lat:.7f},{lon:.7f}:{kind}")

    # Message receipt time is monotonic; GPS time is Unix. The caller supplies
    # the receipt AGE, which can be safely converted to this Unix clock.
    accuracy_ok = camera_accuracy_acceptable(gps)
    data = CameraControlInput(
      enabled=enabled, acc_engaged=engaged,
      gps_valid=bool(gps_valid and gps.hasFix and accuracy_ok), gps_alive=gps_alive,
      lmd_valid=bool(lmd_valid and lmd.locationValid), lmd_alive=lmd_alive,
      now_s=now_s, gps_fix_time_s=gps.unixTimestampMillis * 1e-3,
      lmd_time_s=now_s - lmd_age_s if math.isfinite(lmd_age_s) and lmd_age_s >= 0 else None,
      latitude=gps.latitude, longitude=gps.longitude, heading_deg=gps.bearingDeg,
      user_cruise_mps=user_cruise_mps, camera=camera,
      # A NEW +/- press after completion is an ordinary driver change. The
      # release/latch of a press originating inside the episode stays temporary.
      manual_button=manual_button or (outside_speed_button and self._returning and
                                     not self.controller.episode_active and self._pending_pass is None),
      speed_button=speed_button, resume_requested=brake_context and now_s <= self._resume_until,
      pass_pending=self._pending_pass is not None,
      gas_pressed=gas_pressed, brake_pressed=brake_pressed, override=override,
    )
    previous = self.output
    saved_cap = self.controller.speed_cap_mps
    button_override = self.controller.button_override or (speed_button and self.controller.episode_active)
    output = self.controller.update(data, adapt_accel_mps2=adapt_accel_mps2)
    self._brake_observed = False
    if output.restore_allowed and camera is not None and camera.key != output.camera_id:
      # A second nearby camera can already be the nearest one at passage.
      # Evaluate it in the same frame, so there is no one-frame release.
      next_output = self.controller.update(data, adapt_accel_mps2=adapt_accel_mps2)
      if next_output.speed_cap_mps is not None or next_output.manual_takeover:
        output = next_output

    # GPS can confirm passage before the slower map publisher reports the next
    # camera. Keep the old ceiling until a NEW valid map message is evaluated.
    # Production uses the monotonic receipt timestamp as an unambiguous marker.
    generation = lmd_generation if lmd_generation is not None else data.lmd_time_s
    if self._pending_pass is not None and not output.manual_takeover and output.speed_cap_mps is None:
      reason = ("brake" if brake_pressed else "brake_waiting_resume" if self.controller.waiting_for_resume else
                "gas" if gas_pressed else "manual_button" if speed_button else "")
      if reason:
        self._pending_button_override |= speed_button
        output = CameraControlOutput(state=CameraControlState.manual, camera_id=self._pending_pass.camera_id,
                                     manual_takeover=True, reason=reason)
    if output.state == CameraControlState.manual:
      # Temporary authority loss must not discard the episode or pass barrier.
      self.output = output
      return output
    if output.manual_takeover:
      self._pending_pass = None
      self._pending_pass_cap = None
      self._pending_button_override = False
      self._returning = False
    elif output.restore_allowed:
      self._pending_pass = output
      self._pass_generation = generation
      self._pending_pass_cap = saved_cap if saved_cap is not None else previous.speed_cap_mps
      self._pending_button_override = button_override
      self._returning = True
      output = self._pending_output(user_cruise_mps)
    elif self._pending_pass is not None:
      if output.speed_cap_mps is not None:
        self._pending_pass = None
        self._pending_pass_cap = None
        self._pending_button_override = False
        self._returning = False
      else:
        fresh_map = (data.lmd_valid and data.lmd_alive and data.lmd_time_s is not None and
                     0 <= now_s - data.lmd_time_s <= self.controller.config.lmd_max_age_s)
        fresh_gps = (data.gps_valid and data.gps_alive and
                     0 <= now_s - data.gps_fix_time_s <= self.controller.config.gps_max_age_s and
                     output.reason not in ("gps_unreliable", "lmd_unreliable", "adapt_accel_invalid"))
        new_map = (generation is not None and math.isfinite(generation) and
                   (self._pass_generation is None or generation > self._pass_generation))
        if fresh_map and fresh_gps and new_map:
          output = self._pending_pass
          self._pending_pass = None
          self._pending_pass_cap = None
          self._pending_button_override = False
        else:
          output = self._pending_output(user_cruise_mps)
    elif output.speed_cap_mps is not None:
      self._returning = False

    # Passage releases a ceiling, but ICBM still needs time to raise the stock
    # ACC setpoint with '+' presses. Manual intervention during that return
    # must hand over the CURRENT stock setpoint, never the old saved maximum.
    stock_speed = getattr(cs.cruiseState, "speedCluster", cs.cruiseState.speed)
    if self._returning and self._pending_pass is None and not manual:
      if math.isfinite(stock_speed) and stock_speed >= user_cruise_mps - 0.5:
        self._returning = False
    self.output = output
    return self.output

  def _pending_output(self, user_cruise_mps: float) -> CameraControlOutput:
    if self._pending_button_override:
      return CameraControlOutput(state=CameraControlState.manual, camera_id=self._pending_pass.camera_id,
                                 manual_takeover=True, reason="manual_button")
    if self._pending_pass_cap is not None:
      self._pending_pass_cap = min(self._pending_pass_cap, user_cruise_mps)
    return CameraControlOutput(state=CameraControlState.hold, speed_cap_mps=self._pending_pass_cap,
                               camera_id=self._pending_pass.camera_id, reason="awaiting_map_update")
