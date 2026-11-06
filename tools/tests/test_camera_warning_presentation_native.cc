#include <cassert>
#include <QString>
#include "selfdrive/ui/sunnypilot/qt/onroad/camera_warning_presentation.h"

int main() {
  // These are the independent states produced by warning vs control publishers:
  // warning valid at 250m (known or unknown speed), control has no selected sign.
  const bool warning_at_250m = true;
  assert(camera_ui::drawWarning(true, false, false, warning_at_250m));
  assert(camera_ui::suppressNoCameraStatus(false, warning_at_250m));
  // No duplicate warning beside the selected control sign.
  assert(!camera_ui::drawWarning(true, false, true, true));
  assert(!camera_ui::suppressNoCameraStatus(true, true));
  // Expired/missing warning cannot be shown or hide the diagnostic.
  assert(!camera_ui::drawWarning(true, false, false, false));
  assert(!camera_ui::suppressNoCameraStatus(false, false));
  // Preserve ordinary warning mode and its planner-transition guard.
  assert(camera_ui::drawWarning(false, false, false, true));
  assert(!camera_ui::drawWarning(false, true, false, true));
  const QString reason = "no_camera:provider_has_events";
  assert(reason.section(':', 0, 0) == "no_camera");
}
