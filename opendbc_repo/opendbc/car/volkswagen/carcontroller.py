import numpy as np
from opendbc.can import CANPacker
from opendbc.car import Bus, DT_CTRL, structs
from opendbc.car.lateral import apply_driver_steer_torque_limits
from opendbc.car.common.conversions import Conversions as CV
from opendbc.car.interfaces import CarControllerBase
from opendbc.car.volkswagen import mqbcan, pqcan
from opendbc.car.volkswagen.values import CanBus, CarControllerParams, VolkswagenFlags
from opendbc.sunnypilot.car.volkswagen.icbm import IntelligentCruiseButtonManagementInterface
from opendbc.sunnypilot.car.volkswagen.pq_icbm_coordinator import PQICBMCoordinator, StockFeedback

VisualAlert = structs.CarControl.HUDControl.VisualAlert
LongCtrlState = structs.CarControl.Actuators.LongControlState


class CarController(CarControllerBase, IntelligentCruiseButtonManagementInterface):
  # Explicit host observation policy. These freshness/deadline values bound
  # stale data; they are not claims about ECU button acceptance timing.
  ICBM_INPUT_MAX_AGE_S = 0.12
  ICBM_FEEDBACK_MAX_AGE_S = 0.12
  ICBM_RELEASE_MAX_AGE_S = 0.12
  ICBM_TRANSACTION_TIMEOUT_S = 1.0
  ICBM_SETTLE_S = 0.08
  ICBM_ECHO_MAX_AGE_S = 0.5

  def __init__(self, dbc_names, CP, CP_SP):
    CarControllerBase.__init__(self, dbc_names, CP, CP_SP)
    IntelligentCruiseButtonManagementInterface.__init__(self, CP, CP_SP)
    self.CCP = CarControllerParams(CP)
    self.CAN = CanBus(CP)
    self.CCS = pqcan if CP.flags & VolkswagenFlags.PQ else mqbcan
    self.packer_pt = CANPacker(dbc_names[Bus.pt])
    self.aeb_available = not CP.flags & VolkswagenFlags.PQ

    self.apply_torque_last = 0
    self.gra_acc_counter_last = None
    self.eps_timer_soft_disable_alert = False
    self.hca_frame_timer_running = 0
    self.hca_frame_same_torque = 0
    self.icbm_coordinator = None
    self.icbm_command = None
    if CP.flags & VolkswagenFlags.PQ:
      self.icbm_coordinator = PQICBMCoordinator(
        transaction_timeout_s=self.ICBM_TRANSACTION_TIMEOUT_S,
        feedback_max_age_s=self.ICBM_FEEDBACK_MAX_AGE_S,
        release_max_age_s=self.ICBM_RELEASE_MAX_AGE_S,
        settle_s=self.ICBM_SETTLE_S,
        echo_max_age_s=self.ICBM_ECHO_MAX_AGE_S,
        native_bus=0,
        returned_bus=130,
      )

  def update(self, CC, CC_SP, CS, now_nanos):
    actuators = CC.actuators
    hud_control = CC.hudControl
    can_sends = []
    self.icbm_command = None

    # **** Steering Controls ************************************************ #

    if self.frame % self.CCP.STEER_STEP == 0:
      # Logic to avoid HCA state 4 "refused":
      #   * Don't steer unless HCA is in state 3 "ready" or 5 "active"
      #   * Don't steer at standstill
      #   * Don't send > 3.00 Newton-meters torque
      #   * Don't send the same torque for > 6 seconds
      #   * Don't send uninterrupted steering for > 360 seconds
      # MQB racks reset the uninterrupted steering timer after a single frame
      # of HCA disabled; this is done whenever output happens to be zero.

      if CC.latActive:
        new_torque = int(round(actuators.torque * self.CCP.STEER_MAX))
        apply_torque = apply_driver_steer_torque_limits(new_torque, self.apply_torque_last, CS.out.steeringTorque, self.CCP)
        self.hca_frame_timer_running += self.CCP.STEER_STEP
        if self.apply_torque_last == apply_torque:
          self.hca_frame_same_torque += self.CCP.STEER_STEP
          if self.hca_frame_same_torque > self.CCP.STEER_TIME_STUCK_TORQUE / DT_CTRL:
            apply_torque -= (1, -1)[apply_torque < 0]
            self.hca_frame_same_torque = 0
        else:
          self.hca_frame_same_torque = 0
        hca_enabled = abs(apply_torque) > 0
      else:
        hca_enabled = False
        apply_torque = 0

      if not hca_enabled:
        self.hca_frame_timer_running = 0

      self.eps_timer_soft_disable_alert = self.hca_frame_timer_running > self.CCP.STEER_TIME_ALERT / DT_CTRL
      self.apply_torque_last = apply_torque
      can_sends.append(self.CCS.create_steering_control(self.packer_pt, self.CAN.pt, apply_torque, hca_enabled))

      if self.CP.flags & VolkswagenFlags.STOCK_HCA_PRESENT:
        # Pacify VW Emergency Assist driver inactivity detection by changing its view of driver steering input torque
        # to the greatest of actual driver input or 2x openpilot's output (1x openpilot output is not enough to
        # consistently reset inactivity detection on straight level roads). See commaai/openpilot#23274 for background.
        ea_simulated_torque = float(np.clip(apply_torque * 2, -self.CCP.STEER_MAX, self.CCP.STEER_MAX))
        if abs(CS.out.steeringTorque) > abs(ea_simulated_torque):
          ea_simulated_torque = CS.out.steeringTorque
        can_sends.append(self.CCS.create_eps_update(self.packer_pt, self.CAN.cam, CS.eps_stock_values, ea_simulated_torque))

    # **** Acceleration Controls ******************************************** #

    if self.CP.openpilotLongitudinalControl:
      if self.frame % self.CCP.ACC_CONTROL_STEP == 0:
        acc_control = self.CCS.acc_control_value(CS.out.cruiseState.available, CS.out.accFaulted, CC.longActive)
        accel = float(np.clip(actuators.accel, self.CCP.ACCEL_MIN, self.CCP.ACCEL_MAX) if CC.longActive else 0)
        stopping = actuators.longControlState == LongCtrlState.stopping
        starting = actuators.longControlState == LongCtrlState.pid and (CS.esp_hold_confirmation or CS.out.vEgo < self.CP.vEgoStopping)
        can_sends.extend(self.CCS.create_acc_accel_control(self.packer_pt, self.CAN.pt, CS.acc_type, CC.longActive, accel,
                                                           acc_control, stopping, starting, CS.esp_hold_confirmation))

      #if self.aeb_available:
      #  if self.frame % self.CCP.AEB_CONTROL_STEP == 0:
      #    can_sends.append(self.CCS.create_aeb_control(self.packer_pt, False, False, 0.0))
      #  if self.frame % self.CCP.AEB_HUD_STEP == 0:
      #    can_sends.append(self.CCS.create_aeb_hud(self.packer_pt, False, False))

    # **** HUD Controls ***************************************************** #

    if self.frame % self.CCP.LDW_STEP == 0:
      hud_alert = 0
      if hud_control.visualAlert in (VisualAlert.steerRequired, VisualAlert.ldw):
        hud_alert = self.CCP.LDW_MESSAGES["laneAssistTakeOver"]
      can_sends.append(self.CCS.create_lka_hud_control(self.packer_pt, self.CAN.pt, CS.ldw_stock_values, CC.latActive,
                                                       CS.out.steeringPressed, hud_alert, hud_control))

    if self.frame % self.CCP.ACC_HUD_STEP == 0 and self.CP.openpilotLongitudinalControl:
      lead_distance = 0
      if hud_control.leadVisible and self.frame * DT_CTRL > 1.0:  # Don't display lead until we know the scaling factor
        lead_distance = 512 if CS.upscale_lead_car_signal else 8
      acc_hud_status = self.CCS.acc_hud_status_value(CS.out.cruiseState.available, CS.out.accFaulted, CC.longActive)
      # FIXME: PQ may need to use the on-the-wire mph/kmh toggle to fix rounding errors
      # FIXME: Detect clusters with vEgoCluster offsets and apply an identical vCruiseCluster offset
      set_speed = hud_control.setSpeed * CV.MS_TO_KPH
      can_sends.append(self.CCS.create_acc_hud_control(self.packer_pt, self.CAN.pt, acc_hud_status, set_speed,
                                                       lead_distance, hud_control.leadDistanceBars))

    # **** Stock ACC Button Controls **************************************** #

    gra_send_ready = self.CP.pcmCruise and CS.gra_stock_values["COUNTER"] != self.gra_acc_counter_last
    if gra_send_ready:
      if CC.cruiseControl.cancel or CC.cruiseControl.resume:
        can_sends.append(self.CCS.create_acc_buttons_control(self.packer_pt, self.CAN.ext, CS.gra_stock_values,
                                                             cancel=CC.cruiseControl.cancel, resume=CC.cruiseControl.resume))
      elif self.icbm_coordinator is None:
        can_sends.extend(IntelligentCruiseButtonManagementInterface.update(self, CS, CC_SP, self.packer_pt, self.CCS,
                                                                           self.CAN.ext, self.frame, self.gra_acc_counter_last))

    if self.icbm_coordinator is not None:
      firmware_status_valid = bool(getattr(CC_SP, "icbm_status_valid", False))
      firmware_signature_matched = bool(getattr(CC_SP, "icbm_signature_matched", False))
      firmware_protocol_valid = bool(getattr(CC_SP, "icbm_protocol_valid", False))
      firmware_panda_count = getattr(CC_SP, "icbm_panda_count", 0)
      firmware_request_id = getattr(CC_SP, "icbm_firmware_request_id", 0)
      firmware_state = getattr(CC_SP, "icbm_firmware_state", 0)
      firmware_status = {
        "valid": firmware_status_valid,
        "signature_matched": firmware_signature_matched,
        "protocol_valid": firmware_protocol_valid,
        "panda_count": firmware_panda_count,
        "request_id": firmware_request_id,
        "state": firmware_state,
        "button": getattr(CC_SP, "icbm_firmware_button", 0),
        "reason": getattr(CC_SP, "icbm_firmware_reason", 0),
        "tx_counter": getattr(CC_SP, "icbm_firmware_tx_counter", 0),
        "status_mono_time_ns": getattr(CC_SP, "icbm_firmware_status_mono_time_ns", 0),
      }
      paired_firmware = (firmware_status_valid and firmware_signature_matched
                         and firmware_protocol_valid and firmware_panda_count == 1)
      input_timestamp_ns = getattr(CC_SP, "icbm_input_timestamp_ns", 0)
      input_valid = getattr(CC_SP, "icbm_input_valid", False) is True
      now_s = now_nanos * 1e-9
      input_s = input_timestamp_ns * 1e-9 if type(input_timestamp_ns) is int and input_timestamp_ns > 0 else 0
      input_fresh = (input_valid and input_s > 0 and 0 <= now_s - input_s <= self.ICBM_INPUT_MAX_AGE_S)
      icbm = CC_SP.intelligentCruiseButtonManagement
      fb = CS.icbm_feedback_snapshot["ACC_GRA_Anzeige"]
      readiness_ts = CS.icbm_feedback_snapshot["readiness_timestamps"]
      feedback_ts_ns = fb["timestamp_nanos"]
      feedback_s = feedback_ts_ns * 1e-9 if type(feedback_ts_ns) is int and feedback_ts_ns > 0 else 0
      feedback_fresh = feedback_s > 0 and 0 <= now_s - feedback_s <= self.ICBM_FEEDBACK_MAX_AGE_S
      unit = "kph" if fb["ACA_kmh_mph"] == 0 else "mph" if fb["ACA_kmh_mph"] == 1 else "unknown"
      planner_unit = str(icbm.speedUnit)
      target = icbm.vTarget
      target_eligible = (icbm.sendButton in (structs.IntelligentCruiseButtonManagement.SendButtonState.increase,
                                             structs.IntelligentCruiseButtonManagement.SendButtonState.decrease)
                         and gra_send_ready
                         and ((target > fb["ACA_V_Wunsch"]) ==
                              (icbm.sendButton == structs.IntelligentCruiseButtonManagement.SendButtonState.increase)))
      readiness_fresh = all(type(ts) is int and ts > 0 and 0 <= now_nanos-ts <= int(self.ICBM_INPUT_MAX_AGE_S*1e9)
                            for ts in readiness_ts.values())
      radar = CS.icbm_feedback_snapshot["ACC_System"]
      radar_ts = radar["timestamp_nanos"]
      radar_fresh = (type(radar_ts) is int and radar_ts > 0
                     and 0 <= now_nanos-radar_ts <= int(self.ICBM_INPUT_MAX_AGE_S*1e9)
                     and radar["ACS_Sta_ADR"] == 1 and radar["ACS_FreigSollB"] == 1)
      manual_button_pressed = any(button.pressed for button in CS.out.buttonEvents)
      active = bool(CS.out.cruiseState.enabled and CS.out.cruiseState.available
                    and not CS.out.accFaulted and not CS.out.gasPressed and not CS.out.brakePressed
                    and not manual_button_pressed
                    and not CC.cruiseControl.cancel and not CC.cruiseControl.resume)
      feedback_speed = fb["ACA_V_Wunsch"]
      feedback = StockFeedback(feedback_speed, feedback_s, feedback_fresh)
      self.icbm_coordinator.update(
        now=now_s, target=target, speed_unit=planner_unit if planner_unit == unit else "unit_mismatch",
        ready=input_fresh and readiness_fresh and active, readiness_fresh=readiness_fresh,
        active=active, radar_fresh=radar_fresh, target_fresh=input_fresh,
        target_eligible=target_eligible, feedback=feedback, paired_firmware=paired_firmware,
        firmware_status=firmware_status)
      pending = self.icbm_coordinator.transaction.pending
      if pending is not None:
        status_owns_request = (firmware_status_valid and firmware_signature_matched and firmware_protocol_valid
                               and firmware_request_id == pending.sequence and firmware_state in (1, 2, 3, 4, 5, 6, 7))
        if not status_owns_request:
          button_id = {"UP": 1, "DOWN": 2, "SET": 3, "RES": 4}[pending.button]
          self.icbm_command = (pending.sequence, button_id)

    new_actuators = actuators.as_builder()
    new_actuators.torque = self.apply_torque_last / self.CCP.STEER_MAX
    new_actuators.torqueOutputCan = self.apply_torque_last

    self.gra_acc_counter_last = CS.gra_stock_values["COUNTER"]
    self.frame += 1
    return new_actuators, can_sends
