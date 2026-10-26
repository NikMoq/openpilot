import datetime

from openpilot.common.params import Params
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.list_view import button_item, multiple_button_item, text_item
from openpilot.system.ui.widgets.scroller import Scroller
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import Mode, OffsetType, Policy


class SpeedLimitLayout(Widget):
  """Speed-limit policy and speed-camera database controls in one panel."""

  def __init__(self):
    super().__init__()
    self._params = Params()
    self._mode_values = [Mode.off, Mode.information, Mode.warning, Mode.assist]
    self._policy_values = [Policy.car_state_only, Policy.map_data_only, Policy.car_state_priority,
                           Policy.map_data_priority, Policy.combined]
    self._offset_values = [OffsetType.off, OffsetType.fixed, OffsetType.percentage]
    self._offset_presets = [-5, 0, 5, 10]
    self._scroller = Scroller([
      multiple_button_item("Speed Limit", "Controls speed-limit display, warnings and Assist.",
                           ["Off", "Information", "Warning", "Assist"], self._selected("SpeedLimitMode", self._mode_values),
                           button_width=205, callback=self._set_mode),
      multiple_button_item("Customize Source", "Choose whether the car, map and camera database provide the limit.",
                           ["Car", "Map", "Car First", "Map First", "Combined"],
                           self._selected("SpeedLimitPolicy", self._policy_values),
                           button_width=165, callback=self._set_policy),
      multiple_button_item("Speed Limit Offset", "Apply no offset, a fixed offset or a percentage offset.",
                           ["None", "Fixed", "Percent"], self._selected("SpeedLimitOffsetType", self._offset_values),
                           button_width=210, callback=self._set_offset_type),
      multiple_button_item("Offset Value", "Used by Fixed and Percent modes.",
                           [str(value) for value in self._offset_presets],
                           self._selected_offset(), button_width=170, callback=self._set_offset_value),
      text_item("Camera database", self._camera_status),
      text_item("Last camera update", self._last_update),
      text_item("Camera database version", lambda: self._params.get("SpeedCameraDatabaseVersion") or "default"),
      button_item("Update camera database", "UPDATE NOW", description=self._update_description,
                  callback=self._request_update),
    ], line_separator=True, spacing=0)

  def _selected(self, key, values) -> int:
    try:
      value = int(self._params.get(key, return_default=True) or 0)
    except (TypeError, ValueError):
      value = 0
    return values.index(value) if value in values else 0

  def _selected_offset(self) -> int:
    try:
      value = int(self._params.get("SpeedLimitValueOffset", return_default=True) or 0)
    except (TypeError, ValueError):
      value = 0
    return self._offset_presets.index(value) if value in self._offset_presets else 1

  def _set_mode(self, index: int):
    self._params.put("SpeedLimitMode", int(self._mode_values[index]))

  def _set_policy(self, index: int):
    self._params.put("SpeedLimitPolicy", int(self._policy_values[index]))

  def _set_offset_type(self, index: int):
    self._params.put("SpeedLimitOffsetType", int(self._offset_values[index]))

  def _set_offset_value(self, index: int):
    self._params.put("SpeedLimitValueOffset", str(self._offset_presets[index]))

  def _camera_status(self) -> str:
    return self._params.get("SpeedCameraDatabaseStatus") or "not downloaded"

  def _last_update(self) -> str:
    raw = self._params.get("SpeedCameraDatabaseUpdatedAt") or "0"
    try:
      timestamp = float(raw)
      return "never" if timestamp <= 0 else datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OverflowError, OSError):
      return "unknown"

  def _update_description(self) -> str:
    error = self._params.get("SpeedCameraDatabaseError")
    return f"Last error: {error}" if error else "Downloads the configured camera database on the next mapd cycle."

  def _request_update(self):
    self._params.put("SpeedCameraDatabaseUpdatedAt", "0")
    self._params.put("SpeedCameraDatabaseStatus", "update requested")

  def _render(self, rect):
    self._scroller.render(rect)
