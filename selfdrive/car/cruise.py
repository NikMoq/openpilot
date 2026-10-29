import math
import numpy as np

from cereal import car
from openpilot.common.constants import CV
from openpilot.sunnypilot.selfdrive.car.cruise_ext import VCruiseHelperSP


# WARNING: this value was determined based on the model's training distribution,
#          model predictions above this speed can be unpredictable
# V_CRUISE's are in kph
V_CRUISE_MIN = 8
V_CRUISE_MAX = 145
V_CRUISE_UNSET = 255
V_CRUISE_INITIAL = 40
V_CRUISE_INITIAL_EXPERIMENTAL_MODE = 105
IMPERIAL_INCREMENT = round(CV.MPH_TO_KPH, 1)  # round here to avoid rounding errors incrementing set speed

ButtonEvent = car.CarState.ButtonEvent
ButtonType = car.CarState.ButtonEvent.Type
CRUISE_LONG_PRESS = 50
CRUISE_NEAREST_FUNC = {
  ButtonType.accelCruise: math.ceil,
  ButtonType.decelCruise: math.floor,
}
CRUISE_INTERVAL_SIGN = {
  ButtonType.accelCruise: +1,
  ButtonType.decelCruise: -1,
}


class VCruiseHelper(VCruiseHelperSP):
  def __init__(self, CP, CP_SP):
    VCruiseHelperSP.__init__(self, CP, CP_SP)
    self.CP = CP
    self.v_cruise_kph = V_CRUISE_UNSET
    self.v_cruise_cluster_kph = V_CRUISE_UNSET
    self.v_cruise_kph_last = 0
    self.button_timers = {ButtonType.decelCruise: 0, ButtonType.accelCruise: 0}
    self.button_change_states = {btn: {"standstill": False, "enabled": False, "camera": False} for btn in self.button_timers}

  @property
  def v_cruise_initialized(self):
    return self.v_cruise_kph != V_CRUISE_UNSET

  def update_v_cruise(self, CS, enabled, is_metric):
    was_engaged = self._camera_acc_engaged_prev
    engaged = bool(CS.cruiseState.enabled and not CS.brakePressed)
    self._camera_acc_engaged_prev = engaged
    if not engaged:
      self._camera_manual_follow = False
    self.v_cruise_kph_last = self.v_cruise_kph
    if 0 < self.v_cruise_kph <= V_CRUISE_MAX:
      self.driver_set_speed_kph = self.v_cruise_kph

    self.get_minimum_set_speed(is_metric)

    if CS.cruiseState.available:
      if self.remember_stock_cruise:
        self.update_stock_set_intent(CS, enabled)
        if self._stock_set_pending:
          self.update_enabled_state(CS, enabled)
          stock_kph = CS.cruiseState.speed * CV.MS_TO_KPH
          if (enabled and CS.cruiseState.enabled and not self._stock_set_held and
              math.isfinite(stock_kph) and 0 < stock_kph <= V_CRUISE_MAX):
            # Wait for released SET, stock engagement and valid native feedback;
            # the old stock value on the disabled press edge is not the new base.
            self.v_cruise_kph = stock_kph
            cluster_kph = CS.cruiseState.speedCluster * CV.MS_TO_KPH
            self.v_cruise_cluster_kph = cluster_kph if math.isfinite(cluster_kph) and cluster_kph > 0 else stock_kph
            self._stock_virtual_cruise_initialized = True
            self._stock_set_pending = False
          self.update_button_timers(CS, enabled)
          return
        if (not self._stock_virtual_cruise_initialized and not self._stock_set_held and
            enabled and CS.cruiseState.enabled):
          stock_kph = CS.cruiseState.speed * CV.MS_TO_KPH
          if math.isfinite(stock_kph) and 0 < stock_kph <= V_CRUISE_MAX:
            # RES can engage before native speed feedback arrives. Initialize
            # once it becomes valid, even after the first enabled edge; an
            # already remembered driver target never enters this path.
            self.v_cruise_kph = stock_kph
            cluster_kph = CS.cruiseState.speedCluster * CV.MS_TO_KPH
            self.v_cruise_cluster_kph = cluster_kph if math.isfinite(cluster_kph) and cluster_kph > 0 else stock_kph
            self._stock_virtual_cruise_initialized = True
            self.update_enabled_state(CS, enabled)
            self.update_button_timers(CS, enabled)
            return
      if self.remember_stock_cruise and self._stock_virtual_cruise_initialized:
        action = None
        released = None
        if not CS.buttonEvents:
          self._camera_release_seen.clear()
        for b in CS.buttonEvents:
          if b.type in (ButtonType.cancel, ButtonType.mainCruise):
            self._camera_manual_feedback_pending = False
            self._camera_manual_gesture = False
            self._camera_manual_follow = False
          if b.type not in (ButtonType.accelCruise, ButtonType.decelCruise, ButtonType.setCruise, ButtonType.resumeCruise):
            continue
          kind = b.type.raw
          if b.type == ButtonType.resumeCruise and b.pressed and kind not in self._camera_speed_buttons_held:
            self._camera_resume_session = not (was_engaged and engaged)
          session_resume = b.type == ButtonType.resumeCruise and (self._camera_resume_session or not (was_engaged and engaged))
          if b.pressed:
            if kind not in self._camera_speed_buttons_held:
              action = None if session_resume else b.type
            self._camera_speed_buttons_held.add(kind)
            self._camera_release_seen.discard(kind)
          else:
            if kind in self._camera_speed_buttons_held and not session_resume:
              released = kind
            if kind not in self._camera_speed_buttons_held and kind not in self._camera_release_seen:
              action = None if session_resume else b.type
            self._camera_speed_buttons_held.discard(kind)
            self._camera_release_seen.add(kind)
            if b.type == ButtonType.resumeCruise:
              self._camera_resume_session = False
        stock_kph = CS.cruiseState.speed * CV.MS_TO_KPH
        if action is not None and not self.camera_episode_active:
          self._camera_manual_follow = False
        if action is not None and (self.camera_episode_active or self._camera_manual_feedback_pending):
          self._camera_manual_feedback_pending = True
          self._camera_manual_feedback_baseline = stock_kph
          self._camera_manual_gesture_kind = action.raw
          self._camera_manual_gesture = action.raw in self._camera_speed_buttons_held
          self._camera_manual_changed_held = self._camera_manual_follow = False
          self._camera_manual_feedback_direction = 1 if action in (ButtonType.accelCruise, ButtonType.resumeCruise) else -1
        if released == self._camera_manual_gesture_kind and self._camera_manual_gesture:
          self._camera_manual_gesture = False
          self._camera_manual_feedback_pending = not self._camera_manual_changed_held
          self._camera_manual_follow = self._camera_manual_changed_held
        if self._camera_manual_feedback_pending or self._camera_manual_gesture or self._camera_manual_follow:
          # Preserve the driver's base until the attributed native response
          # changes. No unproved ECU latency timeout may replace it with the
          # old temporary ACC target. The planner marks this uncertainty as
          # manual so the unchanged ICBM guard pauses requests meanwhile.
          baseline = self._camera_manual_feedback_baseline
          if not math.isfinite(stock_kph) or not 0 < stock_kph <= V_CRUISE_MAX:
            self._camera_manual_feedback_pending = True
          if (math.isfinite(stock_kph) and 0 < stock_kph <= V_CRUISE_MAX and baseline is not None and
              math.isfinite(baseline) and baseline > 0 and
              abs(stock_kph - baseline) > .036 and
              (self._camera_manual_feedback_direction == 0 or
               (stock_kph - baseline) * self._camera_manual_feedback_direction > 0)):
            self.v_cruise_kph = self.driver_set_speed_kph = stock_kph
            cluster_kph = CS.cruiseState.speedCluster * CV.MS_TO_KPH
            self.v_cruise_cluster_kph = cluster_kph if math.isfinite(cluster_kph) and cluster_kph > 0 else stock_kph
            self._camera_manual_feedback_pending = False
            self._camera_manual_changed_held |= self._camera_manual_gesture
            self._camera_manual_feedback_baseline = stock_kph
          self.update_enabled_state(CS, enabled)
          self.update_button_timers(CS, enabled)
          return
      if self.remember_stock_cruise and self.camera_episode_active and self._stock_virtual_cruise_initialized:
        # Preserve the driver's base throughout a camera episode. Temporary
        # button corrections use the attributed native response above, not
        # virtual increments; pedal disengagement and RES keep the base.
        self.update_enabled_state(CS, enabled)
        self.update_button_timers(CS, enabled)
        return
      if self.camera_manual_takeover and not self.CP_SP.pcmCruiseSpeed:
        # Retain the existing stock handover outside a remembered camera episode.
        self.v_cruise_kph = CS.cruiseState.speed * CV.MS_TO_KPH
        self.v_cruise_cluster_kph = CS.cruiseState.speedCluster * CV.MS_TO_KPH
        self.update_button_timers(CS, enabled)
        return
      _enabled = self.update_enabled_state(CS, enabled)
      keep_virtual = self.remember_stock_cruise and self._stock_virtual_cruise_initialized
      if not self.CP.pcmCruise or (not self.CP_SP.pcmCruiseSpeed and (_enabled or keep_virtual)):
        # if stock cruise is completely disabled, then we can use our own set speed logic
        self._update_v_cruise_non_pcm(CS, _enabled, is_metric)
        self.update_speed_limit_assist_v_cruise_non_pcm()
        self.v_cruise_cluster_kph = self.v_cruise_kph
        self.update_button_timers(CS, enabled)
      else:
        self.v_cruise_kph = CS.cruiseState.speed * CV.MS_TO_KPH
        self.v_cruise_cluster_kph = CS.cruiseState.speedCluster * CV.MS_TO_KPH
        if CS.cruiseState.speed == 0:
          self.v_cruise_kph = V_CRUISE_UNSET
          self.v_cruise_cluster_kph = V_CRUISE_UNSET
        elif CS.cruiseState.speed == -1:
          self.v_cruise_kph = -1
          self.v_cruise_cluster_kph = -1
      if (self.remember_stock_cruise and enabled and CS.cruiseState.enabled and
          0 < self.v_cruise_kph <= V_CRUISE_MAX):
        # Once engaged, preserve the driver's target across pedal disengagement
        # and the guarded RES edge. This does not enable or resume stock ACC.
        self._stock_virtual_cruise_initialized = True
    else:
      self.v_cruise_kph = V_CRUISE_UNSET
      self.v_cruise_cluster_kph = V_CRUISE_UNSET
      if self.remember_stock_cruise:
        # Turning the main cruise switch off ends this remembered session.
        self._stock_virtual_cruise_initialized = False
        self.camera_episode_active = False
        self.enabled_prev = False
        self._stock_set_pending = self._stock_set_held = self._stock_set_from_camera = False

  def _update_v_cruise_non_pcm(self, CS, enabled, is_metric):
    # handle button presses. TODO: this should be in state_control, but a decelCruise press
    # would have the effect of both enabling and changing speed is checked after the state transition
    if not enabled:
      return

    long_press = False
    button_type = None

    v_cruise_delta = 1. if is_metric else IMPERIAL_INCREMENT

    for b in CS.buttonEvents:
      if b.type.raw in self.button_timers and not b.pressed:
        if self.button_timers[b.type.raw] > CRUISE_LONG_PRESS:
          return  # end long press
        button_type = b.type.raw
        break
    else:
      for k, timer in self.button_timers.items():
        if timer and timer % CRUISE_LONG_PRESS == 0:
          button_type = k
          long_press = True
          break

    if button_type is None:
      return

    # A press made during camera control remains a native correction even if
    # its release or long-press timer arrives after the camera has been passed.
    if self.button_change_states[button_type]["camera"]:
      return

    # Don't adjust speed when pressing resume to exit standstill
    cruise_standstill = self.button_change_states[button_type]["standstill"] or CS.cruiseState.standstill
    if button_type == ButtonType.accelCruise and cruise_standstill:
      return

    # Don't adjust speed if we've enabled since the button was depressed (some ports enable on rising edge)
    if not self.button_change_states[button_type]["enabled"]:
      return

    # Speed Limit Assist for Non PCM long cars.
    # True: Disallow set speed changes when user confirmed the target set speed during preActive state
    # False: Allow set speed changes as SLA is not requesting user confirmation
    if self.update_speed_limit_assist_pre_active_confirmed(button_type):
      return

    long_press, v_cruise_delta = VCruiseHelperSP.update_v_cruise_delta(self, long_press, v_cruise_delta)
    if long_press and self.v_cruise_kph % v_cruise_delta != 0:  # partial interval
      self.v_cruise_kph = CRUISE_NEAREST_FUNC[button_type](self.v_cruise_kph / v_cruise_delta) * v_cruise_delta
    else:
      self.v_cruise_kph += v_cruise_delta * CRUISE_INTERVAL_SIGN[button_type]

    # If set is pressed while overriding, clip cruise speed to minimum of vEgo
    if CS.gasPressed and button_type in (ButtonType.decelCruise, ButtonType.setCruise):
      self.v_cruise_kph = max(self.v_cruise_kph, CS.vEgo * CV.MS_TO_KPH)

    self.v_cruise_kph = np.clip(round(self.v_cruise_kph, 1), self.v_cruise_min, V_CRUISE_MAX)

  def update_button_timers(self, CS, enabled):
    # increment timer for buttons still pressed
    for k in self.button_timers:
      if self.button_timers[k] > 0:
        self.button_timers[k] += 1
        if self.remember_stock_cruise and self.camera_episode_active:
          self.button_change_states[k]["camera"] = True

    for b in CS.buttonEvents:
      if b.type.raw in self.button_timers:
        # Start/end timer and store current state on change of button pressed
        self.button_timers[b.type.raw] = 1 if b.pressed else 0
        camera = (self.remember_stock_cruise and self.camera_episode_active if b.pressed else
                  self.button_change_states[b.type.raw]["camera"])
        self.button_change_states[b.type.raw] = {"standstill": CS.cruiseState.standstill, "enabled": enabled, "camera": camera}

  def initialize_v_cruise(self, CS, experimental_mode: bool, dynamic_experimental_control: bool) -> None:
    # initializing is handled by the PCM
    if self.CP.pcmCruise:
      return

    initial_experimental_mode = experimental_mode and not dynamic_experimental_control
    initial = V_CRUISE_INITIAL_EXPERIMENTAL_MODE if initial_experimental_mode else V_CRUISE_INITIAL

    if any(b.type in (ButtonType.accelCruise, ButtonType.resumeCruise) for b in CS.buttonEvents) and self.v_cruise_initialized:
      self.v_cruise_kph = self.v_cruise_kph_last
    else:
      self.v_cruise_kph = int(round(np.clip(CS.vEgo * CV.MS_TO_KPH, initial, V_CRUISE_MAX)))

    self.v_cruise_cluster_kph = self.v_cruise_kph
