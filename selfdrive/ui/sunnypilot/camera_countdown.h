#pragma once

// Authored independently for cam. This component is not yet linked to the
// shipped Qt executable: its matching renderer/build sources are unavailable.
#include <algorithm>
#include <cmath>
#include <optional>
#include <string>

namespace camera_ui {

constexpr int kDefaultReactionDistance = 300;
inline int reactionDistance(int metres) {
  return metres >= 50 && metres <= 1000 && metres % 50 == 0 ? metres : kDefaultReactionDistance;
}

struct Rect {
  double x, y, width, height;
};

// Anchor to the EXISTING sign and offset rectangles. Reflect the offset's
// centre across the sign centre; allow room for e.g. "1000 m". No sign or
// offset is drawn by this component. The caller must check viewport/collisions.
inline Rect oppositeOffset(const Rect &sign, const Rect &offset, double width, double height) {
  const double sign_centre = sign.x + sign.width / 2;
  const double offset_centre = offset.x + offset.width / 2;
  return {2 * sign_centre - offset_centre - width / 2,
          offset.y + (offset.height - height) / 2, width, height};
}

class Countdown {
public:
  // Freshness means the caller has checked message age, GPS/graph validity and
  // that sign_event_id is the SAME camera currently represented by the sign.
  // Missing/stale data immediately clears the badge. A camera event is never
  // attached to a car/OSM sign merely because its numerical limit is equal.
  std::optional<int> update(const std::string &event_id, const std::string &sign_event_id,
                            double distance_m, int configured_m, bool countdown_valid,
                            bool fresh, bool passed) {
    const int threshold = reactionDistance(configured_m);
    if (!fresh || !countdown_valid || passed || event_id.empty() || event_id != sign_event_id ||
        !std::isfinite(distance_m) || distance_m < 0 || distance_m > threshold) {
      clear();
      return std::nullopt;
    }
    const int measured = std::min(threshold, static_cast<int>(std::ceil(distance_m)));
    if (event_id != event_id_ || threshold != threshold_) {
      event_id_ = event_id;
      threshold_ = threshold;
      remaining_ = measured;
    } else {
      remaining_ = std::min(remaining_, measured);  // Suppress GPS increases.
    }
    return remaining_;
  }

  void clear() {
    event_id_.clear();
    threshold_ = 0;
    remaining_ = 0;
  }

private:
  std::string event_id_;
  int threshold_ = 0;
  int remaining_ = 0;
};

}  // namespace camera_ui
