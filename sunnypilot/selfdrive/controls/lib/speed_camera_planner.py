"""Adapt map/GPS messages to temporary camera ceilings, without vehicle I/O."""

from __future__ import annotations

import math

from openpilot.sunnypilot.selfdrive.controls.lib.speed_camera_controller import (
  CameraControlInput, CameraControlOutput, CameraControlState, CameraObservation, SpeedCameraController,
)


class SpeedCameraPlanner:
  def __init__(self):
    self.controller = SpeedCameraController()
    self.output = CameraControlOutput(state=CameraControlState.inactive)
    self._manual_until = 0.0
    self._gas_until = 0.0
    self._brake_until = 0.0
    self._pending_pass: CameraControlOutput | None = None
    self._pass_generation: float | None = None
    self._returning = False

  def observe_car_state(self, cs, now_s: float) -> None:
    # plannerd observes every carState, while model planning runs more slowly.
    # Latch a short physical tap so a model frame cannot miss the override.
    if cs.buttonEvents:
      self._manual_until = now_s + 0.5
    if cs.gasPressed:
      self._gas_until = now_s + 0.5
    if cs.brakePressed:
      self._brake_until = now_s + 0.5

  def update(self, cs, cc, gps, lmd, *, enabled: bool, gps_valid: bool, gps_alive: bool,
             lmd_valid: bool, lmd_alive: bool, lmd_age_s: float, user_cruise_mps: float,
             minimum_speed_mps: float, now_s: float, adapt_accel_mps2: float,
             lmd_generation: float | None = None) -> CameraControlOutput:
    engaged = bool(cc.enabled and cs.cruiseState.enabled)
    if not engaged:
      self.controller = SpeedCameraController()
      self.output = CameraControlOutput(state=CameraControlState.inactive, reason="acc_disengaged")
      self._pending_pass = None
      self._pass_generation = None
      self._returning = False
      self._manual_until = self._gas_until = self._brake_until = 0.0
      return self.output

    tracking = self.output.speed_cap_mps is not None or self._returning
    takeover = self.output.manual_takeover
    if not enabled and not tracking and not takeover:
      self.output = CameraControlOutput(state=CameraControlState.inactive, reason="disabled")
      return self.output

    manual_button = now_s <= self._manual_until
    gas_pressed = bool(cs.gasPressed or now_s <= self._gas_until)
    brake_pressed = bool(cs.brakePressed or now_s <= self._brake_until)
    manual = bool(manual_button or gas_pressed or brake_pressed or cc.cruiseControl.override or
                  cc.cruiseControl.cancel or cc.cruiseControl.resume)
    if manual and not tracking and not takeover:
      self.output = CameraControlOutput(state=CameraControlState.inactive, reason="manual_control")
      return self.output

    kind = lmd.speedCameraType
    eligible = kind in ("speedcamonline:1", "speedcamonline:2", "speedcamonline:5")
    # CityGuide needs the parser's direction/type validation as well as an
    # eligible point-camera type. Older map publishers default this flag false.
    eligible |= (kind in ("speedcamonline-cg77:18059", "speedcamonline-cg77:18950", "speedcamonline-cg77:18958") and
                 bool(getattr(lmd, "speedCameraAutoControlAllowed", False)))
    camera = None
    if lmd.speedCameraAheadValid and eligible and minimum_speed_mps <= lmd.speedCameraSpeedLimit <= 300 / 3.6:
      camera = CameraObservation(latitude=lmd.speedCameraLatitude, longitude=lmd.speedCameraLongitude,
                                 speed_limit_mps=lmd.speedCameraSpeedLimit,
                                 camera_id=f"{lmd.speedCameraLatitude:.7f},{lmd.speedCameraLongitude:.7f}:{kind}")

    # Message receipt time is monotonic; GPS time is Unix. The caller supplies
    # the receipt AGE, which can be safely converted to this Unix clock.
    accuracy_ok = (math.isfinite(gps.horizontalAccuracy) and 0 < gps.horizontalAccuracy <= 20 and
                   math.isfinite(gps.bearingAccuracyDeg) and 0 <= gps.bearingAccuracyDeg <= 30)
    data = CameraControlInput(
      enabled=enabled, acc_engaged=engaged,
      gps_valid=bool(gps_valid and gps.hasFix and accuracy_ok), gps_alive=gps_alive,
      lmd_valid=bool(lmd_valid and lmd.locationValid), lmd_alive=lmd_alive,
      now_s=now_s, gps_fix_time_s=gps.unixTimestampMillis * 1e-3,
      lmd_time_s=now_s - lmd_age_s if math.isfinite(lmd_age_s) and lmd_age_s >= 0 else None,
      latitude=gps.latitude, longitude=gps.longitude, heading_deg=gps.bearingDeg,
      user_cruise_mps=user_cruise_mps, camera=camera,
      manual_button=manual_button, gas_pressed=gas_pressed, brake_pressed=brake_pressed,
      override=bool(cc.cruiseControl.override or cc.cruiseControl.cancel or cc.cruiseControl.resume or
                    (self._returning and not enabled)),
    )
    previous = self.output
    output = self.controller.update(data, adapt_accel_mps2=adapt_accel_mps2)
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
    if output.manual_takeover:
      self._pending_pass = None
      self._returning = False
    elif output.restore_allowed:
      self._pending_pass = output
      self._pass_generation = generation
      self._returning = True
      output = CameraControlOutput(state=CameraControlState.hold, speed_cap_mps=previous.speed_cap_mps,
                                   camera_id=output.camera_id, reason="awaiting_map_update")
    elif self._pending_pass is not None:
      if output.speed_cap_mps is not None:
        self._pending_pass = None
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
        else:
          output = CameraControlOutput(state=CameraControlState.hold, speed_cap_mps=previous.speed_cap_mps,
                                       camera_id=self._pending_pass.camera_id, reason="awaiting_map_update")
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
