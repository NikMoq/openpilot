"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

from opendbc.car import DT_CTRL, structs
from opendbc.car.can_definitions import CanData
from opendbc.sunnypilot.car.intelligent_cruise_button_management_interface_base import IntelligentCruiseButtonManagementInterfaceBase

SendButtonState = structs.IntelligentCruiseButtonManagement.SendButtonState

BUTTON_STEP_INTERVAL = 0.1

BUTTONS = {
  SendButtonState.increase: "accel",
  SendButtonState.decrease: "decel",
}


class IntelligentCruiseButtonManagementInterface(IntelligentCruiseButtonManagementInterfaceBase):
  def __init__(self, CP, CP_SP):
    super().__init__(CP, CP_SP)

  def update(self, CS, CC_SP, packer, button_can, bus, frame, gra_acc_counter_last) -> list[CanData]:
    can_sends = []
    self.CC_SP = CC_SP
    self.ICBM = CC_SP.intelligentCruiseButtonManagement
    self.frame = frame

    if not self.CP.pcmCruise or self.ICBM.sendButton == SendButtonState.none:
      return can_sends

    if CS.gra_stock_values["COUNTER"] == gra_acc_counter_last:
      return can_sends

    # VW ACC buttons are counter-synchronized, so send one clean short-press
    # pulse after fresh stock GRA data arrives and let normal passthrough supply
    # the release frames in between pulses.
    if (self.frame - self.last_button_frame) * DT_CTRL > BUTTON_STEP_INTERVAL:
      button = BUTTONS[self.ICBM.sendButton]
      can_sends.append(button_can.create_acc_buttons_control(packer, bus, CS.gra_stock_values, **{button: True}))
      self.last_button_frame = self.frame

    return can_sends
