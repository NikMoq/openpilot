#pragma once

namespace camera_ui {
// A valid warning keeps its existing presentation when control has no sign.
// A selected control sign retains priority and must not be drawn twice.
inline bool drawWarning(bool camera_mode, bool awaiting_plan, bool control_sign, bool warning_visible) {
  return (!camera_mode && !awaiting_plan) || (camera_mode && !control_sign && warning_visible);
}

inline bool suppressNoCameraStatus(bool control_sign, bool warning_visible) {
  return !control_sign && warning_visible;
}
}  // namespace camera_ui
