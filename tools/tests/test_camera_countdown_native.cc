#include <cassert>
#include <limits>
#include "selfdrive/ui/sunnypilot/camera_countdown.h"

int main() {
  using namespace camera_ui;
  Countdown countdown;
  for (int threshold = 50; threshold <= 1000; threshold += 50) {
    countdown.clear();
    assert(!countdown.update("a", "a", threshold + .01, threshold, true, true, false));
    assert(countdown.update("a", "a", threshold, threshold, true, true, false) == threshold);
    assert(countdown.update("a", "a", threshold - 10.1, threshold, true, true, false) == threshold - 10);
    assert(countdown.update("a", "a", threshold - 5, threshold, true, true, false) == threshold - 10);
    assert(countdown.update("a", "a", 0, threshold, true, true, false) == 0);
    assert(!countdown.update("a", "a", 0, threshold, true, true, true));
  }
  assert(!countdown.update("a", "other", 100, 300, true, true, false));
  assert(!countdown.update("", "", 100, 300, true, true, false));
  assert(!countdown.update("a", "a", 100, 300, true, false, false));
  assert(!countdown.update("a", "a", 100, 300, false, true, false));
  assert(!countdown.update("a", "a", -1, 300, true, true, false));
  assert(!countdown.update("a", "a", std::numeric_limits<double>::quiet_NaN(), 300, true, true, false));
  assert(!countdown.update("a", "a", std::numeric_limits<double>::infinity(), 300, true, true, false));
  assert(countdown.update("a", "a", 70, 300, true, true, false) == 70);
  assert(countdown.update("b", "b", 250, 300, true, true, false) == 250);
  assert(countdown.update("b", "b", 450, 500, true, true, false) == 450);
  for (int invalid : {0, 49, 51, 1001}) assert(reactionDistance(invalid) == 300);
  const Rect sign{280, 100, 100, 100}, offset{360, 90, 30, 30};
  const auto badge = oppositeOffset(sign, offset, 70, 30);
  assert(badge.x == 250 && badge.y == 90 && badge.width == 70 && badge.height == 30);
  assert(badge.x + badge.width / 2 < sign.x + sign.width / 2);
  // Mirroring also works if a future native layout places offset on the left.
  const auto right_badge = oppositeOffset(sign, {250, 90, 30, 30}, 70, 30);
  assert(right_badge.x + right_badge.width / 2 > sign.x + sign.width / 2);
}
